"""ADB command wrapper and high-level operations."""

from __future__ import annotations

import subprocess
import time
import re
from pathlib import Path
from typing import BinaryIO, Callable, Iterable, TextIO

from .errors import AdbCommandError, DeviceSelectionError, suggestion_for_device_state
from .models import CommandResult, Device, DeviceInfo, RunningProcess
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
                self._format_failure(command, completed.returncode, completed.stderr, completed.stdout),
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
        return self._deduplicate_devices(parse_devices(result.stdout))

    def _deduplicate_devices(self, devices: list[Device]) -> list[Device]:
        """Collapse multiple ADB transports that point to the same handset."""
        ready_devices = [device for device in devices if device.is_ready]
        if len(ready_devices) < 2:
            return devices

        deduplicated: list[Device] = []
        hardware_indexes: dict[str, int] = {}
        for device in devices:
            if not device.is_ready:
                deduplicated.append(device)
                continue

            hardware_serial = self._hardware_serial(device)
            if not hardware_serial:
                deduplicated.append(device)
                continue

            existing_index = hardware_indexes.get(hardware_serial)
            if existing_index is None:
                hardware_indexes[hardware_serial] = len(deduplicated)
                deduplicated.append(device)
                continue

            if self._device_preference(device) > self._device_preference(deduplicated[existing_index]):
                deduplicated[existing_index] = device

        return deduplicated

    def _hardware_serial(self, device: Device) -> str | None:
        result = self.run(
            ["shell", "getprop", "ro.serialno"],
            serial=device.serial,
            timeout=5,
            check=False,
        )
        value = result.stdout.strip()
        if not value or value.lower() in {"unknown", "<unknown>"}:
            return None
        return value

    @staticmethod
    def _device_preference(device: Device) -> int:
        serial = device.serial.lower()
        if "._adb-tls-" in serial or "_adb-tls-" in serial:
            return 0
        if ":" in serial:
            return 2
        return 1

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

    def foreground_package(self, serial: str | None = None) -> str:
        target = self.require_serial(serial)
        window_output = self.run(["shell", "dumpsys", "window"], serial=target, timeout=20, check=False).stdout
        package = parse_foreground_package(window_output)
        if package:
            return package

        activity_output = self.run(["shell", "dumpsys", "activity", "activities"], serial=target, timeout=20, check=False).stdout
        package = parse_foreground_package(activity_output)
        if package:
            return package

        raise DeviceSelectionError("未能识别当前前台应用包名。请确认设备已解锁且有应用处于前台。")

    def running_processes(self, serial: str | None = None) -> list[RunningProcess]:
        target = self.require_serial(serial)
        result = self.run(["shell", "ps", "-A"], serial=target, check=False, timeout=20)
        output = result.stdout
        if result.returncode != 0 or not output.strip():
            output = self.run(["shell", "ps"], serial=target, timeout=20).stdout
        return parse_processes(output)

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

    def pair(self, address: str, pairing_code: str) -> str:
        return self.run(["pair", address, pairing_code], timeout=60).stdout.strip()

    def disconnect(self, address: str | None = None) -> str:
        args = ["disconnect"]
        if address:
            args.append(address)
        return self.run(args, timeout=60).stdout.strip()

    def mdns_services(self) -> str:
        return self.run(["mdns", "services"], timeout=20).stdout

    def wait_for_mdns_connect_service(self, *, host: str | None = None, timeout: int = 30) -> dict[str, str] | None:
        deadline = time.monotonic() + timeout
        last_match: dict[str, str] | None = None
        while time.monotonic() < deadline:
            services = parse_mdns_services(self.mdns_services())
            last_match = find_mdns_service(
                services,
                service_type="_adb-tls-connect._tcp",
                host=host,
            )
            if last_match:
                return last_match
            if host is not None:
                last_match = find_mdns_service(services, service_type="_adb-tls-connect._tcp")
                if last_match:
                    return last_match
            time.sleep(1)
        return None

    def connect_paired_by_mdns(self, *, timeout: int = 15) -> str:
        connect_service = self.wait_for_mdns_connect_service(timeout=timeout)
        if not connect_service:
            raise DeviceSelectionError(
                "未发现已配对设备的 ADB 连接服务（_adb-tls-connect）。"
                "请确认手机和电脑在同一个 Wi-Fi，并保持手机“无线调试”页面打开；"
                "如果仍然没有发现，请在手机无线调试主页面查看 IP 地址和端口，填到“连接地址”后点击连接。"
            )
        return self.connect(connect_service["address"])

    def pair_by_qr(
        self,
        service_name: str,
        password: str,
        *,
        timeout: int = 120,
        progress: Callable[[str], None] | None = None,
    ) -> str:
        """Complete a QR pairing session and connect every newly discovered device.

        Android versions differ in what they advertise after a QR scan. Some
        expose a pairing service first, while others expose the TLS connect
        service immediately after the scan. The connect service is authoritative
        for this GUI, so it must be accepted as a successful scan result.
        """

        def report(message: str) -> None:
            if progress:
                progress(message)

        report("扫码配对：进入等待扫码状态")
        initial_services = parse_mdns_services(self.mdns_services())
        known_connect_addresses = {
            service["address"]
            for service in initial_services
            if is_mdns_service_type(service.get("type", ""), "adb-tls-connect._tcp")
        }
        report(f"扫码配对：当前已发现 {len(known_connect_addresses)} 个连接服务")

        deadline = time.monotonic() + timeout
        pairing_attempted = False
        pairing_output = ""
        discovered: dict[str, dict[str, str]] = {}
        last_services = ""
        collect_until: float | None = None
        post_pair_deadline: float | None = None

        while time.monotonic() < deadline:
            last_services = self.mdns_services()
            services = parse_mdns_services(last_services)

            pairing_service = find_mdns_service(
                services,
                name=service_name,
                service_type="adb-tls-pairing._tcp",
            )
            if pairing_service and not pairing_attempted:
                pairing_attempted = True
                pair_address = pairing_service["address"]
                report(f"扫码配对：发现配对服务 {pair_address}，正在执行 adb pair")
                pairing_output = self.pair(pair_address, password)
                report(f"扫码配对：adb pair 完成{f'：{pairing_output}' if pairing_output else ''}")
                post_pair_deadline = min(deadline, time.monotonic() + 45)

            for service in services:
                if not is_mdns_service_type(service.get("type", ""), "adb-tls-connect._tcp"):
                    continue
                address = service["address"]
                if address in known_connect_addresses or address in discovered:
                    continue
                discovered[address] = service
                collect_until = time.monotonic() + 3
                report(f"扫码配对：发现连接服务 {service['name']} -> {address}")

            if discovered and collect_until is not None and time.monotonic() >= collect_until:
                break
            if pairing_attempted and not discovered and post_pair_deadline is not None:
                if time.monotonic() >= post_pair_deadline:
                    break
            time.sleep(1)

        if not discovered:
            detail = last_services.strip() or "<empty>"
            message = (
                "扫码配对：超时，未发现新的 ADB 连接服务。\n"
                "请确认手机和电脑在同一个 Wi-Fi，并在手机无线调试中选择“使用二维码配对设备”。\n"
                f"最近一次 mdns services 输出：\n{detail}"
            )
            report(message)
            report("扫码配对：退出扫码匹配状态（失败）")
            raise DeviceSelectionError(message)

        if len(discovered) > 1:
            report(f"扫码配对：本次发现 {len(discovered)} 台设备，将分别建立连接")
        else:
            report("扫码配对：已发现 1 台设备，正在建立连接")

        outputs: list[str] = []
        failures: list[str] = []
        for service in discovered.values():
            address = service["address"]
            report(f"扫码配对：正在连接 {address}")
            try:
                connect_output = self.connect(address)
            except AdbCommandError as exc:
                failures.append(f"{address}: {exc}")
                report(f"扫码配对：连接失败 {address}")
                continue
            outputs.append(f"{address}: {connect_output}" if connect_output else address)
            report(f"扫码配对：连接完成 {address}")

        if not outputs:
            message = "扫码配对：发现设备，但所有 ADB 连接均失败。\n" + "\n".join(failures)
            report("扫码配对：退出扫码匹配状态（失败）")
            raise DeviceSelectionError(message)

        result_lines = []
        if pairing_output:
            result_lines.append(pairing_output)
        result_lines.extend(outputs)
        if failures:
            result_lines.append("部分设备连接失败：\n" + "\n".join(failures))
        report(f"扫码配对：完成，成功连接 {len(outputs)} 台设备")
        report("扫码配对：退出扫码匹配状态（完成）")
        return "\n".join(result_lines)

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
    def _format_failure(command: list[str], returncode: int, stderr: str, stdout: str = "") -> str:
        rendered = " ".join(command)
        if stdout.strip():
            stderr = f"{stderr.strip()}\nstdout:\n{stdout.strip()}".strip()
        detail = stderr.strip() or "无 stdout/stderr 输出"
        return f"ADB 命令执行失败，退出码 {returncode}: {rendered}\n{detail}"


def parse_mdns_services(output: str) -> list[dict[str, str]]:
    services: list[dict[str, str]] = []
    for raw_line in output.splitlines():
        parts = raw_line.split()
        if len(parts) < 3 or not is_mdns_service_type(parts[1]):
            continue
        services.append({"name": parts[0], "type": parts[1], "address": parts[2]})
    return services


def find_mdns_service(
    services: list[dict[str, str]],
    *,
    service_type: str,
    name: str | None = None,
    host: str | None = None,
) -> dict[str, str] | None:
    normalized_service_type = normalize_mdns_service_type(service_type)
    for service in services:
        if normalize_mdns_service_type(service.get("type", "")) != normalized_service_type:
            continue
        if name is not None and service.get("name") != name:
            continue
        if host is not None and mdns_address_host(service.get("address", "")) != host:
            continue
        return service
    return None


def normalize_mdns_service_type(service_type: str) -> str:
    return service_type.strip().lower().lstrip("_").rstrip(".")


def is_mdns_service_type(service_type: str, expected: str | None = None) -> bool:
    normalized = normalize_mdns_service_type(service_type)
    known_types = {"adb-tls-pairing._tcp", "adb-tls-connect._tcp"}
    if expected is None:
        return normalized in known_types
    return normalized == normalize_mdns_service_type(expected)


def mdns_address_host(address: str) -> str:
    if address.startswith("[") and "]" in address:
        return address[1:].split("]", 1)[0]
    return address.rsplit(":", 1)[0]


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


def parse_foreground_package(output: str) -> str:
    patterns = [
        r"mCurrentFocus=.*?\s([A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+)/",
        r"mFocusedApp=.*?\s([A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+)/",
        r"topResumedActivity=.*?\s([A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+)/",
        r"mResumedActivity=.*?\s([A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+)/",
        r"ACTIVITY\s+([A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+)/",
    ]
    for pattern in patterns:
        match = re.search(pattern, output)
        if match:
            return match.group(1)
    return ""


def parse_processes(output: str) -> list[RunningProcess]:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines:
        return []

    header = lines[0].split()
    if "PID" not in header:
        return []

    pid_index = header.index("PID")
    user_index = header.index("USER") if "USER" in header else 0
    name_index = find_process_name_index(header)
    processes: list[RunningProcess] = []

    for line in lines[1:]:
        parts = line.split(None, name_index)
        if len(parts) <= max(pid_index, user_index, name_index):
            continue
        pid = parts[pid_index]
        user = parts[user_index]
        name = parts[name_index]
        package = name if looks_like_package_name(name) else ""
        processes.append(RunningProcess(pid=pid, user=user, name=name, package=package))

    return processes


def find_process_name_index(header: list[str]) -> int:
    for candidate in ("NAME", "ARGS", "CMD", "COMMAND"):
        if candidate in header:
            return header.index(candidate)
    return len(header) - 1


def looks_like_package_name(value: str) -> bool:
    return bool(re.match(r"^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+(?:[:][A-Za-z0-9_.-]+)?$", value))
