"""Bump and synchronize the desktop application version."""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


def read_version(root: Path = ROOT) -> str:
    text = (root / "adbpilot" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    if not match:
        raise RuntimeError("Could not find adbpilot.__version__")
    return match.group(1)


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


def sync_version(new_version: str, root: Path = ROOT) -> None:
    if not VERSION_RE.match(new_version):
        raise ValueError(f"Unsupported version format: {new_version}")

    old_version = read_version(root)
    old_tuple = tuple(int(part) for part in old_version.split("."))
    new_tuple = tuple(int(part) for part in new_version.split("."))

    for relative in (
        "adbpilot/__init__.py",
        "pyproject.toml",
        "readme.md",
        "packaging/windows/README.md",
        "packaging/windows/build_windows.ps1",
        "packaging/macos/README.md",
        "tests/test_versioning.py",
    ):
        replace_text(root / relative, old_version, new_version)

    version_resource = root / "packaging/windows/file_version_info.txt"
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--set-version", help="Set an explicit semantic version.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    new_version = args.set_version or bump_patch(read_version())
    sync_version(new_version)

    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with Path(github_output).open("a", encoding="utf-8") as output:
            output.write(f"version={new_version}\n")

    print(new_version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
