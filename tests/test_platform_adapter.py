import unittest
import subprocess
from pathlib import Path
from unittest.mock import patch

from adbpilot.platform_adapter import PlatformAdapter


class PlatformAdapterTests(unittest.TestCase):
    def test_configured_path_takes_priority(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            adb = Path(tmp) / "adb"
            adb.write_text("", encoding="utf-8")

            adapter = PlatformAdapter(env={})

            self.assertEqual(adapter.resolve_adb(str(adb)), adb)

    def test_android_home_candidate(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            executable = "adb.exe" if PlatformAdapter().executable_name == "adb.exe" else "adb"
            adb = root / "platform-tools" / executable
            adb.parent.mkdir()
            adb.write_text("", encoding="utf-8")

            adapter = PlatformAdapter(env={"ANDROID_HOME": str(root)})

            self.assertEqual(adapter.resolve_adb(), Path(adb))

    def test_packaged_adb_candidate_from_pyinstaller_root(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            executable = PlatformAdapter().executable_name
            adb = root / "platform-tools" / executable
            adb.parent.mkdir()
            adb.write_text("", encoding="utf-8")

            adapter = PlatformAdapter(env={})
            with patch("sys._MEIPASS", str(root), create=True):
                self.assertEqual(adapter.resolve_adb(), adb)

    def test_macos_login_shell_path_is_used_when_gui_path_is_minimal(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shell = root / "zsh"
            shell.write_text("", encoding="utf-8")
            adb = root / "platform-tools" / "adb"
            adb.parent.mkdir()
            adb.write_text("", encoding="utf-8")

            adapter = PlatformAdapter(env={"SHELL": str(shell)})
            completed = subprocess.CompletedProcess([str(shell), "-lc", "command -v adb"], 0, stdout=f"{adb}\n", stderr="")
            with patch("adbpilot.platform_adapter.platform.system", return_value="Darwin"), patch(
                "adbpilot.platform_adapter.shutil.which", return_value=None
            ), patch("adbpilot.platform_adapter.subprocess.run", return_value=completed):
                adapter.system = "darwin"
                self.assertEqual(adapter.resolve_adb(), adb)


if __name__ == "__main__":
    unittest.main()
