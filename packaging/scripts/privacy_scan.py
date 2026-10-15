"""Fail CI when tracked text files contain local paths or device identifiers."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Skip generated trees and this scanner's own pattern definitions.
SKIP_PREFIXES = (
    "build/",
    "dist/",
    ".git/",
)
SKIP_FILES = {
    "packaging/scripts/privacy_scan.py",
    # Fixtures intentionally contain the patterns this scanner rejects.
    "tests/test_privacy_scan.py",
}

TEXT_SUFFIXES = {
    ".py",
    ".md",
    ".txt",
    ".yml",
    ".yaml",
    ".toml",
    ".json",
    ".sh",
    ".ps1",
    ".bat",
    ".swift",
    ".cfg",
    ".ini",
    ".xml",
}

PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "local home path",
        re.compile(r"(?:/Users|/home)/[A-Za-z0-9._-]+"),
    ),
    (
        "Windows user profile",
        re.compile(r"[A-Za-z]:\\Users\\[A-Za-z0-9._-]+"),
    ),
    (
        "email address",
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    ),
    (
        "ADB device serial",
        re.compile(r"\b(?:serial|device_serial|adb_serial)\s*[:=]\s*['\"]?[A-Za-z0-9]{8,}['\"]?", re.I),
    ),
)

# Public placeholders used in docs and examples.
ALLOWED_LINES = (
    "@example.com",
    "noreply.github.com",
    "/Users/me/",
    "/home/me/",
    r"C:\Users\me",
)


def tracked_files(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return [item.decode() for item in result.stdout.split(b"\0") if item]


def is_scannable(path: str) -> bool:
    if path in SKIP_FILES or path.startswith(SKIP_PREFIXES):
        return False
    return Path(path).suffix.lower() in TEXT_SUFFIXES


def findings_in(path: Path, relative: str) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return []

    found = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if any(allowed in line for allowed in ALLOWED_LINES):
            continue
        for name, pattern in PATTERNS:
            if pattern.search(line):
                found.append(f"{relative}:{line_number}: {name}")
    return found


def main() -> int:
    root = ROOT
    violations: list[str] = []
    for relative in tracked_files(root):
        if not is_scannable(relative):
            continue
        path = root / relative
        if path.is_file():
            violations.extend(findings_in(path, relative))

    if violations:
        print("Privacy scan failed. Remove local paths, emails, and device identifiers:", file=sys.stderr)
        for item in violations:
            print(f"  {item}", file=sys.stderr)
        return 1

    print("Privacy scan passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
