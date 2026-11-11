"""Local report format and HWUI frame statistics for lag diagnostics."""

from __future__ import annotations

import json
import math
import os
import statistics
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import AdbPilotError


SCHEMA_VERSION = 1
MIN_SUCCESSFUL_PROBES = 5
FRAME_SOURCE = "gfxinfo_framestats"
PROBE_STATUSES = ("ok", "timeout", "failed", "skipped")
SKIP_REASONS = ("host_query_failed", "backoff")
UNRESPONSIVE_MESSAGE = "ADB 显示在线，设备命令无响应（连续超时）。"
FRAME_STATUSES = ("recent", "partial", "unavailable", "not_requested")
REPORT_LIMIT = 1024 * 1024


def milliseconds(nanoseconds: int) -> float:
    return round(nanoseconds / 1_000_000, 3)


def summarize_probes(samples: list[dict[str, Any]]) -> dict[str, Any]:
    counts = dict.fromkeys(PROBE_STATUSES, 0)
    skipped = dict.fromkeys(SKIP_REASONS, 0)
    elapsed = []
    for sample in samples:
        status = sample["status"]
        counts[status] += 1
        if status == "skipped" and sample.get("skip_reason") in skipped:
            skipped[sample["skip_reason"]] += 1
        if status == "ok":
            elapsed.append(sample["elapsed_ms"])
    return {
        "counts": counts,
        "skipped_by_reason": skipped,
        "median_ms": round(statistics.median(elapsed), 3) if elapsed else None,
        "max_ms": max(elapsed) if elapsed else None,
    }


@dataclass
class FrameSnapshot:
    windows: dict[tuple[str, str], dict[int, int]] = field(default_factory=dict)
    malformed: bool = False


def parse_frame_stats(output: str) -> FrameSnapshot | None:
    snapshot = FrameSnapshot()
    seen_graphics = False
    process = ""
    window = ""
    rows: list[str] | None = None
    for raw in output.splitlines():
        line = raw.strip()
        if line == "---PROFILEDATA---":
            seen_graphics = True
            if rows is None:
                rows = []
                continue
            key = (process, window)
            frames, malformed = _parse_frame_rows(rows)
            if key in snapshot.windows:
                # A duplicate window label in one process cannot identify a buffer.
                snapshot.windows[key] = {}
                malformed = True
            else:
                snapshot.windows[key] = frames
            snapshot.malformed |= malformed
            rows = None
        elif rows is not None:
            if line:
                rows.append(line)
        elif line.startswith("** Graphics info for pid "):
            seen_graphics = True
            process = line
            window = ""
        elif line.startswith("Window:"):
            window = line
    snapshot.malformed |= rows is not None
    return snapshot if seen_graphics else None


def _parse_frame_rows(rows: list[str]) -> tuple[dict[int, int], bool]:
    if not rows:
        return {}, True
    header = rows[0].split(",")
    required = ("Flags", "IntendedVsync", "FrameCompleted")
    if any(header.count(name) != 1 for name in required):
        return {}, True
    flag, start, end = (header.index(name) for name in required)
    frames = {}
    malformed = False
    for row in rows[1:]:
        values = row.split(",")
        try:
            if int(values[flag]) != 0:
                continue
            vsync, completed = int(values[start]), int(values[end])
        except (ValueError, IndexError):
            malformed = True
            continue
        if not 0 < vsync < completed < 2**63 - 1:
            malformed = True
            continue
        frames[vsync] = completed
    return frames, malformed


def frame_result(status: str, *reasons: str) -> dict[str, Any]:
    return {
        "source": FRAME_SOURCE,
        "status": status,
        "reasons": list(reasons),
        "window_count": 0,
        "frame_count": 0,
        "span_ms": None,
        "median_ms": None,
        "max_ms": None,
    }


def summarize_frames(before: FrameSnapshot, after: FrameSnapshot) -> dict[str, Any]:
    if before.malformed:
        return frame_result("unavailable", "invalid_frame_data")
    reasons = set()
    if after.malformed:
        reasons.add("invalid_frame_data")
    if before.windows.keys() != after.windows.keys():
        reasons.add("windows_changed")

    samples = []
    window_count = 0
    for key, frames in after.windows.items():
        if key not in before.windows or not frames:
            continue
        previous = before.windows[key]
        if previous and not (previous.keys() & frames.keys()):
            reasons.add("buffer_discontinuity")
        newest_baseline = max(previous, default=0)
        new_frames = [(start, end) for start, end in frames.items() if start > newest_baseline]
        if new_frames:
            window_count += 1
            samples.extend(new_frames)

    if not samples:
        return frame_result("unavailable", *(sorted(reasons) or ["no_new_frames"]))
    result = frame_result("partial" if reasons else "recent", *sorted(reasons))
    durations = [end - start for start, end in samples]
    starts = [start for start, _ in samples]
    result.update(
        window_count=window_count,
        frame_count=len(samples),
        span_ms=milliseconds(max(starts) - min(starts)),
        median_ms=round(statistics.median(durations) / 1_000_000, 3),
        max_ms=milliseconds(max(durations)),
    )
    return result


def write_report(path: Path, report: dict[str, Any]) -> None:
    """Publish a complete file in the destination directory without overwriting."""
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as output:
                temporary = Path(output.name)
                json.dump(report, output, ensure_ascii=False, indent=2, allow_nan=False)
                output.write("\n")
            if os.name == "nt":
                # Unlike POSIX rename, this fails if the destination already exists.
                temporary.rename(path)
            else:
                os.link(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    except FileExistsError as exc:
        raise AdbPilotError(f"报告文件已存在，请选择新路径：{path}") from exc
    except OSError as exc:
        raise AdbPilotError(f"保存诊断报告失败：{exc}") from exc


def _nonnegative_number(value: Any) -> bool:
    return type(value) in (int, float) and 0 <= value <= 2**63 and math.isfinite(value)


def load_report(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as source:
            data = source.read(REPORT_LIMIT + 1)
        if len(data) > REPORT_LIMIT:
            raise ValueError("文件超过诊断报告大小限制")
        report = json.loads(data.decode("utf-8"))
        _validate_report(report)
    except (OSError, ValueError, KeyError, TypeError, RecursionError) as exc:
        raise AdbPilotError(f"无法读取诊断报告 {path}：{exc}") from exc
    return report


def _validate_report(report: Any) -> None:
    if not isinstance(report, dict) or type(report.get("schema_version")) is not int or report["schema_version"] != SCHEMA_VERSION:
        raise ValueError("不支持的诊断报告版本")
    if report["status"] not in ("complete", "partial", "failed"):
        raise ValueError("未知的会话状态")
    diagnosis = report.get("diagnosis")
    if diagnosis is not None and (
        diagnosis["status"] != "device_unresponsive"
        or not _nonnegative_number(diagnosis["first_detected_ms"])
        or not _nonnegative_number(diagnosis["backoff_seconds"])
        or diagnosis["backoff_seconds"] == 0
    ):
        raise ValueError("诊断状态无效")
    context = report["context"]
    for field_name in ("host_platform", "adb_version", "transport", "label", "package"):
        value = context[field_name]
        if value is not None and (not isinstance(value, str) or not value.isprintable()):
            raise ValueError(f"无效的上下文字段 {field_name}")
    sdk = context["android_sdk"]
    if sdk is not None and (type(sdk) is not int or sdk <= 0):
        raise ValueError("无效的 Android SDK")
    for field_name in ("duration_seconds", "interval_seconds", "elapsed_ms"):
        if not _nonnegative_number(context[field_name]):
            raise ValueError(f"无效的采集时长字段 {field_name}")
    if context["duration_seconds"] == 0 or context["interval_seconds"] == 0:
        raise ValueError("采集时长与间隔必须大于零")

    samples = report["adb"]["samples"]
    if not isinstance(samples, list):
        raise ValueError("ADB 样本格式无效")
    for sample in samples:
        if sample["status"] not in PROBE_STATUSES or not _nonnegative_number(sample["planned_ms"]):
            raise ValueError("ADB 样本状态或时间无效")
        if "skip_reason" in sample and (sample["status"] != "skipped" or sample["skip_reason"] not in SKIP_REASONS):
            raise ValueError("ADB 样本跳过原因无效")
        for field_name in ("started_ms", "elapsed_ms"):
            value = sample[field_name]
            if (sample["status"] == "skipped" and value is not None) or (
                sample["status"] != "skipped" and not _nonnegative_number(value)
            ):
                raise ValueError("ADB 样本耗时无效")

    frames = report["frames"]
    if frames["source"] != FRAME_SOURCE or frames["status"] not in FRAME_STATUSES:
        raise ValueError("帧数据来源或状态无效")
    for field_name in ("frame_count", "window_count"):
        if type(frames[field_name]) is not int or frames[field_name] < 0:
            raise ValueError("帧数据计数无效")
    for field_name in ("span_ms", "median_ms", "max_ms"):
        value = frames[field_name]
        if (frames["frame_count"] == 0 and value is not None) or (
            frames["frame_count"] > 0 and not _nonnegative_number(value)
        ):
            raise ValueError("帧数据耗时无效")
    if not isinstance(frames["reasons"], list) or not all(isinstance(reason, str) for reason in frames["reasons"]):
        raise ValueError("帧数据原因格式无效")
    if not isinstance(report["marks"], list) or not all(_nonnegative_number(mark) for mark in report["marks"]):
        raise ValueError("卡顿标记格式无效")


def compare_reports(baseline: dict[str, Any], current: dict[str, Any], *, same_device: bool) -> dict[str, Any]:
    summaries = [summarize_probes(report["adb"]["samples"]) for report in (baseline, current)]
    reasons = []
    if not same_device:
        reasons.append("未确认是同一台手机")
    if baseline["status"] != "complete" or current["status"] != "complete":
        reasons.append("至少一份采集未完整结束")
    if any(report.get("diagnosis") for report in (baseline, current)):
        reasons.append("至少一份报告因设备命令无响应而降频")
    if any(summary["counts"]["ok"] < MIN_SUCCESSFUL_PROBES for summary in summaries):
        reasons.append(f"至少一份报告的成功样本少于 {MIN_SUCCESSFUL_PROBES} 条")
    for key, label in (
        ("duration_seconds", "计划采集时长"), ("interval_seconds", "采样间隔"),
        ("host_platform", "主机平台"), ("adb_version", "ADB 版本"), ("android_sdk", "Android SDK"),
    ):
        left, right = baseline["context"][key], current["context"][key]
        if left is None or right is None or left != right:
            reasons.append(f"{label}不同或未知")

    delta = None
    if not reasons:
        delta = {
            key: round(summaries[1][key] - summaries[0][key], 3)
            for key in ("median_ms", "max_ms")
        }
    result = {"schema_version": SCHEMA_VERSION, "delta_ms": delta, "comparison_limits": reasons}
    for name, report, summary in zip(("baseline", "current"), (baseline, current), summaries):
        result[name] = {
            "status": report["status"], "context": report["context"],
            "adb": summary, "frames": report["frames"], "marks": report["marks"],
        }
        if report.get("diagnosis"):
            result[name]["diagnosis"] = report["diagnosis"]
    return result
