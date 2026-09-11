import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "packaging" / "scripts"
sys.path.insert(0, str(SCRIPTS))
try:
    from bump_version import sync_version
    from resolve_release import is_business_file, resolve_release
finally:
    sys.path.pop(0)


class ReleaseResolutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.git("init", "-b", "master")
        self.git("config", "user.name", "Release tests")
        self.git("config", "user.email", "release-tests@example.com")
        for path in ("packaging/windows", "packaging/macos", "packaging/scripts"):
            shutil.copytree(ROOT / path, self.root / path)
        shutil.copyfile(ROOT / "readme.md", self.root / "readme.md")
        self.write("adbpilot/gui.py", "original\n")
        self.write("adbpilot/macos_floating_helper.swift", "original\n")
        self.commit()
        for platform in ("windows", "macos"):
            self.git("tag", self.tag(platform))

    def git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.root, check=True, capture_output=True, text=True,
        ).stdout.strip()

    def tag(self, platform):
        version = (self.root / f"packaging/{platform}/version.txt").read_text().strip()
        return f"{platform}-v{version}"

    def write(self, path, content):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    def commit(self):
        self.git("add", ".")
        self.git("commit", "-m", "[home][mac-min] test fixture")

    def resolve(self, event="workflow_dispatch", selected=None):
        if selected is None:
            selected = {"windows", "macos"}
        return resolve_release(event, selected, self.root)

    def test_manual_rebuild_uses_existing_versions(self):
        result = self.resolve()
        for platform in ("windows", "macos"):
            self.assertEqual(result[f"{platform}_build"], "true")
            self.assertEqual(result[f"{platform}_bump"], "false")
            self.assertEqual(result[f"{platform}_tag"], self.tag(platform))

    def test_non_business_changes_skip_automatic_builds(self):
        for path in (
            "readme.md", "tests/test_gui.py", ".github/workflows/release.yml",
            "packaging/windows/build_windows.ps1", "packaging/macos/AdbPilot.spec",
            "adbpilot/docs/guide.md", "packaging/entrypoints/README.md",
        ):
            self.write(path, "updated\n")
        self.commit()
        for event in ("workflow_dispatch", "pull_request_target"):
            result = self.resolve(event)
            for platform in ("windows", "macos"):
                self.assertEqual(result[f"{platform}_bump"], "false")
                self.assertEqual(result[f"{platform}_build"], str(event == "workflow_dispatch").lower())

    def test_common_business_change_bumps_both_platforms(self):
        self.write("adbpilot/gui.py", "changed\n")
        self.commit()
        for event in ("workflow_dispatch", "pull_request_target"):
            result = self.resolve(event)
            self.assertEqual(result["windows_bump"], "true")
            self.assertEqual(result["macos_bump"], "true")

    def test_macos_helper_only_bumps_macos(self):
        self.write("adbpilot/macos_floating_helper.swift", "changed\n")
        self.commit()
        result = self.resolve("pull_request_target")
        self.assertEqual(result["windows_build"], "false")
        self.assertEqual(result["windows_bump"], "false")
        self.assertEqual(result["macos_build"], "true")
        self.assertEqual(result["macos_bump"], "true")

    def test_unselected_platform_keeps_its_pending_business_change(self):
        self.write("adbpilot/gui.py", "changed\n")
        self.commit()
        result = self.resolve(selected={"windows"})
        self.assertEqual(result["windows_bump"], "true")
        self.assertEqual(result["macos_build"], "false")
        self.assertEqual(result["macos_bump"], "false")
        sync_version("windows", result["windows_version"], self.root)
        self.commit()
        self.git("tag", self.tag("windows"))
        result = self.resolve()
        self.assertEqual(result["windows_bump"], "false")
        self.assertEqual(result["macos_bump"], "true")

    def test_repeated_release_after_bump_does_not_bump_again(self):
        self.write("adbpilot/gui.py", "changed\n")
        self.commit()
        result = self.resolve()
        for platform in ("windows", "macos"):
            sync_version(platform, result[f"{platform}_version"], self.root)
        self.commit()
        for platform in ("windows", "macos"):
            self.git("tag", self.tag(platform))
        repeated = self.resolve()
        for platform in ("windows", "macos"):
            self.assertEqual(repeated[f"{platform}_version"], result[f"{platform}_version"])
            self.assertEqual(repeated[f"{platform}_bump"], "false")
        self.assertEqual(self.resolve("pull_request_target")["windows_build"], "false")

    def test_missing_tag_recovers_committed_version(self):
        self.write("adbpilot/gui.py", "changed\n")
        self.commit()
        version = self.resolve()["windows_version"]
        sync_version("windows", version, self.root)
        self.commit()
        result = self.resolve(selected={"windows"})
        self.assertEqual(result["windows_version"], version)
        self.assertEqual(result["windows_bump"], "false")
        self.write("adbpilot/gui.py", "changed again\n")
        self.commit()
        self.assertEqual(self.resolve()["windows_bump"], "true")

    def test_first_release_uses_version_commit_as_baseline(self):
        for platform in ("windows", "macos"):
            self.git("tag", "-d", self.tag(platform))
        self.assertEqual(self.resolve()["windows_bump"], "false")
        self.write("adbpilot/gui.py", "changed\n")
        self.commit()
        self.assertEqual(self.resolve()["windows_bump"], "true")

    def test_renaming_business_code_out_of_tree_counts_as_deletion(self):
        self.git("mv", "adbpilot/gui.py", "example.py")
        self.commit()
        self.assertEqual(self.resolve()["windows_bump"], "true")

    def test_reverted_business_change_does_not_bump(self):
        self.write("adbpilot/gui.py", "changed\n")
        self.commit()
        self.write("adbpilot/gui.py", "original\n")
        self.commit()
        self.assertEqual(self.resolve()["windows_bump"], "false")

    def test_tag_from_unrelated_history_fails(self):
        tag = self.tag("windows")
        self.git("checkout", "--orphan", "unrelated")
        self.write("unrelated.txt", "unrelated\n")
        self.commit()
        self.git("tag", "-f", tag)
        self.git("checkout", "master")
        with self.assertRaises(subprocess.CalledProcessError):
            self.resolve()

    def test_cli_applies_only_selected_platform_and_writes_outputs(self):
        self.write("packaging/entrypoints/adbpilot_gui.py", "changed\n")
        self.commit()
        original_macos = self.tag("macos")
        output = self.root / "outputs.txt"
        completed = subprocess.run(
            [sys.executable, str(self.root / "packaging/scripts/resolve_release.py"),
             "--event", "workflow_dispatch", "--windows", "true", "--apply"],
            cwd=self.root, env={**os.environ, "GITHUB_OUTPUT": str(output)},
            check=True, capture_output=True, text=True,
        )
        result = dict(line.split("=", 1) for line in output.read_text().splitlines())
        self.assertEqual(output.read_text(), completed.stdout)
        self.assertEqual(result["windows_bump"], "true")
        self.assertEqual(result["windows_tag"], self.tag("windows"))
        self.assertEqual(self.tag("macos"), original_macos)
        resource = (self.root / "packaging/windows/file_version_info.txt").read_text()
        self.assertIn(f"FileVersion', '{result['windows_version']}'", resource)

    def test_no_platform_selected_does_nothing(self):
        self.write("adbpilot/gui.py", "changed\n")
        self.commit()
        result = self.resolve(selected=set())
        for platform in ("windows", "macos"):
            self.assertEqual(result[f"{platform}_build"], "false")
            self.assertEqual(result[f"{platform}_bump"], "false")

    def test_business_file_rules(self):
        self.assertTrue(is_business_file("adbpilot/windows/helper.py", "windows"))
        self.assertFalse(is_business_file("adbpilot/windows/helper.py", "macos"))
        self.assertTrue(is_business_file("adbpilot/icons/device.png", "windows"))
        self.assertFalse(is_business_file("adbpilot/_packaged_version.py", "windows"))
        self.assertFalse(is_business_file("adbpilot/__pycache__/gui.pyc", "windows"))
        with self.assertRaises(ValueError):
            is_business_file("adbpilot/gui.py", "linux")


if __name__ == "__main__":
    unittest.main()
