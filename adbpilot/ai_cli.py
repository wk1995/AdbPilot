"""Machine-readable CLI for AI and automation callers."""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from . import __version__
from .client import AdbClient
from .errors import AdbPilotError
from .models import Device, DeviceInfo, RunningProcess


JsonMap = dict[str, Any]
Operation = Callable[[AdbClient, JsonMap], Any]


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "schema":
        write_json(operation_schema(), pretty=args.pretty)
        return 0

    if args.command == "run":
        try:
            request = read_request(args.request)
            operation = str(request.get("operation", ""))
            params = request.get("params", {})
            if not isinstance(params, dict):
                raise ValueError("request.params must be an object")
            return run_operation(args, operation, params)
        except Exception as exc:  # noqa: BLE001 - response must stay machine-readable.
            write_error(exc, pretty=args.pretty)
            return exit_code_for_error(exc)

    if args.command in OPERATIONS:
        try:
            return run_operation(args, args.command, parse_params(args.params))
        except Exception as exc:  # noqa: BLE001 - response must stay machine-readable.
            write_error(exc, pretty=args.pretty)
            return exit_code_for_error(exc)

    parser.print_help()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="adbpilot-ai",
        description="AdbPilot JSON CLI for AI agents and automation",
    )
    parser.add_argument("--adb-path", help="Path to adb executable. Also supports ADBPILOT_ADB.")
    parser.add_argument("--timeout", type=int, default=30, help="Default adb timeout in seconds.")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")
    parser.add_argument("--version", action="version", version=f"adbpilot-ai {__version__}")

    subparsers = parser.add_subparsers(dest="command")

    schema = subparsers.add_parser("schema", help="Print operation schema")
    add_pretty_arg(schema)
    schema.set_defaults(command="schema")

    run = subparsers.add_parser("run", help="Run a JSON request from argument, file, or stdin")
    add_pretty_arg(run)
    run.add_argument(
        "request",
        help="JSON object, path to JSON file, or '-' for stdin. Format: {\"operation\":\"devices\",\"params\":{}}",
    )
    run.set_defaults(command="run")

    for name, spec in operation_schema()["operations"].items():
        command = subparsers.add_parser(name, help=spec["description"])
        add_pretty_arg(command)
        command.add_argument("--params", default="{}", help="JSON object with operation parameters")
        command.set_defaults(command=name)

    return parser


def add_pretty_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--pretty", action="store_true", default=argparse.SUPPRESS, help="Pretty-print JSON output.")


def run_operation(args: argparse.Namespace, operation: str, params: JsonMap) -> int:
    if operation not in OPERATIONS:
        raise ValueError(f"unknown operation: {operation}")

    client = AdbClient(adb_path=args.adb_path, timeout=args.timeout)
    data = OPERATIONS[operation](client, params)
    write_json({"ok": True, "operation": operation, "data": data}, pretty=args.pretty)
    return 0


def read_request(value: str) -> JsonMap:
    if value == "-":
        raw = sys.stdin.read()
    else:
        path = Path(value)
        raw = path.read_text(encoding="utf-8") if path.exists() else value

    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("request must be a JSON object")
    return data


def parse_params(raw: str) -> JsonMap:
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("--params must be a JSON object")
    return data


def write_json(data: JsonMap, *, pretty: bool = False) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2 if pretty else None, sort_keys=pretty))


def write_error(exc: Exception, *, pretty: bool = False) -> None:
    write_json(
        {
            "ok": False,
            "error": {
                "type": exc.__class__.__name__,
                "message": str(exc),
            },
        },
        pretty=pretty,
    )


def exit_code_for_error(exc: Exception) -> int:
    if isinstance(exc, KeyboardInterrupt):
        return 130
    if isinstance(exc, (AdbPilotError, subprocess.TimeoutExpired, TimeoutError, ValueError, OSError)):
        return 2
    return 1


def operation_schema() -> JsonMap:
    return {
        "version": 1,
        "program": "adbpilot-ai",
        "request_format": {
            "operation": "string",
            "params": "object",
        },
        "response_format": {
            "ok": "boolean",
            "operation": "string on success",
            "data": "operation-specific value on success",
            "error": "object with type and message on failure",
        },
        "operations": {
            "version": {"description": "Get adb version", "params": {}},
            "restart-server": {"description": "Restart adb server", "params": {}},
            "devices": {"description": "List connected devices", "params": {}},
            "info": {"description": "Get device info", "params": {"serial": "optional string"}},
            "connect": {"description": "Connect wireless adb device", "params": {"address": "host:port string"}},
            "disconnect": {"description": "Disconnect wireless adb device", "params": {"address": "optional host:port string"}},
            "install": {
                "description": "Install APK",
                "params": {"apk": "path string", "serial": "optional string", "replace": "bool", "downgrade": "bool", "grant": "bool"},
            },
            "uninstall": {
                "description": "Uninstall package",
                "params": {"package": "string", "serial": "optional string", "keep_data": "bool"},
            },
            "start": {"description": "Start app", "params": {"package": "string", "serial": "optional string", "activity": "optional string"}},
            "stop": {"description": "Stop app", "params": {"package": "string", "serial": "optional string"}},
            "clear": {"description": "Clear app data", "params": {"package": "string", "serial": "optional string"}},
            "apps": {
                "description": "List packages",
                "params": {"serial": "optional string", "system": "bool", "third_party": "bool", "filter": "optional string"},
            },
            "app-info": {"description": "Get package details", "params": {"package": "string", "serial": "optional string"}},
            "foreground": {"description": "Get current foreground app package", "params": {"serial": "optional string"}},
            "processes": {
                "description": "List running processes",
                "params": {"serial": "optional string", "packages_only": "bool"},
            },
            "export-apk": {
                "description": "Export APK files",
                "params": {"package": "string", "output_dir": "directory path string", "serial": "optional string"},
            },
            "push": {"description": "Push local file or directory", "params": {"local": "path string", "remote": "device path string", "serial": "optional string"}},
            "pull": {"description": "Pull remote file or directory", "params": {"remote": "device path string", "local": "path string", "serial": "optional string"}},
            "screencap": {"description": "Save screenshot", "params": {"output": "path string", "serial": "optional string"}},
            "screenrecord": {"description": "Save screen recording", "params": {"output": "path string", "seconds": "int", "serial": "optional string"}},
            "bugreport": {"description": "Export bugreport", "params": {"output": "path string", "serial": "optional string"}},
            "tap": {"description": "Input tap", "params": {"x": "int", "y": "int", "serial": "optional string"}},
            "swipe": {
                "description": "Input swipe",
                "params": {"x1": "int", "y1": "int", "x2": "int", "y2": "int", "duration_ms": "optional int", "serial": "optional string"},
            },
            "text": {"description": "Input text", "params": {"text": "string", "serial": "optional string"}},
            "key": {"description": "Input keyevent", "params": {"key": "string or int", "serial": "optional string"}},
            "shell": {"description": "Run adb shell command", "params": {"command": "string or string array", "serial": "optional string"}},
            "logcat-dump": {
                "description": "Read current logcat buffer",
                "params": {"serial": "optional string", "package": "optional string", "tag": "optional string", "level": "optional V/D/I/W/E/F/S"},
            },
            "logcat-save": {
                "description": "Save current logcat buffer",
                "params": {"output": "path string", "serial": "optional string", "package": "optional string", "tag": "optional string", "level": "optional V/D/I/W/E/F/S"},
            },
            "logcat-clear": {"description": "Clear device logcat buffer", "params": {"serial": "optional string"}},
        },
    }


def op_version(client: AdbClient, params: JsonMap) -> JsonMap:
    return {"version": client.version()}


def op_restart_server(client: AdbClient, params: JsonMap) -> JsonMap:
    client.restart_server()
    return {"message": "adb server restarted"}


def op_devices(client: AdbClient, params: JsonMap) -> list[JsonMap]:
    return [device_to_dict(device) for device in client.devices()]


def op_info(client: AdbClient, params: JsonMap) -> JsonMap:
    return device_info_to_dict(client.device_info(optional_str(params, "serial")))


def op_connect(client: AdbClient, params: JsonMap) -> JsonMap:
    return {"message": client.connect(required_str(params, "address"))}


def op_disconnect(client: AdbClient, params: JsonMap) -> JsonMap:
    return {"message": client.disconnect(optional_str(params, "address"))}


def op_install(client: AdbClient, params: JsonMap) -> JsonMap:
    return {
        "message": client.install(
            required_str(params, "apk"),
            optional_str(params, "serial"),
            replace=bool(params.get("replace", True)),
            downgrade=bool(params.get("downgrade", False)),
            grant=bool(params.get("grant", False)),
        )
    }


def op_uninstall(client: AdbClient, params: JsonMap) -> JsonMap:
    return {
        "message": client.uninstall(
            required_str(params, "package"),
            optional_str(params, "serial"),
            keep_data=bool(params.get("keep_data", False)),
        )
    }


def op_start(client: AdbClient, params: JsonMap) -> JsonMap:
    return {
        "message": client.start_app(
            required_str(params, "package"),
            optional_str(params, "serial"),
            optional_str(params, "activity"),
        )
    }


def op_stop(client: AdbClient, params: JsonMap) -> JsonMap:
    return {"message": client.stop_app(required_str(params, "package"), optional_str(params, "serial"))}


def op_clear(client: AdbClient, params: JsonMap) -> JsonMap:
    return {"message": client.clear_app(required_str(params, "package"), optional_str(params, "serial"))}


def op_apps(client: AdbClient, params: JsonMap) -> list[str]:
    return client.list_packages(
        optional_str(params, "serial"),
        system=bool(params.get("system", False)),
        third_party=bool(params.get("third_party", False)),
        filter_text=optional_str(params, "filter"),
    )


def op_app_info(client: AdbClient, params: JsonMap) -> JsonMap:
    return client.app_info(required_str(params, "package"), optional_str(params, "serial"))


def op_foreground(client: AdbClient, params: JsonMap) -> JsonMap:
    return {"package": client.foreground_package(optional_str(params, "serial"))}


def op_processes(client: AdbClient, params: JsonMap) -> list[JsonMap]:
    processes = client.running_processes(optional_str(params, "serial"))
    if bool(params.get("packages_only", False)):
        processes = [process for process in processes if process.package]
    return [process_to_dict(process) for process in processes]


def op_export_apk(client: AdbClient, params: JsonMap) -> JsonMap:
    paths = client.export_apk(
        required_str(params, "package"),
        Path(required_str(params, "output_dir")),
        optional_str(params, "serial"),
    )
    return {"paths": [str(path) for path in paths]}


def op_push(client: AdbClient, params: JsonMap) -> JsonMap:
    return {"message": client.push(required_str(params, "local"), required_str(params, "remote"), optional_str(params, "serial"))}


def op_pull(client: AdbClient, params: JsonMap) -> JsonMap:
    return {"message": client.pull(required_str(params, "remote"), required_str(params, "local"), optional_str(params, "serial"))}


def op_screencap(client: AdbClient, params: JsonMap) -> JsonMap:
    path = client.screencap(Path(required_str(params, "output")), optional_str(params, "serial"))
    return {"path": str(path)}


def op_screenrecord(client: AdbClient, params: JsonMap) -> JsonMap:
    path = client.screenrecord(
        Path(required_str(params, "output")),
        optional_str(params, "serial"),
        seconds=int(params.get("seconds", 10)),
    )
    return {"path": str(path)}


def op_bugreport(client: AdbClient, params: JsonMap) -> JsonMap:
    path = client.bugreport(Path(required_str(params, "output")), optional_str(params, "serial"))
    return {"path": str(path)}


def op_tap(client: AdbClient, params: JsonMap) -> JsonMap:
    return {"message": client.input_tap(int(params["x"]), int(params["y"]), optional_str(params, "serial"))}


def op_swipe(client: AdbClient, params: JsonMap) -> JsonMap:
    duration = params.get("duration_ms")
    return {
        "message": client.input_swipe(
            int(params["x1"]),
            int(params["y1"]),
            int(params["x2"]),
            int(params["y2"]),
            optional_str(params, "serial"),
            duration_ms=int(duration) if duration is not None else None,
        )
    }


def op_text(client: AdbClient, params: JsonMap) -> JsonMap:
    return {"message": client.input_text(required_str(params, "text"), optional_str(params, "serial"))}


def op_key(client: AdbClient, params: JsonMap) -> JsonMap:
    return {"message": client.input_keyevent(str(params["key"]), optional_str(params, "serial"))}


def op_shell(client: AdbClient, params: JsonMap) -> JsonMap:
    command = params.get("command")
    if isinstance(command, str):
        argv = shlex.split(command)
    elif isinstance(command, list) and all(isinstance(item, str) for item in command):
        argv = command
    else:
        raise ValueError("shell command must be a string or string array")
    return {"stdout": client.shell(argv, optional_str(params, "serial"))}


def op_logcat_dump(client: AdbClient, params: JsonMap) -> JsonMap:
    return {"stdout": logcat_dump(client, params)}


def op_logcat_save(client: AdbClient, params: JsonMap) -> JsonMap:
    output = Path(required_str(params, "output"))
    text = logcat_dump(client, params)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text, encoding="utf-8", errors="replace")
    return {"path": str(output), "bytes": output.stat().st_size}


def op_logcat_clear(client: AdbClient, params: JsonMap) -> JsonMap:
    serial = client.require_serial(optional_str(params, "serial"))
    client.run(["logcat", "-c"], serial=serial)
    return {"message": "logcat cleared"}


def logcat_dump(client: AdbClient, params: JsonMap) -> str:
    serial = client.require_serial(optional_str(params, "serial"))
    args = ["logcat", "-d"]
    package = optional_str(params, "package")
    tag = optional_str(params, "tag")
    level = optional_str(params, "level")

    if package:
        pid = client.run(["shell", "pidof", package], serial=serial, check=False).stdout.strip().split()
        if pid:
            args.extend(["--pid", pid[0]])
    if tag and level:
        args.extend([f"{tag}:{level}", "*:S"])
    elif tag:
        args.extend([f"{tag}:V", "*:S"])
    elif level:
        args.append(f"*:{level}")
    return client.run(args, serial=serial, timeout=120).stdout


def required_str(params: JsonMap, key: str) -> str:
    value = params.get(key)
    if value is None or value == "":
        raise ValueError(f"missing required parameter: {key}")
    return str(value)


def optional_str(params: JsonMap, key: str) -> str | None:
    value = params.get(key)
    if value is None or value == "":
        return None
    return str(value)


def device_to_dict(device: Device) -> JsonMap:
    return {"serial": device.serial, "state": device.state, "details": dict(device.details)}


def device_info_to_dict(info: DeviceInfo) -> JsonMap:
    return {
        "serial": info.serial,
        "state": info.state,
        "android_version": info.android_version,
        "sdk": info.sdk,
        "brand": info.brand,
        "model": info.model,
        "product": info.product,
        "abi": info.abi,
        "battery_level": info.battery_level,
        "battery_status": info.battery_status,
    }


def process_to_dict(process: RunningProcess) -> JsonMap:
    return {
        "pid": process.pid,
        "user": process.user,
        "name": process.name,
        "package": process.package,
    }


OPERATIONS: dict[str, Operation] = {
    "version": op_version,
    "restart-server": op_restart_server,
    "devices": op_devices,
    "info": op_info,
    "connect": op_connect,
    "disconnect": op_disconnect,
    "install": op_install,
    "uninstall": op_uninstall,
    "start": op_start,
    "stop": op_stop,
    "clear": op_clear,
    "apps": op_apps,
    "app-info": op_app_info,
    "foreground": op_foreground,
    "processes": op_processes,
    "export-apk": op_export_apk,
    "push": op_push,
    "pull": op_pull,
    "screencap": op_screencap,
    "screenrecord": op_screenrecord,
    "bugreport": op_bugreport,
    "tap": op_tap,
    "swipe": op_swipe,
    "text": op_text,
    "key": op_key,
    "shell": op_shell,
    "logcat-dump": op_logcat_dump,
    "logcat-save": op_logcat_save,
    "logcat-clear": op_logcat_clear,
}


if __name__ == "__main__":
    raise SystemExit(main())
