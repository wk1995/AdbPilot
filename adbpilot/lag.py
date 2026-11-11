"""Short, read-only ADB lag diagnostic sessions."""

from __future__ import annotations

import os
import platform
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import uuid4

from .client import AdbClient, is_tls_connect_serial, parse_devices
from .errors import AdbCommandError, AdbPilotError
from .lag_report import (
    SCHEMA_VERSION, FrameSnapshot, frame_result, milliseconds, parse_frame_stats, summarize_frames, summarize_probes,
)
from .models import CommandResult, Device


DEFAULT_DURATION = 30
MIN_DURATION = 10
MAX_DURATION = 120
INTERVAL = 3
BACKOFF_INTERVAL = 15
UNRESPONSIVE_TIMEOUTS = 2
NANOSECONDS_PER_SECOND = 1_000_000_000
PROBE_TIMEOUT = 2
FRAME_TIMEOUT = 4
STOP_MESSAGES = {
    "target_lost": "目标传输已断开、离线或被替换。请确认连接后重新采集。",
    "target_unauthorized": "目标设备授权失效。请重新授权后采集。",
    "adb_unavailable": "ADB 无法执行。请检查 ADB 路径和设备连接。",
    "no_probe": "采集窗口内未能发起 ADB 探针。请检查连接或增加采集时长。",
    "interrupted": "采集已中断。",
}


class SessionStopped(Exception):
    """The selected ADB transport can no longer be sampled."""

    def __init__(self, reason: str) -> None:
        super().__init__(STOP_MESSAGES[reason])
        self.reason = reason


def validate_inputs(package: str | None, label: str | None, mark_on_enter: bool) -> None:
    if package is not None and not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)*", package):
        raise AdbPilotError("请提供有效的 Android 应用包名。")
    if label is not None and (not label.strip() or len(label) > 80 or not label.isprintable()):
        raise AdbPilotError("场景标签需为 1–80 个可显示字符。")
    if mark_on_enter and not sys.stdin.isatty():
        raise AdbPilotError("--mark-on-enter 需要交互终端。")


def _devices(client: AdbClient, timeout: float = PROBE_TIMEOUT) -> list[Device]:
    return parse_devices(client.run(["devices", "-l"], timeout=timeout).stdout)


def _select_target(client: AdbClient, serial: str | None) -> tuple[Device, int]:
    try:
        devices = _devices(client)
    except (OSError, AdbCommandError, subprocess.TimeoutExpired) as exc:
        raise AdbPilotError("无法读取 ADB 传输列表。请检查 ADB 和设备连接。") from exc
    if serial is not None:
        matches = [device for device in devices if device.serial == serial]
        if len(matches) != 1 or not matches[0].is_ready:
            raise AdbPilotError("指定的 ADB 传输不存在、未就绪或不唯一。请检查 adb devices -l。")
    else:
        matches = [device for device in devices if device.is_ready]
        if len(matches) != 1:
            raise AdbPilotError("需要一条在线 ADB 传输。多条在线时，请用 --serial 指定 adb devices -l 中的完整标识。")
    target = matches[0]
    identifier = target.details.get("transport_id", "")
    if not re.fullmatch(r"[1-9][0-9]{0,19}", identifier):
        raise AdbPilotError("当前 ADB 未提供传输 ID。请更新 Android Platform-Tools。")
    return target, int(identifier)


def _transport_type(device: Device) -> str:
    if device.serial.startswith("emulator-"):
        return "emulator"
    if "usb" in device.details:
        return "usb"
    if is_tls_connect_serial(device.serial) or any(key in device.details for key in ("wifi", "wireless")):
        return "wifi"
    if re.search(r":[0-9]+$", device.serial) or any(key in device.details for key in ("tcp", "tcpip")):
        return "tcpip"
    return "unknown"


def _check_target(client: AdbClient, target: Device, timeout: float = PROBE_TIMEOUT) -> None:
    for device in _devices(client, timeout):
        if device.serial == target.serial and device.details.get("transport_id") == target.details["transport_id"]:
            if device.is_ready:
                return
            if device.state in {"unauthorized", "no permissions"}:
                raise SessionStopped("target_unauthorized")
    raise SessionStopped("target_lost")


def _check_command_target(result: CommandResult) -> None:
    if result.returncode == 0:
        return
    message = result.stderr.lower()
    if "device unauthorized" in message or "no permissions" in message:
        raise SessionStopped("target_unauthorized")
    if "device offline" in message or "no devices/emulators found" in message or re.search(
        r"\b(?:device|transport(?: id)?)\b.*\bnot found\b", message
    ):
        raise SessionStopped("target_lost")


def _metadata(client: AdbClient, target: Device, identifier: int) -> tuple[str | None, int | None]:
    version = None
    try:
        result = client.run(["version"], timeout=PROBE_TIMEOUT, check=False)
        match = re.search(r"^Version ([0-9]+(?:\.[0-9]+){2}(?:-[0-9A-Za-z.]+)?)\s*$", result.stdout, re.M)
        if result.returncode == 0 and match:
            version = match[1]
    except subprocess.TimeoutExpired:
        pass

    sdk = None
    try:
        _check_target(client, target)
        result = client.run(["shell", "-n", "getprop", "ro.build.version.sdk"], transport_id=identifier,
                            timeout=PROBE_TIMEOUT, check=False)
        _check_command_target(result)
        value = result.stdout.strip()
        if result.returncode == 0 and re.fullmatch(r"[1-9][0-9]{0,2}", value):
            sdk = int(value)
    except (AdbCommandError, subprocess.TimeoutExpired):
        pass
    return version, sdk


def _frames(client: AdbClient, target: Device, identifier: int, package: str) -> tuple[FrameSnapshot | None, str | None]:
    try:
        _check_target(client, target)
        result = client.run(["shell", "-n", "dumpsys", "gfxinfo", package, "framestats"], transport_id=identifier,
                            timeout=FRAME_TIMEOUT, check=False)
        _check_command_target(result)
    except subprocess.TimeoutExpired:
        return None, "frame_query_timeout"
    except AdbCommandError:
        return None, "frame_query_failed"
    if result.returncode != 0:
        return None, "frame_query_failed"
    snapshot = parse_frame_stats(result.stdout)
    return snapshot, "unsupported_frame_output" if snapshot is None else None


def _watch_enter(stop: threading.Event, marks: list[int]) -> None:
    if os.name == "nt":
        import msvcrt

        while not stop.wait(0.05):
            if msvcrt.kbhit() and msvcrt.getwch() == "\r":
                marks.append(time.monotonic_ns())
        return

    import select

    try:
        fd = sys.stdin.fileno()
        while not stop.is_set():
            if select.select([fd], [], [], 0.1)[0]:
                data = os.read(fd, 4096)
                if not data:
                    return
                marks.extend([time.monotonic_ns()] * data.count(b"\n"))
    except (OSError, ValueError):
        return


def _wait_until(deadline: int) -> None:
    time.sleep(max(0, (deadline - time.monotonic_ns()) / NANOSECONDS_PER_SECOND))


def _sample_probes(client: AdbClient, target: Device, identifier: int, start: int, duration: int,
                   samples: list[dict[str, Any]], on_unresponsive: Callable[[int], None]) -> None:
    last_probe = start - INTERVAL * NANOSECONDS_PER_SECOND
    consecutive_timeouts = 0
    backed_off = False
    for offset in range(0, duration, INTERVAL):
        planned = start + offset * NANOSECONDS_PER_SECOND
        slot_end = start + min(offset + INTERVAL, duration) * NANOSECONDS_PER_SECOND
        sample = {"planned_ms": offset * 1000, "started_ms": None, "elapsed_ms": None, "status": "skipped"}
        samples.append(sample)
        interval = BACKOFF_INTERVAL if backed_off else INTERVAL
        next_probe = last_probe + interval * NANOSECONDS_PER_SECOND
        skip_for_backoff = backed_off and next_probe >= slot_end
        _wait_until(planned if skip_for_backoff else min(slot_end, max(planned, next_probe)))
        remaining = (slot_end - time.monotonic_ns()) / NANOSECONDS_PER_SECOND
        if remaining <= 0:
            continue
        try:
            _check_target(client, target, min(PROBE_TIMEOUT, remaining))
        except (AdbCommandError, subprocess.TimeoutExpired):
            sample["skip_reason"] = "host_query_failed"
            consecutive_timeouts = 0
            continue
        if skip_for_backoff:
            sample["skip_reason"] = "backoff"
            continue

        probe_start = time.monotonic_ns()
        remaining = (slot_end - probe_start) / NANOSECONDS_PER_SECOND
        if remaining <= 0:
            continue
        last_probe = probe_start
        status = "failed"
        try:
            result = client.run(["shell", "-n", "true"], transport_id=identifier,
                                timeout=min(PROBE_TIMEOUT, remaining), check=False)
            _check_command_target(result)
            if result.returncode == 0:
                status = "ok"
        except subprocess.TimeoutExpired:
            status = "timeout"
        except KeyboardInterrupt:
            status = "skipped"
            raise
        finally:
            if status != "skipped":
                sample.update(status=status, started_ms=milliseconds(probe_start - start),
                              elapsed_ms=milliseconds(time.monotonic_ns() - probe_start))
        consecutive_timeouts = consecutive_timeouts + 1 if status == "timeout" else 0
        if not backed_off and consecutive_timeouts >= UNRESPONSIVE_TIMEOUTS:
            backed_off = True
            on_unresponsive(time.monotonic_ns())
    _wait_until(start + duration * NANOSECONDS_PER_SECOND)


def record_session(client: AdbClient, *, serial: str | None, package: str | None, label: str | None,
                   duration: int, mark_on_enter: bool,
                   on_checkpoint: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    target, identifier = _select_target(client, serial)
    samples: list[dict[str, Any]] = []
    marks: list[int] = []
    stop = threading.Event()
    watcher = None
    reason = None
    version = sdk = start = end = None
    first_unresponsive = None
    session_id = uuid4().hex
    started_at = datetime.now(timezone.utc).isoformat()
    frames = frame_result("unavailable", "capture_interrupted") if package else frame_result("not_requested")

    def build_report(status: str, finished: int) -> dict[str, Any]:
        report = {
            "schema_version": SCHEMA_VERSION,
            "session_id": session_id,
            "status": status,
            "stop_reason": reason,
            "context": {
                "started_at": started_at, "label": label, "package": package,
                "host_platform": f"{platform.system()}-{platform.machine()}",
                "adb_version": version, "android_sdk": sdk, "transport": _transport_type(target),
                "duration_seconds": duration, "interval_seconds": INTERVAL,
                "elapsed_ms": milliseconds(finished - start) if start is not None else 0,
            },
            "adb": {"samples": list(samples), **summarize_probes(samples)},
            "frames": frames,
            "marks": [milliseconds(mark - start) for mark in list(marks) if start is not None and start <= mark <= finished],
        }
        if first_unresponsive is not None:
            report["diagnosis"] = {
                "status": "device_unresponsive",
                "first_detected_ms": milliseconds(first_unresponsive - start),
                "backoff_seconds": BACKOFF_INTERVAL,
            }
        return report

    def note_unresponsive(detected: int) -> None:
        nonlocal first_unresponsive, frames
        first_unresponsive = detected
        if package:
            frames = frame_result("unavailable", "device_unresponsive")
        if on_checkpoint is not None:
            on_checkpoint(build_report("partial", detected))

    try:
        version, sdk = _metadata(client, target, identifier)
        before, frame_error = _frames(client, target, identifier, package) if package else (None, None)
        if frame_error:
            frames = frame_result("unavailable", frame_error)
        message = f"开始 {duration} 秒 ADB 采集，请在手机上复现卡顿。"
        if mark_on_enter:
            message += " 按 Enter 标记卡顿时刻。"
        print(message, file=sys.stderr, flush=True)
        started_at = datetime.now(timezone.utc).isoformat()
        start = time.monotonic_ns()
        if mark_on_enter:
            watcher = threading.Thread(target=_watch_enter, args=(stop, marks), daemon=True)
            watcher.start()
        _sample_probes(client, target, identifier, start, duration, samples, note_unresponsive)
        end = time.monotonic_ns()
        stop.set()
        if before is not None and package and first_unresponsive is None:
            after, frame_error = _frames(client, target, identifier, package)
            if after is None:
                frames = frame_result("unavailable", frame_error or "unsupported_frame_output")
            else:
                frames = summarize_frames(before, after)
        else:
            # Catch a disconnect after the final sample, even without frame collection.
            try:
                _check_target(client, target)
            except (AdbCommandError, subprocess.TimeoutExpired):
                pass
        if all(sample["status"] == "skipped" for sample in samples):
            reason = "no_probe"
    except SessionStopped as exc:
        reason = exc.reason
    except OSError:
        reason = "adb_unavailable"
    except KeyboardInterrupt:
        reason = "interrupted"
    finally:
        stop.set()
        if end is None:
            end = time.monotonic_ns()
        if watcher is not None and watcher.ident is not None:
            watcher.join(timeout=0.2)

    status = "complete" if reason is None else "partial" if start is not None else "failed"
    return build_report(status, end)
