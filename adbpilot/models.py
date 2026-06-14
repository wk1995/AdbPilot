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
