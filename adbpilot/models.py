"""Data models used by AdbPilot."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True)
class CommandResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class Device:
    serial: str
    state: str
    details: Mapping[str, str] = field(default_factory=dict)

    @property
    def is_ready(self) -> bool:
        return self.state == "device"


@dataclass(frozen=True)
class DeviceInfo:
    serial: str
    state: str
    android_version: str = ""
    sdk: str = ""
    brand: str = ""
    model: str = ""
    product: str = ""
    abi: str = ""
    battery_level: str = ""
    battery_status: str = ""


@dataclass(frozen=True)
class RunningProcess:
    pid: str
    user: str
    name: str
    package: str = ""


@dataclass(frozen=True)
class AdbWifiCapabilities:
    adb_path: str
    adb_version: str
    platform_tools_revision: str = ""
    supports_connect: bool = True
    supports_pair: bool = False
    mdns_enabled: str = "unknown"
    mdns_backend: str = "unknown"
    network_mdns_status: str = "unknown"
    wifi2_host_compatible: bool = False
    device_generation: str = "unknown"
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class WirelessPairingResult:
    status: str
    paired: bool
    connected: bool
    pairing_address: str
    connection_address: str = ""
    device_serial: str = ""
    next_action: str = ""
    capabilities: AdbWifiCapabilities | None = None
