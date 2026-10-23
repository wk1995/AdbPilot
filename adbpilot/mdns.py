"""Bounded, evidence-based recovery of stale ADB mDNS discovery."""

from __future__ import annotations

import math
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Callable

from .errors import AdbCommandError
from .models import Device

if TYPE_CHECKING:
    from .client import AdbClient
    from .platform_adapter import PlatformAdapter


TLS_SERVICE_TYPES = ("_adb-tls-pairing._tcp", "_adb-tls-connect._tcp")
# Server startup can wait for discovery/TLS initialization before sending its ACK.
# Killing its client at the short query deadline can break the server's ACK pipe.
ADB_SERVER_START_TIMEOUT_SECONDS = 30


class MdnsRecoveryState(str, Enum):
    IDLE = "idle"
    PROBING = "probing"
    CONFIRMING = "confirming"
    RESTARTING = "restarting"
    VERIFYING = "verifying"
    COOLDOWN = "cooldown"


@dataclass(frozen=True)
class MdnsService:
    """Recovery evidence deliberately excludes raw instance names/identifiers."""

    service_type: str
    address: str = ""
    name_conflict: bool = False


@dataclass(frozen=True)
class SystemMdnsDiscovery:
    supported: bool
    services: tuple[MdnsService, ...] = ()


@dataclass(frozen=True)
class MdnsRecoveryConfig:
    enabled: bool = True
    probe_interval_seconds: float = 15
    restart_cooldown_seconds: float = 60
    system_browse_timeout_seconds: float = 1.2
    confirm_delay_seconds: float = 0.3

    @classmethod
    def from_gui_config(cls, config: dict) -> MdnsRecoveryConfig:
        values = config.get("wireless_recovery", {})
        if not isinstance(values, dict):
            return cls()
        defaults = cls()

        def number(key: str, minimum: float, maximum: float) -> float:
            value = values.get(key, getattr(defaults, key))
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or (isinstance(value, float) and not math.isfinite(value)) or value < 0):
                return getattr(defaults, key)
            return max(minimum, min(maximum, value))

        enabled = values.get("enabled", True)
        return cls(
            enabled=enabled if isinstance(enabled, bool) else True,
            probe_interval_seconds=number("probe_interval_seconds", 5, 3600),
            restart_cooldown_seconds=number("restart_cooldown_seconds", 30, 86400),
            system_browse_timeout_seconds=number("system_browse_timeout_seconds", 0.1, 5),
            confirm_delay_seconds=number("confirm_delay_seconds", 0.1, 2),
        )


RECOVERY_MESSAGES = {
    "recovered": "ADB Server 已自动重启，已恢复无线调试服务发现或设备可见性。",
    "restarted_unverified": "ADB Server 已重启，但暂未发现服务或设备；请保持无线调试开启。",
    "restart_failed": "ADB Server 自动重启失败，请使用“重启服务”检查 ADB 环境。",
    "suspected_name_conflict": "已发现疑似重复配对实例。请关闭并重新打开手机无线调试，重新生成二维码，或改用配对码。",
    "probe_failed": "无线调试自动恢复检测失败，本轮已跳过。",
}


@dataclass(frozen=True)
class MdnsRecoveryResult:
    status: str
    restarted: bool = False
    adb_services_before: tuple[MdnsService, ...] = ()
    system_services: tuple[MdnsService, ...] = ()
    adb_services_after: tuple[MdnsService, ...] = ()
    devices_after: tuple[Device, ...] = ()

    @property
    def message(self) -> str:
        return RECOVERY_MESSAGES.get(self.status, "")


def has_name_conflict(name: str) -> bool:
    return re.search(r"\((?:[2-9]|[1-9]\d+)\)$", name.strip()) is not None


def parse_dns_sd_services(output: str) -> tuple[MdnsService, ...]:
    """Apply Add/Rmv events per interface; never retain raw names in results."""
    active: dict[tuple[str, str, str, str], MdnsService] = {}
    for line in output.splitlines():
        parts = line.split(maxsplit=6)
        if len(parts) != 7 or parts[1] not in {"Add", "Rmv"}:
            continue
        _timestamp, operation, flags, interface, domain, service_type, name = parts
        service_type = service_type.rstrip(".").lower()
        if service_type not in TLS_SERVICE_TYPES or domain.lower() != "local.":
            continue
        try:
            int(flags, 16)
            int(interface)
        except ValueError:
            continue
        key = (interface, domain, service_type, name)
        if operation == "Rmv":
            active.pop(key, None)
        else:
            active[key] = MdnsService(service_type, name_conflict=has_name_conflict(name))
    return tuple(dict.fromkeys(active.values()))


class MdnsRecoveryController:
    """One GUI-process lifetime, including client changes and manual restarts."""

    def __init__(self, config: MdnsRecoveryConfig | None = None) -> None:
        self.config = config or MdnsRecoveryConfig()
        self.state = MdnsRecoveryState.IDLE
        self.last_probe: float | None = None
        self.last_restart: float | None = None
        self._lock = threading.Lock()

    def note_restart(self) -> None:
        # Caller serializes manual/automatic restarts with the GUI service lock.
        self.last_restart = time.monotonic()
        self.state = MdnsRecoveryState.COOLDOWN

    def recover(
        self,
        client: AdbClient,
        adapter: PlatformAdapter,
        *,
        cancelled: Callable[[], bool] = lambda: False,
        progress: Callable[[str], None] | None = None,
    ) -> MdnsRecoveryResult:
        if not self._lock.acquire(blocking=False):
            return MdnsRecoveryResult("busy")
        before: tuple[MdnsService, ...] = ()
        system: tuple[MdnsService, ...] = ()
        restarted = False
        attempted = False

        def result(status: str, after: tuple[MdnsService, ...] = (), devices: tuple[Device, ...] = ()) -> MdnsRecoveryResult:
            return MdnsRecoveryResult(status, restarted, before, system, after, devices)

        def transition(state: MdnsRecoveryState, message: str = "") -> None:
            self.state = state
            if message and progress:
                progress(message)

        try:
            if cancelled():
                return result("cancelled")
            if not self.config.enabled:
                return result("disabled")
            now = time.monotonic()
            if self.last_restart is not None and now - self.last_restart < self.config.restart_cooldown_seconds:
                return result("cooldown")
            self.state = MdnsRecoveryState.IDLE
            if self.last_probe is not None and now - self.last_probe < self.config.probe_interval_seconds:
                return result("interval")
            self.last_probe = now
            devices = client.mdns_recovery_devices()
            if devices:
                return result("device_present", devices=devices)
            transition(MdnsRecoveryState.PROBING)
            if not client.mdns_recovery_supported():
                return result("unsupported")
            before = client.mdns_recovery_services()
            if before:
                status = "suspected_name_conflict" if any(s.name_conflict for s in before) else "not_needed"
                return result(status)
            if cancelled():
                return result("cancelled")
            discovery = adapter.discover_system_adb_mdns_services(timeout=self.config.system_browse_timeout_seconds)
            if cancelled():
                return result("cancelled")
            if not discovery.supported:
                return result("unsupported")
            system = discovery.services
            if not system:
                return result("no_system_evidence")
            transition(MdnsRecoveryState.CONFIRMING, "系统可见但 ADB 不可见的无线调试服务，正在二次确认…")
            time.sleep(self.config.confirm_delay_seconds)
            if cancelled():
                return result("cancelled")
            confirmed = client.mdns_recovery_services()
            if confirmed:
                status = "suspected_name_conflict" if any(s.name_conflict for s in confirmed) else "not_needed"
                return result(status, after=confirmed)
            # All records, even offline/unauthorized/no-permissions, protect the server.
            devices = client.mdns_recovery_devices()
            if devices:
                return result("device_present", devices=devices)
            if cancelled():
                return result("cancelled")
            attempted = True
            self.note_restart()
            transition(MdnsRecoveryState.RESTARTING, "ADB mDNS 状态持续异常，正在自动重启 ADB Server…")
            # Unlike the manual action, never stop competing servers on other ports.
            client.run(["kill-server"], check=False, timeout=3)
            client.run(["start-server"], timeout=ADB_SERVER_START_TIMEOUT_SECONDS)
            restarted = True
            transition(MdnsRecoveryState.VERIFYING)
            time.sleep(self.config.confirm_delay_seconds)
            if cancelled():
                return result("cancelled")
            after = client.mdns_recovery_services()
            devices = client.mdns_recovery_devices()
            if not devices and any(s.name_conflict for s in after):
                return result("suspected_name_conflict", after, devices)
            return result("recovered" if after or devices else "restarted_unverified", after, devices)
        except (OSError, subprocess.SubprocessError, AdbCommandError, ValueError):
            # Exceptions may contain command output/instance names; do not display them.
            return result("restarted_unverified" if restarted else "restart_failed" if attempted else "probe_failed")
        finally:
            if self.last_restart is not None and time.monotonic() - self.last_restart < self.config.restart_cooldown_seconds:
                self.state = MdnsRecoveryState.COOLDOWN
            else:
                self.state = MdnsRecoveryState.IDLE
            self._lock.release()
