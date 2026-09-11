"""Resolve desktop builds and bump only business code not yet versioned."""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

from bump_version import PLATFORMS, ROOT, bump_patch, read_version, sync_version


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True,
    ).stdout.strip()


def is_business_file(path: str, platform: str) -> bool:
    """Runtime sources/assets count; build machinery and documentation do not."""
    if platform not in PLATFORMS:
        raise ValueError(f"Unsupported platform: {platform}")
    parts = Path(path).parts
    if not path.startswith(("adbpilot/", "packaging/entrypoints/")):
        return False
    if any(part in {"__pycache__", "tests", "docs"} for part in parts):
        return False
    if Path(path).suffix.lower() in {".md", ".rst", ".pyc", ".pyo"}:
        return False
    if path == "adbpilot/_packaged_version.py":
        return False
    if path == "adbpilot/macos_floating_helper.swift" or path.startswith("adbpilot/macos/"):
        return platform == "macos"
    if path.startswith("adbpilot/windows/"):
        return platform == "windows"
    return True


def release_baseline(platform: str, root: Path) -> str:
    tag = f"refs/tags/{platform}-v{read_version(platform, root)}"
    exists = subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", tag], cwd=root, check=False,
    )
    if exists.returncode == 0:
        baseline = git(root, "rev-parse", f"{tag}^{{commit}}")
        # A tag from a different history must never silently suppress a bump.
        git(root, "merge-base", "--is-ancestor", baseline, "HEAD")
        return baseline
    if exists.returncode != 1:
        raise RuntimeError(f"Cannot check release tag: {tag}")

    # Recover a run interrupted after committing the version but before tagging.
    # Also provides the baseline for a platform that has no release tag yet.
    baseline = git(root, "log", "-1", "--format=%H", "--", f"packaging/{platform}/version.txt")
    if not baseline:
        raise RuntimeError(f"No committed version baseline for {platform}")
    return baseline


def resolve_release(event: str, selected: set[str], root: Path = ROOT) -> dict[str, str]:
    if event not in {"workflow_dispatch", "pull_request_target"}:
        raise ValueError(f"Unsupported release event: {event}")
    if not selected.issubset(PLATFORMS):
        raise ValueError(f"Unsupported platforms: {selected}")

    outputs = {}
    for platform in PLATFORMS:
        version = read_version(platform, root)
        changed = False
        if event != "workflow_dispatch" or platform in selected:
            baseline = release_baseline(platform, root)
            # Disable rename detection so moving code out of the business tree
            # still counts as a deletion, including paths containing newlines.
            paths = git(root, "diff", "--name-only", "--no-renames", "-z", baseline, "HEAD").split("\0")
            changed = any(is_business_file(path, platform) for path in paths if path)
        build = platform in selected if event == "workflow_dispatch" else changed
        if changed:
            version = bump_patch(version)
        outputs[f"{platform}_build"] = str(build).lower()
        outputs[f"{platform}_bump"] = str(changed).lower()
        outputs[f"{platform}_version"] = version
        outputs[f"{platform}_tag"] = f"{platform}-v{version}"
    return outputs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", required=True, choices=("workflow_dispatch", "pull_request_target"))
    parser.add_argument("--windows", choices=("true", "false"), default="false")
    parser.add_argument("--macos", choices=("true", "false"), default="false")
    parser.add_argument("--apply", action="store_true", help="Apply the resolved version changes.")
    args = parser.parse_args()
    selected = {platform for platform in PLATFORMS if getattr(args, platform) == "true"}
    outputs = resolve_release(args.event, selected)
    if args.apply:
        for platform in PLATFORMS:
            if outputs[f"{platform}_bump"] == "true":
                sync_version(platform, outputs[f"{platform}_version"])
    text = "".join(f"{key}={value}\n" for key, value in outputs.items())
    if os.environ.get("GITHUB_OUTPUT"):
        with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
            output.write(text)
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
