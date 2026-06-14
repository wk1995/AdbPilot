"""Error types and user-facing diagnostics."""

from __future__ import annotations


class AdbPilotError(RuntimeError):
    """Base error for expected AdbPilot failures."""


class AdbNotFoundError(AdbPilotError):
    """Raised when the adb executable cannot be found."""


class AdbCommandError(AdbPilotError):
    """Raised when an adb command exits with a non-zero status."""

    def __init__(self, message: str, returncode: int, stdout: str = "", stderr: str = "") -> None:
        super().__init__(message)
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class DeviceSelectionError(AdbPilotError):
    """Raised when a command cannot safely infer the target device."""


def suggestion_for_device_state(state: str) -> str:
    state = state.lower()
    if state == "unauthorized":
        return "设备未授权，请解锁手机并在 USB 调试授权弹窗中点击允许。"
    if state == "offline":
        return "设备离线，请重新插拔 USB、重启 adb server，或检查无线调试连接。"
    if state == "no permissions":
        return "当前用户没有设备权限；Linux 下请检查 udev 规则，Windows 下请检查驱动。"
    return "请确认设备已开启 USB 调试，连接稳定，并能被 adb devices 识别。"
