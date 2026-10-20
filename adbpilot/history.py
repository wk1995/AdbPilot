"""Persistent connection history and diagnostic log.

Records live under ~/.adbpilot/ by default:

- connections.json: one row per remembered device, newest activity first
- adbpilot.log: append-only events for connect, disconnect, pair, and device changes

Pairing codes and QR secrets are never stored. A broken history file is
renamed aside instead of blocking the current connection.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any


MAX_EVENTS = 200
MAX_LOG_BYTES = 1_000_000
SCHEMA_VERSION = 1

_LOCK = threading.Lock()


def data_dir() -> Path:
    override = os.environ.get("ADBPILOT_HOME", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".adbpilot"


def history_path() -> Path:
    return data_dir() / "connections.json"


def log_path() -> Path:
    return data_dir() / "adbpilot.log"


def _safe_text(value: str) -> str:
    text = _single_line(value)
    lowered = text.lower()
    if "pairing_code" in lowered or "password=" in lowered:
        return "[已省略敏感内容]"
    return text


def record_event(
    event: str,
    *,
    source: str,
    ok: bool,
    address: str = "",
    serial: str = "",
    state: str = "",
    model: str = "",
    message: str = "",
    detail: str = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Append one diagnostic line and update the matching device row."""

    timestamp = (now or datetime.now().astimezone()).isoformat(timespec="seconds")
    entry = {
        "time": timestamp,
        "event": event,
        "source": source,
        "ok": ok,
        "address": address,
        "serial": serial,
        "state": state,
        "model": model,
        "message": _safe_text(message),
        "detail": _safe_text(detail),
    }
    with _LOCK:
        _append_log(entry)
        if _should_update_device(entry):
            _update_device(entry)
    return entry


def load_devices() -> list[dict[str, Any]]:
    path = history_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    devices = data.get("devices") if isinstance(data, dict) else None
    if not isinstance(devices, list):
        return []
    return [item for item in devices if isinstance(item, dict)]


def recent_events(limit: int = 80) -> list[str]:
    path = log_path()
    if not path.exists() or limit <= 0:
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return lines[-limit:]


def format_device_row(device: dict[str, Any]) -> str:
    name = str(device.get("model") or device.get("serial") or device.get("address") or "未知设备")
    state = str(device.get("last_state") or "未知")
    address = str(device.get("address") or "-")
    when = str(device.get("last_seen") or "")
    result = str(device.get("last_result") or "")
    return f"{when}  {name}  {state}  {address}  {result}".rstrip()


def _should_update_device(entry: dict[str, Any]) -> bool:
    return bool(entry["serial"] or entry["address"] or entry["event"] in {"connect", "disconnect", "pair", "seen", "lost"})


def _update_device(entry: dict[str, Any]) -> None:
    devices = load_devices()
    index = _find_device(devices, str(entry["serial"]), str(entry["address"]))
    if index is None:
        row: dict[str, Any] = {
            "serial": entry["serial"],
            "address": entry["address"],
            "model": entry["model"],
            "connection": _connection_kind(str(entry["serial"]), str(entry["address"])),
            "first_seen": entry["time"],
            "last_seen": entry["time"],
            "last_state": entry["state"] or ("device" if entry["ok"] else "failed"),
            "last_event": entry["event"],
            "last_source": entry["source"],
            "last_result": entry["message"],
            "success_count": 1 if entry["ok"] else 0,
            "failure_count": 0 if entry["ok"] else 1,
            "events": [],
        }
        devices.insert(0, row)
    else:
        row = devices.pop(index)
        devices.insert(0, row)
        if entry["serial"]:
            row["serial"] = entry["serial"]
        if entry["address"]:
            row["address"] = entry["address"]
        if entry["model"]:
            row["model"] = entry["model"]
        row["connection"] = _connection_kind(str(row.get("serial", "")), str(row.get("address", "")))
        row["last_seen"] = entry["time"]
        row["last_state"] = entry["state"] or row.get("last_state", "")
        row["last_event"] = entry["event"]
        row["last_source"] = entry["source"]
        row["last_result"] = entry["message"]
        if entry["ok"]:
            row["success_count"] = int(row.get("success_count", 0)) + 1
        else:
            row["failure_count"] = int(row.get("failure_count", 0)) + 1

    events = row.setdefault("events", [])
    if isinstance(events, list):
        events.append(
            {
                "time": entry["time"],
                "event": entry["event"],
                "source": entry["source"],
                "ok": entry["ok"],
                "state": entry["state"],
                "message": entry["message"],
            }
        )
        del events[:-MAX_EVENTS]

    _write_history(devices)


def _find_device(devices: list[dict[str, Any]], serial: str, address: str) -> int | None:
    if serial:
        for index, device in enumerate(devices):
            if str(device.get("serial", "")) == serial:
                return index
    if address:
        for index, device in enumerate(devices):
            if str(device.get("address", "")) == address and not str(device.get("serial", "")):
                return index
    return None


def _write_history(devices: list[dict[str, Any]]) -> None:
    path = history_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": SCHEMA_VERSION, "devices": devices}
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _append_log(entry: dict[str, Any]) -> None:
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    _rotate_log(path)
    result = "ok" if entry["ok"] else "fail"
    fields = [
        entry["time"],
        entry["event"],
        entry["source"],
        result,
    ]
    if entry["serial"]:
        fields.append(f"serial={entry['serial']}")
    if entry["address"]:
        fields.append(f"address={entry['address']}")
    if entry["state"]:
        fields.append(f"state={entry['state']}")
    if entry["model"]:
        fields.append(f"model={entry['model']}")
    if entry["message"]:
        fields.append(entry["message"])
    if entry["detail"]:
        fields.append(entry["detail"])
    with path.open("a", encoding="utf-8") as handle:
        handle.write(" ".join(fields) + "\n")


def _rotate_log(path: Path) -> None:
    try:
        if path.exists() and path.stat().st_size > MAX_LOG_BYTES:
            backup = path.with_suffix(".log.1")
            if backup.exists():
                backup.unlink()
            path.replace(backup)
    except OSError:
        return


def _connection_kind(serial: str, address: str) -> str:
    lowered = serial.lower()
    if ":" in serial or ":" in address or "_adb-tls-" in lowered or "._adb" in lowered:
        return "Wi-Fi"
    if serial:
        return "USB"
    return ""


def _single_line(value: str) -> str:
    return " ".join(value.replace("\r", " ").split())
