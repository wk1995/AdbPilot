"""Exercise the production floating controller against real AppKit windows."""

import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


@unittest.skipUnless(sys.platform == "darwin" and shutil.which("swiftc"), "Requires macOS and Swift")
class MacosFloatingTests(unittest.TestCase):
    def test_refresh_preserves_normal_window_order(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "adbpilot/macos_floating_helper.swift").read_text()
        # Replace only the executable's stdin loop with a native regression harness.
        controller_source, separator, _ = source.partition("\nlet app = NSApplication.shared\n")
        self.assertTrue(separator)
        harness = (root / "tests/macos_floating_regression.swift").read_text()
        with TemporaryDirectory() as directory:
            main = Path(directory) / "main.swift"
            binary = Path(directory) / "floating-regression"
            main.write_text(controller_source + "\n" + harness)
            compiled = subprocess.run(
                ["swiftc", str(main), "-o", str(binary)], capture_output=True, text=True, timeout=90
            )
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("PASS", result.stdout)
