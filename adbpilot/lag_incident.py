"""Bounded, two-transport freeze recording and read-only USB recovery checks."""

from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from . import lag
from .client import AdbClient, parse_devices
from .errors import AdbCommandError, AdbPilotError
from .lag_report import milliseconds, write_report
from .models import Device


DURATION = 300
SECOND = lag.NANOSECONDS_PER_SECOND
SYSTEM_TAGS = {
    "SYSTEM_BOOT", "SYSTEM_RESTART", "SYSTEM_LAST_KMSG", "system_server_watchdog",
    "system_server_crash", "system_server_anr", "system_server_native_crash",
}
TRANSPORT_STATES = {"device", "offline", "unauthorized", "no permissions", "recovery", "sideload", "bootloader"}
Event = Callable[[str, str], None]


@dataclass(frozen=True)
class Incident:
    directory: Path
    session_id: str
    started_at: datetime
    hardware_serial: str = field(repr=False)
    initial_boot_id: str | None = field(repr=False)


def _inventory(client: AdbClient, timeout: float = lag.PROBE_TIMEOUT) -> tuple[str, list[Device]]:
    try:
        result = client.run(["devices", "-l"], timeout=timeout)
    except subprocess.TimeoutExpired:
        return "timeout", []
    except (AdbCommandError, OSError):
        return "failed", []
    if re.search(r"daemon not running|server version .*killing", result.stderr, re.I):
        return "server_restarted", parse_devices(result.stdout)
    if not result.stdout.lstrip().startswith("List of devices attached"):
        return "invalid_response", []
    return "ok", parse_devices(result.stdout)


def _state(target: Device, devices: list[Device]) -> str:
    matches = [device for device in devices if device.serial == target.serial]
    if not matches:
        return "not_listed"
    if len(matches) != 1 or matches[0].details.get("transport_id") != target.details.get("transport_id"):
        return "transport_changed"
    return matches[0].state if matches[0].state in TRANSPORT_STATES else "unknown"


def _read(client: AdbClient, target: Device, args: list[str], *, timeout: float = lag.PROBE_TIMEOUT) -> tuple[str, str]:
    try:
        result = client.run(["shell", "-n", *args], transport_id=int(target.details["transport_id"]), timeout=timeout)
    except subprocess.TimeoutExpired:
        return "timeout", ""
    except (AdbCommandError, OSError):
        return "failed", ""
    return "ok", result.stdout.strip()


def _checked_read(client: AdbClient, target: Device, args: list[str], *, timeout: float = lag.PROBE_TIMEOUT) -> tuple[str, str]:
    host, devices = _inventory(client)
    if host != "ok" or _state(target, devices) != "device":
        return "transport_unverified", ""
    return _read(client, target, args, timeout=timeout)


def _identity(client: AdbClient, target: Device) -> str:
    status, value = _checked_read(client, target, ["getprop", "ro.serialno"])
    if status != "ok" or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", value) or value.lower() == "unknown":
        raise AdbPilotError("无法核对设备身份。请在手机响应正常、连接在线时重试。")
    return value


def _boot_id(value: str) -> str | None:
    return value.lower() if re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", value) else None


def record_incident(
    client: AdbClient, directory: Path, *, usb: Device, wifi: Device, label: str | None,
    control: lag.RecordingControl, on_event: Event, duration: int = DURATION,
) -> Incident:
    lag.validate_inputs(None, label, False)
    if not 1 <= duration <= DURATION:
        raise AdbPilotError(f"联合采集最多持续 {DURATION} 秒。")
    if lag._transport_type(usb) != "usb" or lag._transport_type(wifi) not in {"wifi", "tcpip"}:
        raise AdbPilotError("请选择一条 USB 和一条无线 ADB 传输。")
    if any(not re.fullmatch(r"[1-9][0-9]{0,19}", target.details.get("transport_id", "")) for target in (usb, wifi)):
        raise AdbPilotError("所选连接缺少 ADB 传输 ID。请刷新设备列表。")
    if usb.details["transport_id"] == wifi.details["transport_id"]:
        raise AdbPilotError("请选择两条不同的 ADB 传输。")
    lag._check_cancelled(control.cancelled)
    hardware_serial = _identity(client, usb)
    lag._check_cancelled(control.cancelled)
    if _identity(client, wifi) != hardware_serial:
        raise AdbPilotError("USB 和 Wi-Fi 连接属于不同设备，请重新选择。")
    lag._check_cancelled(control.cancelled)
    boot_args = ["cat", "/proc/sys/kernel/random/boot_id"]
    _, usb_boot = _checked_read(client, usb, boot_args)
    lag._check_cancelled(control.cancelled)
    _, wifi_boot = _checked_read(client, wifi, boot_args)
    boot_ids = {_boot_id(usb_boot), _boot_id(wifi_boot)} - {None}
    if len(boot_ids) > 1:
        raise AdbPilotError("两条连接的启动周期不一致。请确认是同一台手机并重试。")
    lag._check_cancelled(control.cancelled)

    session_id = uuid4().hex
    folder = directory / f"adbpilot-incident-{session_id[:12]}"
    try:
        folder.mkdir()
    except OSError as exc:
        raise AdbPilotError(f"无法创建诊断目录：{exc}") from exc
    incident = Incident(folder, session_id, datetime.now(timezone.utc), hardware_serial, next(iter(boot_ids), None))
    timeline_path = folder / "timeline.json"
    start = time.monotonic_ns()
    deadline = start + duration * SECOND
    timeline: list[dict[str, Any]] = []
    targets = {"usb": usb, "wifi": wifi}
    active = set(targets)
    aliases: dict[tuple[str, str], str] = {}
    last_ok: dict[str, float | None] = dict.fromkeys(targets)
    first_timeout: dict[str, float | None] = dict.fromkeys(targets)
    timeout_streak = dict.fromkeys(targets, 0)
    fault: dict[str, Any] | None = None
    next_label = "usb"
    last_probe = start - lag.BACKOFF_INTERVAL * SECOND

    def report(status: str) -> dict[str, Any]:
        now = time.monotonic_ns()
        return {
            "schema_version": 1, "kind": "dual_transport_incident", "session_id": session_id,
            "started_at": incident.started_at.isoformat(), "status": status, "scene": label,
            "elapsed_ms": milliseconds(now - start), "fault": fault, "timeline": timeline,
            "marks_ms": control.marks_between(start, now),
        }

    write_report(timeline_path, report("recording"))
    on_event("timeline_saved", str(timeline_path))
    control.set_recording(True)
    on_event("started", "")
    status = "stopped"
    try:
        due = start
        while due < deadline and not control.cancelled.is_set():
            lag._wait_until(due, control.cancelled)
            now = time.monotonic_ns()
            if now >= deadline:
                break
            host, devices = _inventory(client, min(lag.PROBE_TIMEOUT, (deadline - now) / SECOND))
            states = {name: _state(target, devices) for name, target in targets.items()} if host in {
                "ok", "server_restarted",
            } else dict.fromkeys(targets, "unknown")
            if host != "ok":
                active.clear()
            else:
                active.intersection_update(name for name, state in states.items() if state == "device")
            extra = {}
            for device in devices:
                if device.serial in {usb.serial, wifi.serial} or lag._transport_type(device) not in {"wifi", "tcpip"}:
                    continue
                key = (device.serial, device.details.get("transport_id", ""))
                alias = aliases.setdefault(key, f"unverified_wireless_{len(aliases) + 1}")
                extra[alias] = device.state if device.state in TRANSPORT_STATES else "unknown"
            entry: dict[str, Any] = {
                "at_ms": milliseconds(now - start), "host": host, "transports": states,
                "eligible": sorted(active), "unverified_wireless": extra,
            }
            timeline.append(entry)
            probe_start = time.monotonic_ns()
            remaining = (deadline - probe_start) / SECOND
            if active and remaining > 0 and not control.cancelled.is_set() and (
                fault is None or probe_start - last_probe >= lag.BACKOFF_INTERVAL * SECOND
            ):
                name = next_label if next_label in active else next(iter(active))
                next_label = "wifi" if name == "usb" else "usb"
                last_probe = probe_start
                outcome, _ = _read(client, targets[name], ["true"], timeout=min(lag.PROBE_TIMEOUT, remaining))
                finished = time.monotonic_ns()
                entry["probe"] = {
                    "transport": name, "status": outcome, "started_ms": milliseconds(probe_start - start),
                    "elapsed_ms": milliseconds(finished - probe_start),
                }
                if outcome == "timeout":
                    timeout_streak[name] += 1
                    if timeout_streak[name] == 1:
                        first_timeout[name] = milliseconds(probe_start - start)
                else:
                    timeout_streak[name] = 0
                    first_timeout[name] = None
                    if outcome == "ok":
                        last_ok[name] = milliseconds(finished - start)
                both_timed_out = all(other in active and timeout_streak[other] for other in targets)
                if fault is None and (both_timed_out or timeout_streak[name] >= lag.UNRESPONSIVE_TIMEOUTS):
                    fault = {
                        "detected_ms": milliseconds(finished - start), "last_success_ms": dict(last_ok),
                        "first_timeout_ms": dict(first_timeout), "backoff_seconds": lag.BACKOFF_INTERVAL,
                        "transports": sorted(targets) if both_timed_out else [name],
                    }
                    snapshot = folder / "timeout.json"
                    write_report(snapshot, report("recording"))
                    on_event("checkpoint_saved", str(snapshot))
                    on_event("unresponsive", "both" if both_timed_out else name)

            write_report(timeline_path, report("recording"), replace=True)
            due += lag.INTERVAL * SECOND
            while due <= time.monotonic_ns():
                due += lag.INTERVAL * SECOND
        if not control.cancelled.is_set():
            lag._wait_until(deadline, control.cancelled)
            status = "complete"
    except lag.SessionStopped:
        pass
    finally:
        control.set_recording(False)
        write_report(timeline_path, report(status), replace=True)
    return incident


def collect_recovery(client: AdbClient, incident: Incident, usb: Device) -> tuple[Path, dict[str, Any]]:
    if lag._transport_type(usb) != "usb" or not re.fullmatch(r"[1-9][0-9]{0,19}", usb.details.get("transport_id", "")):
        raise AdbPilotError("请选择恢复后的在线 USB 连接。")
    if _identity(client, usb) != incident.hardware_serial:
        raise AdbPilotError("这条 USB 连接与诊断会话中的手机身份不匹配。")

    checks: dict[str, str] = {}
    lost = False

    def read(key: str, args: list[str], *, timeout: float = lag.PROBE_TIMEOUT) -> str:
        nonlocal lost
        if lost:
            checks[key] = "transport_unverified"
            return ""
        status, value = _checked_read(client, usb, args, timeout=timeout)
        checks[key] = status
        lost = status == "transport_unverified"
        return value

    boot = _boot_id(read("boot_id", ["cat", "/proc/sys/kernel/random/boot_id"]))
    if boot is None and checks["boot_id"] == "ok":
        checks["boot_id"] = "unavailable"
    boot_cycle = "unknown"
    if boot is not None and incident.initial_boot_id is not None:
        boot_cycle = "same_boot" if boot == incident.initial_boot_id else "new_boot"
    uptime = re.match(r"^([0-9]{1,12}(?:\.[0-9]{1,6})?)\s", read("uptime", ["cat", "/proc/uptime"]))
    if uptime is None and checks["uptime"] == "ok":
        checks["uptime"] = "unavailable"
    boot_reasons = {}
    for prop in ("sys.boot.reason", "ro.boot.bootreason"):
        value = read(prop, ["getprop", prop])
        boot_reasons[prop] = value if re.fullmatch(r"[A-Za-z0-9_,.:-]{1,100}", value) else None
        if boot_reasons[prop] is None and checks[prop] == "ok":
            checks[prop] = "unavailable"

    clock = re.fullmatch(r"([0-9]{9,12}),([+-])([0-9]{2})([0-9]{2})", read("clock", ["date", "+%s,%z"]))
    now = datetime.now(timezone.utc)
    offset = None
    if clock and abs(int(clock[1]) - now.timestamp()) <= 120 and int(clock[3]) < 24 and int(clock[4]) < 60:
        minutes = 60 * int(clock[3]) + int(clock[4])
        offset = timezone(timedelta(minutes=minutes if clock[2] == "+" else -minutes))
    if offset is None and checks["clock"] == "ok":
        checks["clock"] = "unaligned"
    dropbox = read("dropbox", ["dumpsys", "dropbox"], timeout=4)
    entries = []
    window_start = incident.started_at - timedelta(minutes=5)
    window_end = min(now + timedelta(minutes=2), incident.started_at + timedelta(minutes=20))
    for line in dropbox.splitlines():
        match = re.match(r"^\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+(\S+)", line)
        if not match or match[2] not in SYSTEM_TAGS:
            continue
        if offset is not None:
            try:
                stamp = datetime.fromisoformat(match[1]).replace(tzinfo=offset)
            except ValueError:
                continue
            if not window_start <= stamp <= window_end:
                continue
        entries.append({"device_local_time": match[1], "tag": match[2]})
    if checks["dropbox"] == "ok" and not re.search(r"Drop box contents:|^\s*\d{4}-\d{2}-\d{2} ", dropbox, re.M | re.I):
        checks["dropbox"] = "unavailable"

    report = {
        "schema_version": 1, "kind": "usb_recovery", "session_id": incident.session_id,
        "collected_at": now.isoformat(), "boot_cycle": boot_cycle, "boot_reasons": boot_reasons,
        "uptime_seconds": float(uptime[1]) if uptime else None, "checks": checks,
        "dropbox_scope": "incident_window" if offset is not None else "latest_entries_unaligned",
        "dropbox_entries": entries[-20:],
    }
    output = incident.directory / f"recovery-{uuid4().hex[:8]}.json"
    write_report(output, report)
    return output, report
