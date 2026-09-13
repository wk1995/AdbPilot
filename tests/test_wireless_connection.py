import queue
import subprocess
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from adbpilot.client import AdbClient
from adbpilot.errors import AdbCommandError, AdbNotFoundError
from adbpilot.gui import AdbPilotGui


class WirelessClientTests(unittest.TestCase):
    def setUp(self):
        with patch("adbpilot.client.PlatformAdapter.resolve_adb", return_value=Path("/test/adb")):
            self.client = AdbClient(timeout=7)

    def test_accepts_new_and_existing_connections(self):
        for output in ("connected to localhost:5555", "already connected to localhost:5555"):
            with self.subTest(output=output), patch("adbpilot.client.subprocess.run") as run:
                run.return_value = subprocess.CompletedProcess([], 0, output + "\n", "")
                self.assertEqual(self.client.connect("localhost:5555"), output)
                self.assertEqual(run.call_args.kwargs["timeout"], 7)

    def test_zero_exit_code_does_not_hide_connection_failures(self):
        for output in ("failed to connect: Connection refused", "cannot connect: No route to host", ""):
            with self.subTest(output=output), patch("adbpilot.client.subprocess.run") as run:
                run.return_value = subprocess.CompletedProcess([], 0, output, "")
                with self.assertRaises(AdbCommandError) as raised:
                    self.client.connect("localhost:5555")
                self.assertEqual(raised.exception.stdout, output)

    def test_nonzero_failure_is_preserved(self):
        with patch("adbpilot.client.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess([], 1, "", "connection refused")
            with self.assertRaises(AdbCommandError) as raised:
                self.client.connect("localhost:5555")
            self.assertEqual(raised.exception.returncode, 1)


class Value:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class WirelessGuiTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.client.connect.return_value = "connected to localhost:5555"
        self.gui = SimpleNamespace(
            connect_addr_var=Value("localhost:5555"),
            wireless_status_var=Value(),
            status_var=Value(),
            wireless_busy=False,
            connect_button=Mock(),
            disconnect_button=Mock(),
            _get_client=Mock(return_value=self.client),
            _append_output=Mock(),
            refresh_devices=Mock(),
            after=Mock(),
            result_queue=queue.Queue(),
        )
        for name in ("connect_device", "disconnect_device", "_set_wireless_busy",
                     "_run_wireless_action", "_run_async", "_process_results"):
            setattr(self.gui, name, getattr(AdbPilotGui, name).__get__(self.gui))
        thread_patch = patch("adbpilot.gui.threading.Thread")
        self.thread = thread_patch.start()
        self.addCleanup(thread_patch.stop)
        error_patch = patch("adbpilot.gui.messagebox.showerror")
        error_patch.start()
        self.addCleanup(error_patch.stop)

    def finish_worker(self):
        self.thread.call_args.kwargs["target"]()
        self.gui._process_results()

    def test_connect_shows_progress_blocks_repeated_clicks_and_refreshes(self):
        self.gui.connect_device()
        self.assertIn("正在连接", self.gui.wireless_status_var.get())
        self.gui.connect_button.configure.assert_called_with(state=tk.DISABLED, text="连接中…")
        self.gui.connect_device()
        self.gui.disconnect_device()
        self.assertEqual(self.thread.call_count, 1)
        self.finish_worker()
        self.client.connect.assert_called_once_with("localhost:5555")
        self.assertEqual(self.gui.wireless_status_var.get(), "连接成功：localhost:5555")
        self.assertFalse(self.gui.wireless_busy)
        self.gui.connect_button.configure.assert_called_with(state=tk.NORMAL, text="连接")
        self.gui.refresh_devices.assert_called_once()

    def test_connect_failure_is_visible_and_allows_retry(self):
        self.client.connect.side_effect = AdbCommandError("Connection refused", 0)
        self.gui.connect_device()
        self.finish_worker()
        self.assertIn("连接失败：Connection refused", self.gui.wireless_status_var.get())
        self.assertFalse(self.gui.wireless_busy)
        self.gui.refresh_devices.assert_not_called()
        self.gui.connect_device()
        self.assertEqual(self.thread.call_count, 2)

    def test_timeout_is_visible_and_restores_buttons(self):
        self.client.connect.side_effect = subprocess.TimeoutExpired(["adb", "connect"], 7)
        self.gui.connect_device()
        self.finish_worker()
        self.assertIn("操作超时（7 秒）", self.gui.wireless_status_var.get())
        self.assertFalse(self.gui.wireless_busy)
        self.gui.disconnect_button.configure.assert_called_with(state=tk.NORMAL, text="断开")

    def test_missing_adb_is_visible_without_starting_worker(self):
        self.gui._get_client.side_effect = AdbNotFoundError("未找到 adb")
        self.gui.connect_device()
        self.assertEqual(self.gui.wireless_status_var.get(), "连接失败：未找到 adb")
        self.thread.assert_not_called()
        self.assertFalse(self.gui.wireless_busy)

    def test_empty_address_is_rejected(self):
        self.gui.connect_addr_var.set(" ")
        with patch("adbpilot.gui.messagebox.showwarning"):
            self.gui.connect_device()
        self.assertEqual(self.gui.wireless_status_var.get(), "请输入 host:port 地址")
        self.thread.assert_not_called()

    def test_disconnect_shows_progress_then_refreshes_devices(self):
        self.client.disconnect.return_value = "disconnected localhost:5555"
        self.gui.disconnect_device()
        self.assertIn("正在断开", self.gui.wireless_status_var.get())
        self.finish_worker()
        self.client.disconnect.assert_called_once_with("localhost:5555")
        self.assertEqual(self.gui.wireless_status_var.get(), "断开成功：localhost:5555")
        self.assertFalse(self.gui.wireless_busy)
        self.gui.refresh_devices.assert_called_once()


if __name__ == "__main__":
    unittest.main()
