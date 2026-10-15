"""Privacy scanner rejects local paths and device identifiers."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


def load_scanner():
    path = Path(__file__).resolve().parents[1] / "packaging" / "scripts" / "privacy_scan.py"
    spec = importlib.util.spec_from_file_location("privacy_scan", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PrivacyScanTests(unittest.TestCase):
    def test_flags_home_path_email_and_serial(self) -> None:
        scanner = load_scanner()
        with tempfile.TemporaryDirectory() as directory:
            sample = Path(directory) / "notes.md"
            sample.write_text(
                "adb lives at /Users/alice/Library/Android/sdk/platform-tools/adb\n"
                "contact alice@company.test\n"
                "serial: ABCDEF1234567890\n",
                encoding="utf-8",
            )
            self.assertEqual(
                scanner.findings_in(sample, "notes.md"),
                [
                    "notes.md:1: local home path",
                    "notes.md:2: email address",
                    "notes.md:3: ADB device serial",
                ],
            )

    def test_allows_public_placeholders(self) -> None:
        scanner = load_scanner()
        with tempfile.TemporaryDirectory() as directory:
            sample = Path(directory) / "readme.md"
            sample.write_text(
                "export ADBPILOT_ADB=/Users/me/Library/Android/sdk/platform-tools/adb\n"
                "mail user@example.com\n",
                encoding="utf-8",
            )
            self.assertEqual(scanner.findings_in(sample, "readme.md"), [])


if __name__ == "__main__":
    unittest.main()
