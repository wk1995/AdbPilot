import unittest
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


if __name__ == "__main__":
    unittest.main()
