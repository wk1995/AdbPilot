"""Check release completeness and mark successful desktop uploads."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

from bump_version import PLATFORMS, VERSION_RE


def package_names(platform: str, version: str) -> set[str]:
    if platform not in PLATFORMS or not VERSION_RE.fullmatch(version):
        raise ValueError(f"Invalid platform version: {platform} {version}")
    architectures = ("32", "64") if platform == "windows" else ("x86_64", "arm64")
    return {f"AdbPilot-{platform}-{version}-{arch}.zip" for arch in architectures}


def marker_name(platform: str, version: str) -> str:
    return f"AdbPilot-{platform}-{version}-complete.json"


def get_release(platform: str, version: str) -> dict | None:
    package_names(platform, version)
    repository = os.environ["GH_REPO"]
    result = subprocess.run(
        ["gh", "api", f"repos/{repository}/releases/tags/{platform}-v{version}"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode:
        # A missing release is recoverable; auth/network/server errors must
        # stop the plan instead of being mistaken for an incomplete release.
        try:
            missing = str(json.loads(result.stdout).get("status")) == "404"
        except (ValueError, AttributeError):
            missing = False
        if missing:
            return None
        raise RuntimeError(f"Cannot inspect {platform}-v{version}: {result.stderr.strip()}")
    return json.loads(result.stdout)


def uploaded_names(release: dict | None) -> set[str]:
    if not release or release.get("draft", False):
        return set()
    return {
        asset["name"] for asset in release.get("assets", [])
        if asset.get("state") == "uploaded" and asset.get("size", 0) > 0
    }


def github_release_complete(platform: str, version: str) -> bool:
    expected = package_names(platform, version) | {marker_name(platform, version)}
    return expected.issubset(uploaded_names(get_release(platform, version)))


def mark_complete(platform: str, version: str, source_sha: str) -> None:
    packages = package_names(platform, version)
    if not packages.issubset(uploaded_names(get_release(platform, version))):
        raise RuntimeError(f"Cannot mark {platform}-v{version} complete: release packages are missing")
    # Called only after every architecture's release AND Actions uploads pass.
    marker = {
        "platform": platform, "version": version, "source_sha": source_sha,
        "run_id": os.environ["GITHUB_RUN_ID"],
        "run_attempt": os.environ["GITHUB_RUN_ATTEMPT"],
        "packages": sorted(packages),
    }
    with tempfile.TemporaryDirectory(prefix="adbpilot-release-") as directory:
        path = Path(directory) / marker_name(platform, version)
        path.write_text(json.dumps(marker, indent=2) + "\n", encoding="utf-8")
        subprocess.run(
            ["gh", "release", "upload", f"{platform}-v{version}", str(path),
             "--repo", os.environ["GH_REPO"], "--clobber"], check=True,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=PLATFORMS, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--source-sha", required=True)
    args = parser.parse_args()
    mark_complete(args.platform, args.version, args.source_sha)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
