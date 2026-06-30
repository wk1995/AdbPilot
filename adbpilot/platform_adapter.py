"""Platform-specific adb discovery."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from .errors import AdbNotFoundError


class PlatformAdapter:
    """Finds an adb executable without making assumptions about the host OS."""

    def __init__(self, env: dict[str, str] | None = None) -> None:
        self.env = env or os.environ
        self.system = platform.system().lower()

    @property
    def executable_name(self) -> str:
        return "adb.exe" if self.system == "windows" else "adb"

    def resolve_adb(self, configured_path: str | None = None) -> Path:
        candidates = list(self._candidate_paths(configured_path))
        for candidate in candidates:
            if candidate and candidate.exists() and candidate.is_file():
                return candidate

        path_hit = shutil.which(self.executable_name) or shutil.which("adb")
        if path_hit:
            return Path(path_hit)

        shell_path_hit = self._which_from_login_shell()
        if shell_path_hit:
            return shell_path_hit

        searched = "\n".join(f"- {path}" for path in candidates)
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
