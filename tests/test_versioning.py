import unittest
from pathlib import Path

from adbpilot import __version__


class VersioningTests(unittest.TestCase):
    def test_desktop_version_is_001(self):
        self.assertEqual(__version__, "0.0.3")

    def test_windows_version_resource_matches_package_version(self):
        version_file = Path("packaging/windows/file_version_info.txt")
        text = version_file.read_text(encoding="utf-8")

        self.assertIn("FileVersion', '0.0.3'", text)
        self.assertIn("ProductVersion', '0.0.3'", text)
        self.assertIn("filevers=(0, 0, 1, 0)", text)


if __name__ == "__main__":
    unittest.main()
