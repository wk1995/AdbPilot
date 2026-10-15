"""Reusable Python dependency download and install.

Features describe the requirement they need. This module owns pip download
and install so later screens can reuse the same flow.
"""

from __future__ import annotations

import re
import subprocess
import sys
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from .errors import AdbPilotError


ProgressCallback = Callable[[str], None]
_PROGRESS_LINE = re.compile(r"^Progress (\d+) of (\d+)$")
_DOWNLOAD_LINE = re.compile(r"^Downloading\s+(\S+?)(?:\s+\(([^)]+)\))?$")
_PHASE_LINE = re.compile(r"^(Collecting|Downloading|Installing|Successfully installed)\b")


class DependencyError(AdbPilotError):
    """Raised when a Python dependency cannot be downloaded or installed."""


@dataclass(frozen=True)
class DependencyRequest:
    """A pip requirement another feature wants available in the current runtime."""

    requirement: str
    import_names: tuple[str, ...] = ()


def missing_imports(import_names: Sequence[str]) -> tuple[str, ...]:
    missing: list[str] = []
    for name in import_names:
        try:
            __import__(name)
        except ImportError:
            missing.append(name)
    return tuple(missing)


def format_pip_progress(line: str, state: dict[str, int]) -> str | None:
    """Turn one pip output line into a user-facing download or install update."""

    text = line.strip()
    if not text or text.startswith(("Requirement already", "Looking in indexes", "WARNING")):
        return None

    progress = _PROGRESS_LINE.match(text)
    if progress:
        current = int(progress.group(1))
        total = int(progress.group(2))
        installing = state.get("install_seen", 0) > 0
        if total > 0:
            percent = min(100, int(current * 100 / total))
            if installing:
                return f"正在安装 {percent}%（{current} / {total}）"
            return f"下载进度 {percent}%（{_format_bytes(current)} / {_format_bytes(total)}）"
        if installing:
            return f"正在安装 {current}"
        return f"下载进度 {_format_bytes(current)}"

    if text.startswith("Installing collected packages"):
        state["install_seen"] = state.get("install_seen", 0) + 1
        return "开始安装"
    if text.startswith("Successfully installed"):
        return "安装完成"
    if state.get("install_seen", 0) and text.startswith("Installing "):
        name = text.removeprefix("Installing ").split("...", 1)[0].strip()
        return f"正在安装 {name}" if name else "正在安装"
    if text.startswith("Downloading "):
        match = _DOWNLOAD_LINE.match(text)
        name = match.group(1) if match else text.removeprefix("Downloading ").strip()
        size = match.group(2) if match else ""
        return f"正在下载 {name}" + (f"（{size}）" if size else "")
    if text.startswith("Collecting "):
        name = text.removeprefix("Collecting ").split(" ", 1)[0].strip()
        return f"准备下载 {name}" if name else None
    if _PHASE_LINE.match(text):
        return text
    return None


def _format_bytes(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def install_dependency(
    request: DependencyRequest,
    *,
    progress: ProgressCallback | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> str:
    """Download and install one pip requirement into the current interpreter."""

    requirement = request.requirement.strip()
    if not requirement:
        raise DependencyError("依赖名称为空")

    if request.import_names and not missing_imports(request.import_names):
        if progress:
            progress("依赖已安装")
        return f"{requirement} 已安装"

    command = [sys.executable, "-m", "pip", "install", "--progress-bar", "raw", requirement]
    if progress:
        progress(f"准备下载 {requirement}")
    run = runner or _run_pip
    try:
        completed = run(command, progress=progress, timeout=180)
    except (OSError, subprocess.SubprocessError) as exc:
        raise DependencyError(f"安装失败：{exc}") from exc

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "pip 安装失败").strip()
        raise DependencyError(detail)

    if request.import_names:
        import importlib

        for name in request.import_names:
            importlib.invalidate_caches()
            sys.modules.pop(name, None)
        still_missing = missing_imports(request.import_names)
        if still_missing:
            raise DependencyError(f"安装后仍无法导入：{', '.join(still_missing)}")

    if progress:
        progress("安装完成")
    return f"{requirement} 安装完成"


def _run_pip(
    command: list[str],
    *,
    progress: ProgressCallback | None = None,
    timeout: int = 180,
) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    output: list[str] = []
    state = {"install_seen": 0}

    def read_output() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            output.append(line)
            message = format_pip_progress(line, state)
            if message and progress:
                progress(message)

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        reader.join(timeout=2)
        raise
    reader.join(timeout=2)
    text = "".join(output)
    return subprocess.CompletedProcess(command, returncode, text, "")
