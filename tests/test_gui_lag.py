import queue
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from adbpilot.gui_lag import LagDiagnosticsTab
from adbpilot.lag_report import UNRESPONSIVE_MESSAGE
from adbpilot.models import Device


class FakeVar:
    def __init__(self):
        self.value = ""

    def set(self, value):
        self.value = value

    def get(self):
        return self.value


class GuiLagTests(unittest.TestCase):
    def make_tab(self):
        device = Device("test-phone", "device", {"transport_id": "7"})
        app = SimpleNamespace(
            _selected_serial=Mock(return_value=device.serial), device_transports=[device],
            _prepare_client_async=Mock(), _run_async=Mock(), result_queue=queue.Queue(),
        )
        tab = SimpleNamespace(
            app=app, control=None, status=FakeVar(), warning=FakeVar(), mark_status=FakeVar(),
            report_path=FakeVar(), snapshot_path=FakeVar(), start_button=Mock(), mark_button=Mock(), stop_button=Mock(),
        )
        tab._reset_controls = lambda: LagDiagnosticsTab._reset_controls(tab)
        return tab

    def test_start_rejects_offline_device_before_opening_save_dialog(self):
        tab = self.make_tab()
        tab.app.device_transports = []
        with patch("adbpilot.gui_lag.filedialog.asksaveasfilename") as save:
            LagDiagnosticsTab.start_recording(tab)
        save.assert_not_called()
        tab.app._prepare_client_async.assert_not_called()
        self.assertIn("选择在线设备", tab.status.get())

    def test_recording_keeps_target_and_displays_timeout_and_both_report_paths(self):
        tab = self.make_tab()
        client = Mock()
        with tempfile.TemporaryDirectory() as directory:
            report_path = Path(directory) / "report.json"
            snapshot = Path(directory) / "report.timeout-test.json"
            with patch("adbpilot.gui_lag.filedialog.asksaveasfilename", return_value=str(report_path)):
                LagDiagnosticsTab.start_recording(tab)
            tab.app._selected_serial.return_value = "different-phone"
            ready, _failed = tab.app._prepare_client_async.call_args.args
            ready(client)
            _label, record, done, _failed = tab.app._run_async.call_args.args

            def deliver_event():
                kind, _label, payload, callback = tab.app.result_queue.get_nowait()
                self.assertEqual(kind, "progress")
                callback(payload)

            def workflow(actual_client, actual_path, *, serial, expected_transport_id, control, on_event):
                self.assertIs(actual_client, client)
                self.assertEqual(actual_path, report_path)
                self.assertEqual((serial, expected_transport_id), ("test-phone", "7"))
                control.set_recording(True)
                on_event("started", "")
                deliver_event()
                self.assertIn("test-phone", tab.status.get())
                LagDiagnosticsTab.mark(tab)
                self.assertEqual(tab.mark_status.get(), "卡顿标记 1 次")
                on_event("unresponsive", "")
                deliver_event()
                self.assertIn(UNRESPONSIVE_MESSAGE, tab.warning.get())
                on_event("checkpoint_saved", str(snapshot))
                deliver_event()
                self.assertEqual(tab.snapshot_path.get(), str(snapshot))
                self.assertEqual(tab.report_path.get(), "")
                LagDiagnosticsTab.stop_recording(tab)
                self.assertTrue(control.cancelled.is_set())
                return {"marks": [1.2], "stop_reason": "interrupted"}

            with patch("adbpilot.gui_lag.lag.record_to_path", side_effect=workflow):
                done(record())
            self.assertEqual(tab.report_path.get(), str(report_path))
            self.assertEqual(tab.snapshot_path.get(), str(snapshot))
            self.assertIsNone(tab.control)


if __name__ == "__main__":
    unittest.main()
