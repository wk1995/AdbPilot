"""Cache verified hardware identity for continuously visible ADB transports."""

from __future__ import annotations

import re
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, Iterator

from .models import Device


RETRY_SECONDS = 30


def _transport_key(device: Device) -> tuple[str, int] | None:
    identifier = device.details.get("transport_id", "")
    if device.is_ready and re.fullmatch(r"[1-9][0-9]{0,19}", identifier):
        return device.serial, int(identifier)
    return None


@dataclass
class _Identity:
    hardware_serial: str | None = None
    retry_at: float = 0


class DeviceIdentityCache:
    def __init__(self) -> None:
        self._entries: dict[tuple[str, int], _Identity] = {}
        self._lock = threading.Lock()
        self._restart_gate = threading.Lock()
        self._working = False

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    @contextmanager
    def restarting(self) -> Iterator[None]:
        # Keep background adb invocations outside an explicit kill/start gap.
        self.clear()
        with self._restart_gate:
            self.clear()
            yield

    def observe(self, devices: list[Device], lookup: Callable[[int], str | None]) -> None:
        keys = list(dict.fromkeys(key for device in devices if (key := _transport_key(device)) is not None))
        with self._lock:
            self._entries = {key: self._entries.get(key) or _Identity() for key in keys}
            if self._working or len(keys) < 2:
                return
            now = time.monotonic()
            for key, entry in self._entries.items():
                if entry.hardware_serial is None and entry.retry_at <= now:
                    self._working = True
                    worker = threading.Thread(target=self._resolve, args=(key, entry, lookup), daemon=True)
                    try:
                        worker.start()
                    except RuntimeError:
                        self._working = False
                        entry.retry_at = now + RETRY_SECONDS
                    break

    def _resolve(self, key: tuple[str, int], entry: _Identity, lookup: Callable[[int], str | None]) -> None:
        identity = None
        try:
            with self._restart_gate:
                with self._lock:
                    if self._entries.get(key) is not entry:
                        return
                identity = lookup(key[1])
        finally:
            with self._lock:
                if self._entries.get(key) is entry:
                    entry.hardware_serial = identity
                    entry.retry_at = time.monotonic() + RETRY_SECONDS
                self._working = False

    def lookup(self, device: Device) -> str | None:
        key = _transport_key(device)
        with self._lock:
            entry = self._entries.get(key) if key is not None else None
            return entry.hardware_serial if entry is not None else None
