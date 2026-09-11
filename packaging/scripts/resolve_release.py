"""Resolve desktop builds and bump only business code not yet versioned."""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path
from typing import Callable

from bump_version import PLATFORMS, ROOT, bump_patch, read_version, sync_version
from release_status import github_release_complete


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


def tag_exists(platform: str, version: str, root: Path) -> bool:
    tag = f"refs/tags/{platform}-v{version}"
    exists = subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", tag], cwd=root, check=False,
    )
    if exists.returncode not in (0, 1):
        raise RuntimeError(f"Cannot check release tag: {tag}")
    return exists.returncode == 0


def business_changed(platform: str, baseline: str, root: Path) -> bool:
    # Renames out of the business tree must count as deletions.
    paths = git(root, "diff", "--name-only", "--no-renames", "-z", baseline, "HEAD").split("\0")
    return any(is_business_file(path, platform) for path in paths if path)


def validate_release_tag(platform: str, version: str, bump: bool, root: Path = ROOT) -> None:
    if not tag_exists(platform, version, root):
        return
    tag = f"refs/tags/{platform}-v{version}"
    if bump:
        raise RuntimeError(f"Refusing version bump: release tag {tag} already exists")
    if business_changed(platform, tag, root):
        raise RuntimeError(f"Refusing to reuse {tag}: business code differs from HEAD")


def release_baseline(platform: str, root: Path) -> str:
    version = read_version(platform, root)
    tag = f"refs/tags/{platform}-v{version}"
    if tag_exists(platform, version, root):
        baseline = git(root, "rev-parse", f"{tag}^{{commit}}")
        # A tag from a different history must never silently suppress a bump.
        git(root, "merge-base", "--is-ancestor", baseline, "HEAD")
        return baseline

    # Recover a run interrupted after committing the version but before tagging.
    # Also provides the baseline for a platform that has no release tag yet.
    baseline = git(root, "log", "-1", "--format=%H", "--", f"packaging/{platform}/version.txt")
    if not baseline:
        raise RuntimeError(f"No committed version baseline for {platform}")
    return baseline


def resolve_release(
    event: str, selected: set[str], root: Path = ROOT,
    release_complete: Callable[[str, str], bool] = github_release_complete,
) -> dict[str, str]:
    if event not in {"workflow_dispatch", "pull_request_target"}:
        raise ValueError(f"Unsupported release event: {event}")
    if not selected.issubset(PLATFORMS):
        raise ValueError(f"Unsupported platforms: {selected}")

    outputs = {}
    for platform in PLATFORMS:
        version = read_version(platform, root)
        changed = False
        build = False
        if event != "workflow_dispatch" or platform in selected:
            baseline = release_baseline(platform, root)
            changed = business_changed(platform, baseline, root)
            build = (
                event == "workflow_dispatch" or changed
                or not tag_exists(platform, version, root)
                or not release_complete(platform, version)
            )
        if changed:
            version = bump_patch(version)
        if build:
            validate_release_tag(platform, version, changed, root)
        outputs[f"{platform}_build"] = str(build).lower()
        outputs[f"{platform}_bump"] = str(changed).lower()
        outputs[f"{platform}_version"] = version
        outputs[f"{platform}_tag"] = f"{platform}-v{version}"
    return outputs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", choices=("workflow_dispatch", "pull_request_target"))
    parser.add_argument("--windows", choices=("true", "false"), default="false")
    parser.add_argument("--macos", choices=("true", "false"), default="false")
    parser.add_argument("--apply", action="store_true", help="Apply the resolved version changes.")
    parser.add_argument("--validate-tags", action="store_true", help="Recheck planned tags after fetching.")
    args = parser.parse_args()
    if args.validate_tags:
        for platform in PLATFORMS:
            if os.environ[f"{platform.upper()}_BUILD"] == "true":
                validate_release_tag(
                    platform, read_version(platform),
                    os.environ[f"{platform.upper()}_BUMP"] == "true",
                )
        return 0
    if args.event is None:
        parser.error("--event is required unless --validate-tags is used")
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
