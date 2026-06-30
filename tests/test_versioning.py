import re
import unittest
from pathlib import Path

from adbpilot import __version__


class VersioningTests(unittest.TestCase):
    def test_package_version_is_003(self):
        self.assertEqual(__version__, "0.0.3")

    def test_platform_versions_are_independent_files(self):
        version_re = re.compile(r"^\d+\.\d+\.\d+$")
        windows_version = Path("packaging/windows/version.txt").read_text(encoding="utf-8").strip()
        macos_version = Path("packaging/macos/version.txt").read_text(encoding="utf-8").strip()

        self.assertRegex(windows_version, version_re)
        self.assertRegex(macos_version, version_re)

    def test_windows_version_resource_matches_package_version(self):
        windows_version = Path("packaging/windows/version.txt").read_text(encoding="utf-8").strip()
        version_file = Path("packaging/windows/file_version_info.txt")
        text = version_file.read_text(encoding="utf-8")
        major, minor, patch = windows_version.split(".")

        self.assertIn(f"FileVersion', '{windows_version}'", text)
        self.assertIn(f"ProductVersion', '{windows_version}'", text)
        self.assertIn(f"filevers=({major}, {minor}, {patch}, 0)", text)


if __name__ == "__main__":
    unittest.main()
