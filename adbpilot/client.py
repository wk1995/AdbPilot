"""ADB command wrapper and high-level operations."""

from __future__ import annotations

import os
import subprocess
import time
import re
from pathlib import Path
from typing import BinaryIO, Callable, Iterable, TextIO

from .errors import (
    AdbCommandError,
    AdbConnectionError,
    AdbPairingError,
    AdbWifiCapabilityError,
    DeviceSelectionError,
    PairingCodeError,
    WirelessAddressError,
    suggestion_for_device_state,
)
from . import history as connection_history
from .models import AdbWifiCapabilities, CommandResult, Device, DeviceInfo, RunningProcess, WirelessPairingResult
from .platform_adapter import PlatformAdapter


DEFAULT_TIMEOUT = 30


class AdbClient:
    """Small, testable wrapper around the adb executable."""

    def __init__(
        self,
        adb_path: str | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        *,
        history_source: str = "cli",
    ) -> None:
        self.adb_path = PlatformAdapter().resolve_adb(adb_path)
        self.timeout = timeout
        self.history_source = history_source

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
        # Another adb (Android Studio, a second SDK) can hold the USB device
        # on a different port. kill-server only stops the server this client
        # talks to, so the phone stays invisible after a "successful" restart.
        stopped = self._stop_competing_adb_servers()
        self.run(["kill-server"], check=False)
        self.run(["start-server"])
        detail = f"stopped competing adb: {', '.join(stopped)}" if stopped else ""
        self._record("restart-server", ok=True, message="adb server 已重启", detail=detail)

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
        normalized = normalize_adb_address(address, require_port=False)
        try:
            result = self.run(["connect", normalized])
        except AdbCommandError as exc:
            self._record("connect", ok=False, address=normalized, message=str(exc), detail=exc.stderr)
            raise
        output = result.stdout.strip()
        # adb can report connection failures on stdout with exit status 0.
        if not output.lower().startswith(("connected to ", "already connected to ")):
            message = output or result.stderr.strip() or "ADB 未返回连接成功信息"
            self._record("connect", ok=False, address=normalized, message=message, detail=result.stderr)
            raise AdbConnectionError(
                message,
                result.returncode,
                result.stdout,
                result.stderr,
                category="connect_failed",
            )
        matched = self._device_at(normalized)
        self._record(
            "connect",
            ok=True,
            address=normalized,
            serial=matched.serial if matched else "",
            state=matched.state if matched else "",
            model=self._device_model(matched),
            message=output,
        )
        return output

    def pair(
        self,
        address: str,
        pairing_code: str,
        *,
        connect_address: str | None = None,
        wait_for_device_seconds: float = 10.0,
        allow_qr_secret: bool = False,
    ) -> WirelessPairingResult:
        pairing_address = normalize_adb_address(address, require_port=True)
        code = pairing_code if allow_qr_secret else normalize_pairing_code(pairing_code)
        if allow_qr_secret and (not code or any(ord(char) < 32 for char in code)):
            raise PairingCodeError("扫码配对信息无效，请重新生成二维码。")
        explicit_address = (
            normalize_adb_address(connect_address, require_port=True) if connect_address else ""
        )
        capabilities = self.wifi_capabilities()
        if not capabilities.supports_pair:
            raise AdbWifiCapabilityError(capabilities.warnings[0] if capabilities.warnings else "当前 adb 不支持安全配对。")

        result = self._run_with_input(
            ["pair", pairing_address],
            input_text=f"{code}\n",
            timeout=60,
            check=False,
            redact=(code,),
        )
        outcome = classify_pair_output(result.returncode, result.stdout, result.stderr)
        if outcome == "failed":
            category = classify_pair_failure(result.stdout, result.stderr)
            message = wifi_error_message(category)
            self._record("pair", ok=False, address=pairing_address, message=message, detail=category)
            raise AdbPairingError(
                message,
                result.returncode,
                result.stdout,
                result.stderr,
                category=category,
            )
        if outcome == "ambiguous":
            self._record("pair", ok=False, address=pairing_address, message="配对结果无法确认")
            return WirelessPairingResult(
                status="ambiguous",
                paired=False,
                connected=False,
                pairing_address=pairing_address,
                connection_address=explicit_address,
                next_action="配对结果无法确认。请升级 Platform-Tools 到 34.0.5 或更高版本后重试。",
                capabilities=capabilities,
            )

        before = self.devices()
        appeared = self._wait_for_new_tls_device(before, wait_for_device_seconds)
        if appeared is not None:
            self._record(
                "pair",
                ok=True,
                address=explicit_address or pairing_address,
                serial=appeared.serial,
                state=appeared.state,
                model=self._device_model(appeared),
                message="配对后设备已出现",
            )
            return WirelessPairingResult(
                status="paired_and_connected",
                paired=True,
                connected=appeared.is_ready,
                pairing_address=pairing_address,
                connection_address=explicit_address,
                device_serial=appeared.serial,
                next_action="设备已自动出现。",
                capabilities=capabilities,
            )
        if explicit_address:
            self.connect(explicit_address)
            after = self.devices()
            matched = next((device for device in after if device.serial == explicit_address), None)
            if matched is None:
                raise AdbConnectionError(
                    wifi_error_message("connect_failed"),
                    0,
                    "",
                    "",
                    category="connect_failed",
                )
            self._record(
                "pair",
                ok=matched.is_ready,
                address=explicit_address,
                serial=matched.serial,
                state=matched.state,
                model=self._device_model(matched),
                message="已使用指定连接地址完成配对后连接",
            )
            return WirelessPairingResult(
                status="paired_connected_explicitly",
                paired=True,
                connected=matched.is_ready,
                pairing_address=pairing_address,
                connection_address=explicit_address,
                device_serial=matched.serial,
                next_action="已使用无线调试主页上的连接地址完成连接。",
                capabilities=capabilities,
            )
        self._record("pair", ok=True, address=pairing_address, message="配对完成，等待填写连接地址")
        return WirelessPairingResult(
            status="paired_waiting_for_connect",
            paired=True,
            connected=False,
            pairing_address=pairing_address,
            next_action="配对已完成，但设备没有自动出现。请填写手机无线调试主页上的连接地址，不要使用配对端口。",
            capabilities=capabilities,
        )

    def wifi_capabilities(self, *, probe_network: bool = False) -> AdbWifiCapabilities:
        version_text = self.run(["version"], timeout=10).stdout
        revision = parse_platform_tools_revision(version_text)
        status_result = self.run(["server-status"], timeout=10, check=False)
        status = parse_server_status(status_result.stdout + "\n" + status_result.stderr)
        revision_text = format_revision(revision)
        supports_pair = revision is not None and revision >= (34, 0, 5)
        mdns_enabled = status.get("mdns_enabled", "unknown").lower()
        mdns_backend = status.get("mdns_backend", "unknown")
        warnings: list[str] = []
        if revision is None:
            warnings.append("无法识别 Platform-Tools 版本，产品内不执行安全配对。")
        elif revision < (30, 0, 0):
            warnings.append("当前 adb 低于 30.0.0，只保留经典连接。")
        elif revision < (34, 0, 5):
            warnings.append("当前 adb 属于 30.0.0–34.0.4 的早期配对实现，产品内不执行配对。请升级到 34.0.5 或更高版本。")
        wifi2 = False
        if revision is not None and revision >= (37, 0, 0) and supports_pair and mdns_enabled == "true":
            if revision == (37, 0, 0) and mdns_backend.upper() != "LIBADBMDNS":
                warnings.append("37.0.0 的发现后端不是 LIBADBMDNS。可设置 ADB_MDNS_OPENSCREEN=0 后自行重启 adb。")
            else:
                wifi2 = True
        if mdns_enabled == "false":
            warnings.append("发现设备功能未开启。可设置 ADB_MDNS=1 后自行重启 adb，仍可手输连接地址。")
        if revision is not None and (30, 0, 1) <= revision < (35, 0, 0):
            warnings.append("这个版本默认不会自动连接已配对设备。10 秒内没有出现时，请填写连接地址。")
        network_status = "unknown"
        device_generation = "unknown"
        if probe_network:
            network_status, device_generation = self._probe_tls_services()
            if network_status == "not_visible":
                warnings.append("无线调试已开启，但当前网络没有发现设备。请确认同一网络，或手输连接地址。")
        return AdbWifiCapabilities(
            adb_path=str(self.adb_path),
            adb_version=version_text.strip(),
            platform_tools_revision=revision_text,
            supports_connect=True,
            supports_pair=supports_pair,
            mdns_enabled=mdns_enabled,
            mdns_backend=mdns_backend,
            network_mdns_status=network_status,
            wifi2_host_compatible=wifi2,
            device_generation=device_generation,
            warnings=tuple(warnings),
        )

    def disconnect(self, address: str | None = None) -> str:
        args = ["disconnect"]
        normalized = normalize_adb_address(address, require_port=False) if address else ""
        if normalized:
            args.append(normalized)
        try:
            output = self.run(args, timeout=60).stdout.strip()
        except AdbCommandError as exc:
            self._record("disconnect", ok=False, address=normalized, message=str(exc), detail=exc.stderr)
            raise
        self._record(
            "disconnect",
            ok=True,
            address=normalized,
            state="disconnected",
            message=output or ("已断开所有无线设备" if not normalized else "已断开"),
        )
        return output

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
        cancelled: Callable[[], bool] | None = None,
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

        def is_cancelled() -> bool:
            return cancelled is not None and cancelled()

        report("扫码配对：进入等待扫码状态")
        try:
            initial_services = parse_mdns_services(self.mdns_services())
        except Exception:
            report("扫码配对：退出扫码匹配状态（异常）")
            raise
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
            if is_cancelled():
                report("扫码配对：退出扫码匹配状态（已取消）")
                raise DeviceSelectionError("扫码配对已取消")
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
                try:
                    pairing_result = self.pair(
                        pair_address,
                        password,
                        wait_for_device_seconds=0,
                        allow_qr_secret=True,
                    )
                    pairing_output = pairing_result.next_action if pairing_result.paired else ""
                except Exception:
                    report("扫码配对：退出扫码匹配状态（异常）")
                    raise
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

    def _run_with_input(
        self,
        args: Iterable[str],
        *,
        input_text: str,
        timeout: int,
        check: bool = True,
        redact: Iterable[str] = (),
    ) -> CommandResult:
        command = self._build_command(args)
        completed = subprocess.run(
            command,
            input=input_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            shell=False,
        )
        stdout = redact_secrets(completed.stdout, redact)
        stderr = redact_secrets(completed.stderr, redact)
        result = CommandResult(tuple(command), completed.returncode, stdout, stderr)
        if check and completed.returncode != 0:
            raise AdbCommandError(
                self._format_failure(command, completed.returncode, stderr, stdout),
                completed.returncode,
                stdout,
                stderr,
            )
        return result

    def _wait_for_new_tls_device(self, before: list[Device], seconds: float) -> Device | None:
        known = {device.serial for device in before}
        deadline = time.monotonic() + max(seconds, 0)
        while True:
            for device in self.devices():
                if device.serial in known or not is_tls_connect_serial(device.serial):
                    continue
                return device
            if time.monotonic() >= deadline:
                return None
            time.sleep(min(1, max(deadline - time.monotonic(), 0)))

    def _probe_tls_services(self) -> tuple[str, str]:
        text = self._read_track_services(seconds=3)
        if "_adb-tls-connect" not in text and "adb-tls-connect" not in text:
            # A header such as "Services:" only means the command started.
            # It is not evidence that wireless debugging is enabled.
            return "unknown", "unknown"
        generation = "wifi1"
        for value in re.findall(r"mdns_service_version\D+([0-9]+(?:\.[0-9]+)?)", text):
            major = int(value.split(".", 1)[0])
            if major >= 2:
                generation = "wifi2"
        return "visible", generation

    def _read_track_services(self, *, seconds: float) -> str:
        """Read a bounded sample from the streaming track-services command.

        The command does not exit on its own. Stopping it with a normal
        subprocess timeout discards the sample on this Python version, so the
        process is read directly and terminated when the window ends.
        """

        command = self._build_command(["mdns", "track-services", "--proto-text"])
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        try:
            stdout, stderr = process.communicate(timeout=seconds)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
        return f"{stdout or ''}\n{stderr or ''}"

    def note_device_changes(self, before: list[Device], after: list[Device]) -> list[str]:
        """Record devices that appeared, disappeared, or changed state."""

        previous = {device.serial: device for device in before}
        current = {device.serial: device for device in after}
        messages: list[str] = []
        for serial, device in current.items():
            old = previous.get(serial)
            if old is None:
                model = self._device_model(device)
                self._record(
                    "seen",
                    ok=device.is_ready,
                    serial=serial,
                    state=device.state,
                    model=model,
                    address=serial if ":" in serial else "",
                    message=f"发现设备 {model or serial}",
                )
                messages.append(f"发现设备：{model or serial}（{device.state}）")
            elif old.state != device.state:
                model = self._device_model(device)
                self._record(
                    "state",
                    ok=device.is_ready,
                    serial=serial,
                    state=device.state,
                    model=model,
                    message=f"状态 {old.state} -> {device.state}",
                )
                messages.append(f"设备状态变化：{model or serial} {old.state} -> {device.state}")
        for serial, device in previous.items():
            if serial in current:
                continue
            model = self._device_model(device)
            self._record(
                "lost",
                ok=False,
                serial=serial,
                state="已断开",
                model=model,
                message=f"设备离开列表 {model or serial}",
            )
            messages.append(f"设备已离开：{model or serial}")
        return messages

    def _device_at(self, address: str) -> Device | None:
        try:
            devices = self.devices()
        except AdbCommandError:
            return None
        for device in devices:
            if device.serial == address or address in device.serial:
                return device
        return None

    @staticmethod
    def _device_model(device: Device | None) -> str:
        if device is None:
            return ""
        return str(device.details.get("model") or device.details.get("product") or "")

    def _record(
        self,
        event: str,
        *,
        ok: bool,
        address: str = "",
        serial: str = "",
        state: str = "",
        model: str = "",
        message: str = "",
        detail: str = "",
    ) -> None:
        try:
            connection_history.record_event(
                event,
                source=self.history_source,
                ok=ok,
                address=address,
                serial=serial,
                state=state,
                model=model,
                message=message,
                detail=detail,
            )
        except OSError:
            return

    def _stop_competing_adb_servers(self) -> list[str]:
        """Stop adb servers that are not the one this client uses.

        A second server keeps the USB interface exclusively, so `adb devices`
        on the default port stays empty even though the phone says it is connected.
        """
        own_port = os.environ.get("ANDROID_ADB_SERVER_PORT", "5037")
        stopped: list[str] = []
        for port, pid in self._listening_adb_servers():
            if port == own_port:
                continue
            completed = subprocess.run(
                [str(self.adb_path), "-P", port, "kill-server"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
            )
            if completed.returncode == 0:
                stopped.append(f"{port} (pid {pid})" if pid else port)
        return stopped

    def _listening_adb_servers(self) -> list[tuple[str, str]]:
        system = os.uname().sysname.lower() if hasattr(os, "uname") else ""
        if system == "darwin":
            completed = subprocess.run(
                ["lsof", "-nP", "-iTCP", "-sTCP:LISTEN", "-c", "adb"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
            )
            return _parse_lsof_adb_listeners(completed.stdout)
        if system == "linux":
            completed = subprocess.run(
                ["ss", "-ltnp"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
            )
            return _parse_ss_adb_listeners(completed.stdout)
        return []

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


def _parse_lsof_adb_listeners(output: str) -> list[tuple[str, str]]:
    listeners: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw_line in output.splitlines():
        parts = raw_line.split()
        if len(parts) < 9 or parts[0] != "adb" or not parts[-1].startswith("(LISTEN)"):
            continue
        name = parts[-2]
        port = name.rsplit(":", 1)[-1]
        pid = parts[1]
        if not port.isdigit() or port in seen:
            continue
        seen.add(port)
        listeners.append((port, pid))
    return listeners


def _parse_ss_adb_listeners(output: str) -> list[tuple[str, str]]:
    listeners: list[tuple[str, str]] = []
    seen: set[str] = set()
    for line in output.splitlines():
        if "adb" not in line:
            continue
        port_match = re.search(r":(\d+)\s", line)
        pid_match = re.search(r"pid=(\d+)", line)
        if not port_match:
            continue
        port = port_match.group(1)
        if port in seen:
            continue
        seen.add(port)
        listeners.append((port, pid_match.group(1) if pid_match else ""))
    return listeners


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


def parse_platform_tools_revision(output: str) -> tuple[int, int, int] | None:
    for line in output.splitlines():
        match = re.search(r"\bVersion\s+(\d+)\.(\d+)\.(\d+)", line)
        if match and not line.lower().startswith("android debug bridge"):
            return tuple(int(part) for part in match.groups())  # type: ignore[return-value]
    return None


def format_revision(revision: tuple[int, int, int] | None) -> str:
    if revision is None:
        return ""
    return ".".join(str(part) for part in revision)


def parse_server_status(output: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in output.splitlines():
        line = raw_line.strip().lstrip("-").strip()
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower().replace(" ", "_")
        if key in {"version", "mdns_enabled", "mdns_backend"}:
            values[key] = value.strip().strip('"')
    return values


def normalize_adb_address(value: str, *, require_port: bool = True) -> str:
    text = value.strip()
    if not text or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise WirelessAddressError("请使用手机当前页面显示的 IP:端口。")
    if text.startswith("[") and "]" in text:
        host, _, port = text[1:].partition("]")
        port = port[1:] if port.startswith(":") else ""
        if not host or ":" not in host:
            raise WirelessAddressError("请使用手机当前页面显示的 IP:端口。")
    else:
        if text.count(":") != 1:
            if require_port:
                raise WirelessAddressError("请使用手机当前页面显示的 IP:端口。")
            return text
        host, port = text.rsplit(":", 1)
        if not host or any(char.isspace() for char in host):
            raise WirelessAddressError("请使用手机当前页面显示的 IP:端口。")
    if require_port or port:
        if not port.isdigit() or not 1 <= int(port) <= 65535:
            raise WirelessAddressError("请使用手机当前页面显示的 IP:端口。")
    return text


def normalize_pairing_code(value: str) -> str:
    code = value.strip()
    if not re.fullmatch(r"[0-9]{6}", code):
        raise PairingCodeError("请重新打开配对码页面，输入当前显示的 6 位数字。")
    return code


def classify_pair_output(returncode: int, stdout: str, stderr: str) -> str:
    text = f"{stdout}\n{stderr}".lower()
    if returncode != 0 or re.search(r"\bunsuccessful\b|\bfailed\b", text):
        return "failed"
    if any(token in text for token in ("successfully paired", "paired to")):
        return "success"
    return "ambiguous"


def classify_pair_failure(stdout: str, stderr: str) -> str:
    text = f"{stdout}\n{stderr}".lower()
    if any(token in text for token in ("timeout", "timed out", "connection refused", "unreachable", "no route")):
        return "pair_unreachable"
    if any(token in text for token in ("failed", "incorrect", "wrong", "rejected", "denied", "invalid")):
        return "pair_rejected"
    return "unknown"


def wifi_error_message(category: str) -> str:
    messages = {
        "adb_missing": "未找到 adb。请安装或选择 Platform-Tools。",
        "pair_unsupported": "当前 adb 不能在产品内安全配对。请升级到 34.0.5 或更高版本，经典连接仍可使用。",
        "mdns_disabled": "发现设备功能未开启。可设置 ADB_MDNS=1 后自行重启 adb，仍可手输地址。",
        "mdns_backend_obsolete": "发现设备的后端过旧。37.0.0 可设置 ADB_MDNS_OPENSCREEN=0 后自行重启 adb。",
        "network_mdns_not_visible": "当前网络没有发现设备。请确认同一网络，或手输连接地址。",
        "invalid_address": "请使用手机当前页面显示的 IP:端口。",
        "invalid_code": "请重新打开配对码页面，输入当前显示的 6 位数字。",
        "pair_rejected": "配对码可能错误或已过期。请在手机上生成新的配对码后重试。",
        "pair_unreachable": "配对页面可能已关闭，或地址已经变化。请保持配对页面开启后重试。",
        "paired_not_connected": "已经配对，但设备没有自动出现。请填写无线调试主页上的连接地址。",
        "connect_failed": "连接失败。请使用无线调试主页上的连接端口，不要使用配对端口。",
        "unknown": "配对没有完成。请查看手机当前页面后重试。",
    }
    return messages.get(category, messages["unknown"])


def is_tls_connect_serial(serial: str) -> bool:
    return "._adb-tls-connect._tcp" in serial.lower()


def redact_secrets(text: str, secrets_to_hide: Iterable[str]) -> str:
    redacted = text
    for secret in secrets_to_hide:
        if secret:
            redacted = redacted.replace(secret, "[redacted]")
    return redacted


def capability_summary(capabilities: AdbWifiCapabilities) -> str:
    parts = ["经典连接可用"]
    parts.append("安全配对可用" if capabilities.supports_pair else "安全配对不可用")
    if capabilities.wifi2_host_compatible:
        parts.append("主机具备 Wi-Fi 2.0 条件")
    if capabilities.device_generation == "wifi2":
        parts.append("设备支持 Wi-Fi 2.0")
    elif capabilities.device_generation == "wifi1":
        parts.append("设备为安全无线调试")
    return " · ".join(parts)


def pairing_status_text(result: WirelessPairingResult) -> str:
    labels = {
        "paired_and_connected": "配对成功，设备已出现",
        "paired_waiting_for_connect": "配对成功，等待填写连接地址",
        "paired_connected_explicitly": "配对成功，已按连接地址连上",
        "unsupported": "当前 adb 不支持安全配对",
        "ambiguous": "配对结果无法确认",
    }
    label = labels.get(result.status, "配对结束")
    if result.next_action:
        return f"{label}。{result.next_action}"
    return label
