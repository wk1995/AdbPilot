"""Platform-specific adb discovery."""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from .errors import AdbNotFoundError
from .mdns import SystemMdnsDiscovery, TLS_SERVICE_TYPES, parse_dns_sd_services


class PlatformAdapter:
    """Finds an adb executable without making assumptions about the host OS."""

    def __init__(self, env: dict[str, str] | None = None) -> None:
        self.env = env if env is not None else os.environ
        self.system = platform.system().lower()
        self._mdns_lock = threading.Lock()
        self._mdns_processes: set[subprocess.Popen] = set()
        self._mdns_closed = False

    def cancel_mdns_discovery(self) -> None:
        """Prevent new browsers and terminate active ones; their worker reaps them."""
        with self._mdns_lock:
            self._mdns_closed = True
            for process in self._mdns_processes:
                try:
                    process.terminate()
                except OSError:
                    pass

    @staticmethod
    def _finish_mdns_browser(process: subprocess.Popen) -> str:
        if process.poll() is None:
            process.terminate()
        try:
            output, _ = process.communicate(timeout=0.3)
        except subprocess.TimeoutExpired:
            process.kill()
            output, _ = process.communicate(timeout=0.3)
        return output

    def discover_system_adb_mdns_services(self, *, timeout: float = 1.2) -> SystemMdnsDiscovery:
        """Browse both local TLS service types concurrently under one deadline."""
        executable = shutil.which("dns-sd", path=self.env.get("PATH", os.defpath))
        if executable is None and self.system == "darwin" and Path("/usr/bin/dns-sd").is_file():
            executable = "/usr/bin/dns-sd"
        if executable is None:
            return SystemMdnsDiscovery(False)
        processes: list[subprocess.Popen] = []
        services = []
        deadline = time.monotonic() + max(0.1, min(timeout, 5))
        try:
            # Hold the registration lock through spawn so shutdown cannot miss a child.
            with self._mdns_lock:
                if self._mdns_closed:
                    return SystemMdnsDiscovery(False)
                for service_type in TLS_SERVICE_TYPES:
                    process = subprocess.Popen(
                        [executable, "-B", service_type, "local."],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                    )
                    processes.append(process)
                    self._mdns_processes.add(process)
            for process in processes:
                try:
                    output, _ = process.communicate(timeout=max(0.001, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    # communicate() after terminate preserves the buffered partial output.
                    output = self._finish_mdns_browser(process)
                else:
                    # A browser normally runs until stopped. An early failure is not evidence.
                    if process.returncode != 0:
                        return SystemMdnsDiscovery(False)
                if re.search(r"\bError code\s+-?\d+", output):
                    return SystemMdnsDiscovery(False)
                services.extend(parse_dns_sd_services(output))
            return SystemMdnsDiscovery(True, tuple(dict.fromkeys(services)))
        except (OSError, subprocess.SubprocessError):
            return SystemMdnsDiscovery(False)
        finally:
            for process in processes:
                try:
                    self._finish_mdns_browser(process)
                except (OSError, subprocess.SubprocessError):
                    # Still attempt cleanup of the other browser if this one failed.
                    try:
                        process.kill()
                        process.wait(timeout=0.3)
                    except (OSError, subprocess.SubprocessError):
                        pass
                with self._mdns_lock:
                    self._mdns_processes.discard(process)

    @property
    def executable_name(self) -> str:
        return "adb.exe" if self.system == "windows" else "adb"

    def resolve_adb(self, configured_path: str | None = None) -> Path:
        for candidate in self.discover_adb_paths(configured_path):
            return candidate

        searched = "\n".join(f"- {path}" for path in self._candidate_paths(configured_path))
        raise AdbNotFoundError(
            "未找到 adb。请安装 Android platform-tools，或通过 --adb-path / ADBPILOT_ADB 指定路径。\n"
            f"已检查路径：\n{searched}"
        )

    def _candidate_paths(self, configured_path: str | None) -> list[Path]:
        values: list[str | None] = [
            configured_path,
            self.env.get("ADBPILOT_ADB"),
            self.env.get("ANDROID_HOME") and str(Path(self.env["ANDROID_HOME"]) / "platform-tools" / self.executable_name),
            self.env.get("ANDROID_SDK_ROOT") and str(Path(self.env["ANDROID_SDK_ROOT"]) / "platform-tools" / self.executable_name),
            str(Path.cwd() / "tools" / "platform-tools" / self.executable_name),
            str(Path.cwd() / "platform-tools" / self.executable_name),
        ]
        values.extend(str(path) for path in self._packaged_adb_candidates())

        if self.system == "darwin":
            values.extend(
                [
                    "/opt/homebrew/bin/adb",
                    "/usr/local/bin/adb",
                    str(Path.home() / "Library" / "Android" / "sdk" / "platform-tools" / "adb"),
                ]
            )
        elif self.system == "linux":
            values.extend(
                [
                    "/usr/bin/adb",
                    "/usr/local/bin/adb",
                    str(Path.home() / "Android" / "Sdk" / "platform-tools" / "adb"),
                ]
            )
        elif self.system == "windows":
            local_app_data = self.env.get("LOCALAPPDATA")
            program_files = self.env.get("ProgramFiles")
            values.extend(
                [
                    local_app_data
                    and str(Path(local_app_data) / "Android" / "Sdk" / "platform-tools" / "adb.exe"),
                    program_files and str(Path(program_files) / "Android" / "platform-tools" / "adb.exe"),
                ]
            )

        return [Path(value).expanduser() for value in values if value]

    def discover_adb_paths(self, configured_path: str | None = None) -> list[Path]:
        """Return existing adb executables, keeping the first occurrence of each file."""
        found: list[Path] = []
        seen: set[Path] = set()
        for candidate in self._candidate_paths(configured_path):
            self._add_existing_adb(found, seen, candidate)

        for name in (self.executable_name, "adb"):
            path_hit = shutil.which(name)
            if path_hit:
                self._add_existing_adb(found, seen, Path(path_hit))

        self._add_existing_adb(found, seen, self._which_from_login_shell())
        return found

    def _add_existing_adb(self, found: list[Path], seen: set[Path], candidate: Path | None) -> None:
        if candidate is None or not candidate.exists() or not candidate.is_file():
            return
        try:
            identity = candidate.resolve()
        except OSError:
            identity = candidate
        if identity in seen:
            return
        seen.add(identity)
        found.append(candidate)

    def _which_from_login_shell(self) -> Path | None:
        if self.system != "darwin":
            return None

        shell_values = [
            self.env.get("SHELL"),
            "/bin/zsh",
            "/bin/bash",
        ]
        for shell in dict.fromkeys(value for value in shell_values if value):
            shell_path = Path(shell)
            if not shell_path.exists():
                continue
            try:
                result = subprocess.run(
                    [str(shell_path), "-lc", "command -v adb"],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
            except (OSError, subprocess.SubprocessError):
                continue

            adb_path = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
            if result.returncode == 0 and adb_path:
                candidate = Path(adb_path).expanduser()
                if candidate.exists() and candidate.is_file():
                    return candidate
        return None

    def _packaged_adb_candidates(self) -> list[Path]:
        roots: list[Path] = []
        frozen_root = getattr(sys, "_MEIPASS", None)
        if frozen_root:
            roots.append(Path(frozen_root))
        if getattr(sys, "frozen", False):
            executable = Path(sys.executable).resolve()
            roots.extend(
                [
                    executable.parent,
                    executable.parent.parent / "Resources",
                    executable.parent.parent / "Frameworks",
                ]
            )

        candidates: list[Path] = []
        arch = platform.machine().lower()
        arch_names = [arch]
        if arch in {"arm64", "aarch64"}:
            arch_names.extend(["darwin-arm64", "macos-arm64", "arm64"])
        elif arch in {"x86_64", "amd64"}:
            arch_names.extend(["darwin-x86_64", "macos-x86_64", "x86_64", "intel"])

        for root in roots:
            candidates.extend(
                [
                    root / "platform-tools" / self.executable_name,
                    root / "tools" / "platform-tools" / self.executable_name,
                ]
            )
            for arch_name in dict.fromkeys(arch_names):
                candidates.extend(
                    [
                        root / "platform-tools" / arch_name / self.executable_name,
                        root / "tools" / "platform-tools" / arch_name / self.executable_name,
                    ]
                )
        return candidates
