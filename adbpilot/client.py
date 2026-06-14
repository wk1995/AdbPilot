"""ADB command wrapper and high-level operations."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import BinaryIO, Iterable, TextIO

from .errors import AdbCommandError, DeviceSelectionError, suggestion_for_device_state
from .models import CommandResult, Device, DeviceInfo
from .platform_adapter import PlatformAdapter


DEFAULT_TIMEOUT = 30


class AdbClient:
    """Small, testable wrapper around the adb executable."""

    def __init__(self, adb_path: str | None = None, timeout: int = DEFAULT_TIMEOUT) -> None:
        self.adb_path = PlatformAdapter().resolve_adb(adb_path)
        self.timeout = timeout

    def run(
        self,
        args: Iterable[str],
        *,
        serial: str | None = None,
        timeout: int | None = None,
        check: bool = True,
    ) -> CommandResult:
        command = self._build_command(args, serial=serial)
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout or self.timeout,
        )
        result = CommandResult(tuple(command), completed.returncode, completed.stdout, completed.stderr)
        if check and completed.returncode != 0:
            raise AdbCommandError(
                self._format_failure(command, completed.returncode, completed.stderr),
                completed.returncode,
                completed.stdout,
                completed.stderr,
            )
        return result

    def run_binary(
        self,
        args: Iterable[str],
        output: BinaryIO,
        *,
        serial: str | None = None,
        timeout: int | None = None,
    ) -> int:
        command = self._build_command(args, serial=serial)
        completed = subprocess.run(
            command,
            stdout=output,
            stderr=subprocess.PIPE,
            timeout=timeout or self.timeout,
        )
        if completed.returncode != 0:
            stderr = completed.stderr.decode("utf-8", errors="replace")
            raise AdbCommandError(
                self._format_failure(command, completed.returncode, stderr),
                completed.returncode,
                "",
                stderr,
            )
        return completed.returncode

    def stream(
        self,
        args: Iterable[str],
        *,
        serial: str | None = None,
        output: TextIO | None = None,
    ) -> int:
        command = self._build_command(args, serial=serial)
        with subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        ) as proc:
            assert proc.stdout is not None
            for line in proc.stdout:
                if output:
                    output.write(line)
                    output.flush()
                else:
                    print(line, end="")

            _, stderr = proc.communicate()
            if proc.returncode:
                raise AdbCommandError(
                    self._format_failure(command, proc.returncode, stderr),
                    proc.returncode,
                    "",
                    stderr,
                )
            return proc.returncode

    def version(self) -> str:
        return self.run(["version"]).stdout.strip()

    def restart_server(self) -> None:
        self.run(["kill-server"], check=False)
        self.run(["start-server"])

    def devices(self) -> list[Device]:
        result = self.run(["devices", "-l"])
        return parse_devices(result.stdout)

    def require_serial(self, serial: str | None = None) -> str:
        if serial:
            return serial

        ready_devices = [device for device in self.devices() if device.is_ready]
        if not ready_devices:
            all_devices = self.devices()
            if all_devices:
                states = ", ".join(
                    f"{device.serial}={device.state}（{suggestion_for_device_state(device.state)}）"
                    for device in all_devices
                )
                raise DeviceSelectionError(f"没有可用设备。当前状态：{states}")
            raise DeviceSelectionError("没有检测到设备。请连接设备或使用 adbpilot connect 建立无线连接。")

        if len(ready_devices) > 1:
            serials = ", ".join(device.serial for device in ready_devices)
            raise DeviceSelectionError(f"检测到多台设备：{serials}。请使用 --serial 指定目标设备。")

        return ready_devices[0].serial

    def device_info(self, serial: str | None = None) -> DeviceInfo:
        target = self.require_serial(serial)
        devices_by_serial = {device.serial: device for device in self.devices()}
        device = devices_by_serial.get(target, Device(target, "unknown"))

        def prop(name: str) -> str:
            return self.run(["shell", "getprop", name], serial=target).stdout.strip()

        battery = parse_key_value_lines(
            self.run(["shell", "dumpsys", "battery"], serial=target, timeout=10).stdout
        )
        return DeviceInfo(
            serial=target,
            state=device.state,
            android_version=prop("ro.build.version.release"),
            sdk=prop("ro.build.version.sdk"),
            brand=prop("ro.product.brand"),
            model=prop("ro.product.model"),
            product=prop("ro.product.name"),
            abi=prop("ro.product.cpu.abi"),
            battery_level=battery.get("level", ""),
            battery_status=battery.get("status", ""),
        )

    def install(self, apk: str, serial: str | None = None, *, replace: bool = True, downgrade: bool = False, grant: bool = False) -> str:
        target = self.require_serial(serial)
        args = ["install"]
        if replace:
            args.append("-r")
        if downgrade:
            args.append("-d")
        if grant:
            args.append("-g")
        args.append(apk)
        return self.run(args, serial=target, timeout=300).stdout.strip()

    def uninstall(self, package: str, serial: str | None = None, *, keep_data: bool = False) -> str:
        target = self.require_serial(serial)
        args = ["uninstall"]
        if keep_data:
            args.append("-k")
        args.append(package)
        return self.run(args, serial=target, timeout=120).stdout.strip()

    def start_app(self, package: str, serial: str | None = None, activity: str | None = None) -> str:
        target = self.require_serial(serial)
        if activity:
            return self.run(["shell", "am", "start", "-n", activity], serial=target).stdout.strip()
        return self.run(
            ["shell", "monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1"],
            serial=target,
        ).stdout.strip()

    def stop_app(self, package: str, serial: str | None = None) -> str:
        target = self.require_serial(serial)
        return self.run(["shell", "am", "force-stop", package], serial=target).stdout.strip()

    def clear_app(self, package: str, serial: str | None = None) -> str:
        target = self.require_serial(serial)
        return self.run(["shell", "pm", "clear", package], serial=target).stdout.strip()

    def list_packages(
        self,
        serial: str | None = None,
        *,
        system: bool = False,
        third_party: bool = False,
        filter_text: str | None = None,
    ) -> list[str]:
        target = self.require_serial(serial)
        args = ["shell", "pm", "list", "packages"]
        if system:
            args.append("-s")
        if third_party:
            args.append("-3")
        if filter_text:
            args.append(filter_text)
        output = self.run(args, serial=target).stdout
        packages = []
        for line in output.splitlines():
            line = line.strip()
            if line.startswith("package:"):
                packages.append(line.removeprefix("package:"))
        return packages

    def app_info(self, package: str, serial: str | None = None) -> dict[str, str]:
        target = self.require_serial(serial)
        output = self.run(["shell", "dumpsys", "package", package], serial=target, timeout=30).stdout
        return parse_package_dump(output)

    def export_apk(self, package: str, output_dir: Path, serial: str | None = None) -> list[Path]:
        target = self.require_serial(serial)
        output_dir.mkdir(parents=True, exist_ok=True)
        result = self.run(["shell", "pm", "path", package], serial=target).stdout
        remote_paths = [
            line.removeprefix("package:").strip()
            for line in result.splitlines()
            if line.strip().startswith("package:")
        ]
        if not remote_paths:
            raise DeviceSelectionError(f"未找到应用 APK 路径：{package}")

        pulled: list[Path] = []
        for index, remote_path in enumerate(remote_paths):
            suffix = "" if len(remote_paths) == 1 else f"-{index + 1}"
            local_path = output_dir / f"{package}{suffix}.apk"
            self.pull(remote_path, str(local_path), target)
            pulled.append(local_path)
        return pulled

    def push(self, local: str, remote: str, serial: str | None = None) -> str:
        target = self.require_serial(serial)
        return self.run(["push", local, remote], serial=target, timeout=600).stdout.strip()

    def pull(self, remote: str, local: str, serial: str | None = None) -> str:
        target = self.require_serial(serial)
        return self.run(["pull", remote, local], serial=target, timeout=600).stdout.strip()

    def screencap(self, output_path: Path, serial: str | None = None) -> Path:
        target = self.require_serial(serial)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("wb") as output:
            self.run_binary(["exec-out", "screencap", "-p"], output, serial=target, timeout=60)
        return output_path

    def screenrecord(self, output_path: Path, serial: str | None = None, *, seconds: int = 10) -> Path:
        target = self.require_serial(serial)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        remote_path = f"/sdcard/adbpilot-screenrecord-{int(time.time())}.mp4"
        self.run(["shell", "screenrecord", "--time-limit", str(seconds), remote_path], serial=target, timeout=seconds + 30)
        self.pull(remote_path, str(output_path), target)
        self.run(["shell", "rm", "-f", remote_path], serial=target, check=False)
        return output_path

    def bugreport(self, output_path: Path, serial: str | None = None) -> Path:
        target = self.require_serial(serial)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        self.run(["bugreport", str(output_path)], serial=target, timeout=1200)
        return output_path

    def input_tap(self, x: int, y: int, serial: str | None = None) -> str:
        target = self.require_serial(serial)
        return self.run(["shell", "input", "tap", str(x), str(y)], serial=target).stdout.strip()

    def input_swipe(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        serial: str | None = None,
        *,
        duration_ms: int | None = None,
    ) -> str:
        target = self.require_serial(serial)
        args = ["shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2)]
        if duration_ms is not None:
            args.append(str(duration_ms))
        return self.run(args, serial=target).stdout.strip()

    def input_text(self, text: str, serial: str | None = None) -> str:
        target = self.require_serial(serial)
        escaped = text.replace(" ", "%s")
        return self.run(["shell", "input", "text", escaped], serial=target).stdout.strip()

    def input_keyevent(self, key: str, serial: str | None = None) -> str:
        target = self.require_serial(serial)
        return self.run(["shell", "input", "keyevent", key], serial=target).stdout.strip()

    def shell(self, command: list[str], serial: str | None = None) -> str:
        target = self.require_serial(serial)
        return self.run(["shell", *command], serial=target, timeout=120).stdout

    def connect(self, address: str) -> str:
        return self.run(["connect", address], timeout=60).stdout.strip()

    def disconnect(self, address: str | None = None) -> str:
        args = ["disconnect"]
        if address:
            args.append(address)
        return self.run(args, timeout=60).stdout.strip()

    def logcat(
        self,
        serial: str | None = None,
        *,
        output: TextIO | None = None,
        package: str | None = None,
        tag: str | None = None,
        level: str | None = None,
    ) -> int:
        target = self.require_serial(serial)
        args = ["logcat"]
        if package:
            pid = self.run(["shell", "pidof", package], serial=target, check=False).stdout.strip().split()
            if not pid:
                raise DeviceSelectionError(f"未找到正在运行的进程：{package}")
            args.extend(["--pid", pid[0]])
        if tag and level:
            args.extend([f"{tag}:{level}", "*:S"])
        elif tag:
            args.extend([f"{tag}:V", "*:S"])
        elif level:
            args.append(f"*:{level}")
        return self.stream(args, serial=target, output=output)

    def _build_command(self, args: Iterable[str], *, serial: str | None = None) -> list[str]:
        command = [str(self.adb_path)]
        if serial:
            command.extend(["-s", serial])
        command.extend(str(arg) for arg in args)
        return command

    @staticmethod
    def _format_failure(command: list[str], returncode: int, stderr: str) -> str:
        rendered = " ".join(command)
        detail = stderr.strip() or "无 stderr 输出"
        return f"ADB 命令执行失败，退出码 {returncode}: {rendered}\n{detail}"


def parse_devices(output: str) -> list[Device]:
    devices: list[Device] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("List of devices attached"):
            continue

        parts = line.split()
        if len(parts) < 2:
            continue

        serial, state = parts[0], parts[1]
        detail_start = 2
        if len(parts) >= 3 and parts[1] == "no" and parts[2] == "permissions":
            state = "no permissions"
            detail_start = 3
        details: dict[str, str] = {}
        for item in parts[detail_start:]:
            if ":" in item:
                key, value = item.split(":", 1)
                details[key] = value
        devices.append(Device(serial, state, details))
    return devices


def parse_key_value_lines(output: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in output.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip()
    return values


def parse_package_dump(output: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("Package [") and "]" in line:
            fields["package"] = line.removeprefix("Package [").split("]", 1)[0]
        elif line.startswith("versionName="):
            fields["versionName"] = line.split("=", 1)[1]
        elif line.startswith("versionCode="):
            fields["versionCode"] = line.split("=", 1)[1].split(" ", 1)[0]
        elif line.startswith("firstInstallTime="):
            fields["firstInstallTime"] = line.split("=", 1)[1]
        elif line.startswith("lastUpdateTime="):
            fields["lastUpdateTime"] = line.split("=", 1)[1]
        elif line.startswith("firstCodePath="):
            fields["firstCodePath"] = line.split("=", 1)[1]
        elif line.startswith("dataDir="):
            fields["dataDir"] = line.split("=", 1)[1]
    return fields
