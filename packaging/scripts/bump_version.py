"""Bump and synchronize one desktop platform application version."""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
PLATFORMS = ("windows", "macos")


def version_file(platform: str, root: Path = ROOT) -> Path:
    if platform not in PLATFORMS:
        raise ValueError(f"Unsupported platform: {platform}")
    return root / "packaging" / platform / "version.txt"


def read_version(platform: str, root: Path = ROOT) -> str:
    version = version_file(platform, root).read_text(encoding="utf-8").strip()
    if not VERSION_RE.match(version):
        raise ValueError(f"Unsupported {platform} version format: {version}")
    return version


def bump_patch(version: str) -> str:
    if not VERSION_RE.match(version):
        raise ValueError(f"Unsupported version format: {version}")
    major, minor, patch = [int(part) for part in version.split(".")]
    return f"{major}.{minor}.{patch + 1}"


def replace_text(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    updated = text.replace(old, new)
    if updated == text:
        raise RuntimeError(f"No version text replaced in {path}")
    path.write_text(updated, encoding="utf-8")


def replace_readme_platform_version(platform_label: str, new_version: str, root: Path = ROOT) -> None:
    path = root / "readme.md"
    if not path.exists():
        return

    text = path.read_text(encoding="utf-8")
    pattern = re.compile(rf"(\| {re.escape(platform_label)} \| )\d+\.\d+\.\d+( \|)")
    updated, count = pattern.subn(rf"\g<1>{new_version}\2", text, count=1)
    if count == 0:
        raise RuntimeError(f"No {platform_label} version row replaced in {path}")
    path.write_text(updated, encoding="utf-8")


def sync_windows_version(old_version: str, new_version: str, root: Path = ROOT) -> None:
    old_tuple = tuple(int(part) for part in old_version.split("."))
    new_tuple = tuple(int(part) for part in new_version.split("."))

    replace_text(root / "packaging" / "windows" / "README.md", old_version, new_version)

    version_resource = root / "packaging" / "windows" / "file_version_info.txt"
    text = version_resource.read_text(encoding="utf-8")
    text = text.replace(
        f"filevers=({old_tuple[0]}, {old_tuple[1]}, {old_tuple[2]}, 0)",
        f"filevers=({new_tuple[0]}, {new_tuple[1]}, {new_tuple[2]}, 0)",
    )
    text = text.replace(
        f"prodvers=({old_tuple[0]}, {old_tuple[1]}, {old_tuple[2]}, 0)",
        f"prodvers=({new_tuple[0]}, {new_tuple[1]}, {new_tuple[2]}, 0)",
    )
    text = text.replace(old_version, new_version)
    version_resource.write_text(text, encoding="utf-8")
    replace_readme_platform_version("Windows", new_version, root)


def sync_macos_version(old_version: str, new_version: str, root: Path = ROOT) -> None:
    replace_text(root / "packaging" / "macos" / "README.md", old_version, new_version)
    replace_readme_platform_version("macOS", new_version, root)


def sync_version(platform: str, new_version: str, root: Path = ROOT) -> None:
    if platform not in PLATFORMS:
        raise ValueError(f"Unsupported platform: {platform}")
    if not VERSION_RE.match(new_version):
        raise ValueError(f"Unsupported version format: {new_version}")

    old_version = read_version(platform, root)
    version_file(platform, root).write_text(f"{new_version}\n", encoding="utf-8")

    if platform == "windows":
        sync_windows_version(old_version, new_version, root)
    elif platform == "macos":
        sync_macos_version(old_version, new_version, root)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=PLATFORMS, required=True)
    parser.add_argument("--set-version", help="Set an explicit semantic version.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    new_version = args.set_version or bump_patch(read_version(args.platform))
    sync_version(args.platform, new_version)

    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with Path(github_output).open("a", encoding="utf-8") as output:
            output.write(f"version={new_version}\n")

    print(new_version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
