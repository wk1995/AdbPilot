"""Download official platform-tools builds and switch to the unpacked adb."""

from __future__ import annotations

import platform
import re
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path

from .errors import AdbDownloadError


ProgressCallback = Callable[[str], None]
VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")
# Stable releases from the public platform-tools notes. 36.0.1 never left Canary.
ADB_VERSIONS = (
    "37.0.1",
    "37.0.0",
    "36.0.2",
    "36.0.0",
    "35.0.2",
    "35.0.1",
    "35.0.0",
    "34.0.5",
    "34.0.4",
    "33.0.3",
)


def platform_tools_host(system_name: str | None = None) -> str:
    name = (system_name or platform.system()).lower()
    if name == "darwin" or name == "macos":
        return "darwin"
    if name == "windows":
        return "windows"
    if name == "linux":
        return "linux"
    raise AdbDownloadError(f"当前系统没有对应的 adb 安装包：{name or '未知'}")


def platform_tools_url(version: str, system_name: str | None = None) -> str:
    cleaned = version.strip()
    if not VERSION_PATTERN.fullmatch(cleaned):
        raise AdbDownloadError("版本号需要写成 37.0.0 这种三段数字")
    host = platform_tools_host(system_name)
    return f"https://dl.google.com/android/repository/platform-tools_r{cleaned}-{host}.zip"


def default_download_root(source_file: str | Path | None = None) -> Path:
    """Use the source checkout when running from source, otherwise the app location."""
    if not getattr(sys, "frozen", False):
        anchor = Path(source_file or __file__).resolve()
        return anchor.parents[1]
    executable = Path(sys.executable).resolve()
    if platform.system().lower() == "darwin" and executable.parent.name == "MacOS":
        return executable.parents[2].parent
    return executable.parent


def adb_executable_name(system_name: str | None = None) -> str:
    name = (system_name or platform.system()).lower()
    return "adb.exe" if name == "windows" else "adb"


def version_directory(root: Path, version: str) -> Path:
    return root / "tools" / f"platform-tools-{version.strip()}"


def download_platform_tools(
    version: str,
    destination: Path,
    *,
    system_name: str | None = None,
    progress: ProgressCallback | None = None,
    opener: Callable[..., object] | None = None,
) -> Path:
    """Download one official build into destination and return its adb path."""
    cleaned = version.strip()
    url = platform_tools_url(cleaned, system_name)
    target_dir = version_directory(destination, cleaned)
    executable = target_dir / "platform-tools" / adb_executable_name(system_name)
    if executable.is_file():
        if progress:
            progress(f"{cleaned} 已在目录中")
        return executable

    if progress:
        progress(f"正在下载 {cleaned}")
    open_url = opener or urllib.request.urlopen
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="adbpilot-platform-tools-") as temp_dir:
        archive = Path(temp_dir) / "platform-tools.zip"
        try:
            with open_url(url, timeout=60) as response, archive.open("wb") as handle:
                total = int(response.headers.get("Content-Length") or 0)
                copied = 0
                last_percent = -1
                while True:
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
                    copied += len(chunk)
                    if progress and total:
                        percent = min(100, int(copied * 100 / total))
                        if percent != last_percent:
                            last_percent = percent
                            progress(f"下载进度 {percent}%")
        except (OSError, ValueError) as exc:
            raise AdbDownloadError(f"下载失败：{exc}") from exc

        if progress:
            progress("正在解压")
        target_dir.parent.mkdir(parents=True, exist_ok=True)
        try:
            # Publish only a fully extracted package, on the same filesystem.
            # A failed extraction must never become a reusable adb cache.
            with tempfile.TemporaryDirectory(prefix=".platform-tools-", dir=target_dir.parent) as staging_dir:
                staged = Path(staging_dir) / "package"
                with zipfile.ZipFile(archive) as package:
                    _extract_platform_tools(package, staged)
                if not (staged / "platform-tools" / adb_executable_name(system_name)).is_file():
                    raise AdbDownloadError("安装包里没有找到 adb")
                if target_dir.exists():
                    shutil.rmtree(target_dir)
                staged.replace(target_dir)
        except (OSError, zipfile.BadZipFile) as exc:
            raise AdbDownloadError(f"解压失败：{exc}") from exc

    if not executable.is_file():
        raise AdbDownloadError("安装包里没有找到 adb")
    if progress:
        progress("下载完成")
    return executable


def open_directory(path: Path) -> None:
    target = path.expanduser()
    target.mkdir(parents=True, exist_ok=True)
    system_name = platform.system().lower()
    if system_name == "darwin":
        command = ["open", str(target)]
    elif system_name == "windows":
        command = ["explorer", str(target)]
    else:
        command = ["xdg-open", str(target)]
    try:
        import subprocess

        subprocess.run(command, check=False, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        raise AdbDownloadError(f"无法打开目录：{exc}") from exc


def _extract_platform_tools(package: zipfile.ZipFile, target_dir: Path) -> None:
    if target_dir.exists():
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    root = target_dir.resolve()
    for member in package.infolist():
        destination = (target_dir / member.filename).resolve()
        if root != destination and root not in destination.parents:
            raise AdbDownloadError("安装包包含目标目录之外的文件")
        if member.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        with package.open(member) as source, destination.open("wb") as handle:
            shutil.copyfileobj(source, handle)
        mode = (member.external_attr >> 16) & 0o777
        if mode:
            destination.chmod(mode)
