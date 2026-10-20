import json
import os
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from adbpilot.client import AdbClient
from adbpilot import history as connection_history
from adbpilot.models import Device


class HistoryStoreTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="adbpilot-history-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.env = patch.dict(os.environ, {"ADBPILOT_HOME": str(self.root)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.paths = patch.multiple(
            connection_history,
            history_path=lambda: self.root / "connections.json",
            log_path=lambda: self.root / "adbpilot.log",
        )
        self.paths.start()
        self.addCleanup(self.paths.stop)

    def test_same_device_updates_one_row_and_appends_log(self):
        moment = datetime.fromisoformat("2026-10-20T16:28:03+08:00")
        connection_history.record_event(
            "connect",
            source="cli",
            ok=False,
            address="192.168.31.39:39973",
            message="failed to connect",
            now=moment,
        )
        connection_history.record_event(
            "seen",
            source="gui",
            ok=True,
            address="192.168.31.39:39973",
            serial="adb-example._adb-tls-connect._tcp",
            state="device",
            model="2410DPN6CC",
            message="发现设备",
            now=moment,
        )

        devices = connection_history.load_devices()
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]["serial"], "adb-example._adb-tls-connect._tcp")
        self.assertEqual(devices[0]["model"], "2410DPN6CC")
        self.assertEqual(devices[0]["success_count"], 1)
        self.assertEqual(devices[0]["failure_count"], 1)
        self.assertEqual(len(devices[0]["events"]), 2)
        text = "\n".join(connection_history.recent_events())
        self.assertIn("connect cli fail", text)
        self.assertIn("seen gui ok", text)
        self.assertTrue(connection_history.log_path().is_file())

    def test_pairing_secret_is_not_written(self):
        connection_history.record_event("pair", source="gui", ok=False, address="192.168.1.8:37123", message="password=secret-code")
        stored = json.dumps(connection_history.load_devices(), ensure_ascii=False) + "\n" + "\n".join(connection_history.recent_events())
        self.assertNotIn("secret-code", stored)
        self.assertIn("已省略敏感内容", stored)


class ClientHistoryTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="adbpilot-history-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.paths = patch.multiple(
            connection_history,
            history_path=lambda: self.root / "connections.json",
            log_path=lambda: self.root / "adbpilot.log",
        )
        self.paths.start()
        self.addCleanup(self.paths.stop)
        with patch("adbpilot.client.PlatformAdapter.resolve_adb", return_value=Path("/test/adb")):
            self.client = AdbClient(timeout=7, history_source="cli")

    def test_failed_connect_is_recorded_without_raising_history_errors(self):
        with patch("adbpilot.client.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, "failed to connect to 192.168.1.8:5555\n", "")
            with self.assertRaises(Exception):
                self.client.connect("192.168.1.8:5555")
        devices = connection_history.load_devices()
        self.assertEqual(devices[0]["address"], "192.168.1.8:5555")
        self.assertFalse(devices[0]["events"][0]["ok"])
        self.assertIn("connect cli fail", "\n".join(connection_history.recent_events()))

    def test_device_appearance_and_departure_are_recorded(self):
        before = [Device("usb-1", "device", {"model": "Old"})]
        after = [Device("wifi-1:5555", "device", {"model": "Phone"})]
        messages = self.client.note_device_changes(before, after)
        self.assertEqual(len(messages), 2)
        serials = {device["serial"] for device in connection_history.load_devices()}
        self.assertEqual(serials, {"usb-1", "wifi-1:5555"})


if __name__ == "__main__":
    unittest.main()
