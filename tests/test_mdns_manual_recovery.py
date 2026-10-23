import subprocess
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from adbpilot.client import AdbClient
from adbpilot.errors import AdbPilotError
from adbpilot.gui import AdbPilotGui
from adbpilot.mdns import MdnsRecoveryConfig, MdnsRecoveryController, MdnsRecoveryState, MdnsService
from adbpilot.models import Device


class Value:
    def __init__(self):
        self.value = ""

    def set(self, value):
        self.value = value

    def get(self):
        return self.value


class ManualMdnsRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.phone = Device("private-usb-serial", "device")
        self.client = Mock()
        self.client.mdns_recovery_devices.return_value = (self.phone,)
        self.client.mdns_recovery_services.return_value = (MdnsService("_adb-tls-connect._tcp", "192.0.2.8:5555"),)
        self.button = Mock()
        self.button.winfo_exists.return_value = True
        self.gui = SimpleNamespace(
            client=self.client, devices=[self.phone], adb_service_lock=threading.Lock(),
            mdns_stop=threading.Event(), mdns_recovery=MdnsRecoveryController(),
            mdns_manual_restart_running=False, mdns_restart_buttons=[self.button],
            mdns_recovery_blocked=False, mdns_recovery_notice_var=Value(),
            wireless_status_var=Value(), qr_pair_status_var=Value(), pairing_in_progress=False, wireless_busy=False,
            qr_pair_window=None, qr_pair_cancel_event=None, qr_pair_worker_done=None,
            qr_pair_session=0, _finish_qr_pairing=Mock(), _append_output=Mock(),
            _render_devices=Mock(), _run_async=Mock(),
        )
        self.gui._set_mdns_manual_restart_running = lambda value: AdbPilotGui._set_mdns_manual_restart_running(self.gui, value)
        self.gui._set_mdns_manual_status = lambda message: AdbPilotGui._set_mdns_manual_status(self.gui, message)
        self.gui._restart_and_redetect = lambda *args: AdbPilotGui._restart_and_redetect(self.gui, *args)
        self.gui._prepare_client_async = Mock(side_effect=lambda ready, failed, **kwargs: ready(self.client))
        self.gui.restart_adb_and_redetect = lambda: AdbPilotGui.restart_adb_and_redetect(self.gui)

    def start_manual(self, confirm=True):
        with patch("adbpilot.gui.messagebox.askyesno", return_value=confirm) as dialog:
            self.gui.restart_adb_and_redetect()
        return dialog

    def finish_manual(self):
        label, work, done, failed = self.gui._run_async.call_args.args
        self.assertEqual(label, "重启 ADB 并重新检测")
        result = work()
        done(result)
        return result, failed

    def test_device_protection_reason_is_visible_without_repeated_log_spam(self):
        for state in ("device", "offline", "unauthorized", "no permissions"):
            AdbPilotGui._update_mdns_recovery_notice(self.gui, [Device("private-usb-serial", state)])
            self.assertIn("已跳过自动恢复", self.gui.mdns_recovery_notice_var.get())
            self.assertIn("USB/Wi-Fi", self.gui.mdns_recovery_notice_var.get())
            self.assertNotIn("private-usb-serial", self.gui.mdns_recovery_notice_var.get())
        self.gui._append_output.assert_called_once()
        AdbPilotGui._update_mdns_recovery_notice(self.gui, [])
        self.assertFalse(self.gui.mdns_recovery_blocked)
        self.assertNotIn("已跳过", self.gui.mdns_recovery_notice_var.get())

    def test_disabled_auto_recovery_does_not_claim_device_protection(self):
        self.gui.mdns_recovery.config = MdnsRecoveryConfig(enabled=False)
        AdbPilotGui._update_mdns_recovery_notice(self.gui, [self.phone])
        self.assertIn("自动恢复已关闭", self.gui.mdns_recovery_notice_var.get())
        self.assertFalse(self.gui.mdns_recovery_blocked)
        self.gui._append_output.assert_not_called()

    def test_main_and_qr_controls_share_notice_and_disable_together(self):
        self.gui.mdns_restart_buttons = []
        buttons = [Mock(), Mock()]
        with patch("adbpilot.gui.ttk.Label") as label, patch("adbpilot.gui.ttk.Button", side_effect=buttons) as button:
            AdbPilotGui._build_mdns_recovery_controls(self.gui, Mock())
            AdbPilotGui._build_mdns_recovery_controls(self.gui, Mock())
        self.assertTrue(all(call.kwargs["textvariable"] is self.gui.mdns_recovery_notice_var for call in label.call_args_list))
        self.assertTrue(all(call.kwargs["text"] == "重启 ADB 并重新检测" for call in button.call_args_list))
        self.gui._set_mdns_manual_restart_running(True)
        for widget in buttons:
            widget.configure.assert_called_with(state="disabled")

    def test_declining_confirmation_has_no_restart_or_qr_side_effects(self):
        self.gui.qr_pair_cancel_event = threading.Event()
        self.start_manual(confirm=False)
        self.assertFalse(self.gui.mdns_manual_restart_running)
        self.assertFalse(self.gui.qr_pair_cancel_event.is_set())
        self.gui._prepare_client_async.assert_not_called()
        self.client.restart_current_server.assert_not_called()

    def test_confirmed_action_restarts_with_online_device_and_redetects(self):
        dialog = self.start_manual()
        self.assertIn("中断", dialog.call_args.args[1])
        self.assertTrue(self.gui.mdns_manual_restart_running)
        self.button.configure.assert_called_with(state="disabled")
        duplicate_dialog = self.start_manual()
        duplicate_dialog.assert_not_called()
        self.gui._run_async.assert_called_once()
        result, _ = self.finish_manual()
        self.assertEqual(result.status, "checked")
        self.assertTrue(result.restarted)
        self.client.restart_current_server.assert_called_once()
        self.client.mdns_recovery_devices.assert_called_once()
        self.client.mdns_recovery_services.assert_called_once()
        self.gui._render_devices.assert_called_once_with([self.phone], mark_disconnected=True, noisy=True)
        self.assertIn("1 台设备", self.gui.wireless_status_var.get())
        self.assertIn("1 个无线调试服务", self.gui.wireless_status_var.get())
        self.assertNotIn("private", self.gui.wireless_status_var.get())
        self.assertFalse(self.gui.mdns_manual_restart_running)
        self.button.configure.assert_called_with(state="normal")
        self.assertEqual(self.gui.mdns_recovery.state, MdnsRecoveryState.COOLDOWN)

    def test_restart_from_qr_popup_cancels_old_session_and_expires_qr(self):
        self.gui.qr_pair_window = Mock()
        self.gui.qr_pair_cancel_event = threading.Event()
        self.gui.qr_pair_worker_done = threading.Event()
        self.gui.qr_pair_worker_done.set()
        self.gui.qr_pair_restart_button = Mock()
        dialog = self.start_manual()
        self.assertIs(dialog.call_args.kwargs["parent"], self.gui.qr_pair_window)
        self.assertTrue(self.gui.qr_pair_cancel_event.is_set())
        self.assertEqual(self.gui.qr_pair_session, 1)
        self.gui._finish_qr_pairing.assert_called_once()
        self.gui.qr_pair_restart_button.configure.assert_called_with(state="disabled")
        self.finish_manual()
        self.gui.qr_pair_restart_button.configure.assert_called_with(state="normal")
        self.assertEqual(self.gui.qr_pair_status_var.get(), self.gui.wireless_status_var.get())
        self.assertIn("已重启并重新检测", self.gui.qr_pair_status_var.get())

    def test_worker_waits_for_old_qr_command_before_restarting(self):
        waiting = threading.Event()
        finished = threading.Event()
        qr_done = Mock()
        qr_done.wait.side_effect = lambda timeout: waiting.set() or finished.wait(timeout)
        results = []
        latest_done = threading.Event()
        latest_done.set()
        worker = threading.Thread(target=lambda: results.append(self.gui._restart_and_redetect(self.client, (latest_done, qr_done))))
        worker.start()
        try:
            self.assertTrue(waiting.wait(1))
            self.client.restart_current_server.assert_not_called()
        finally:
            finished.set()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(results[0].status, "checked")

    def test_manual_request_captures_workers_from_previous_qr_generations(self):
        older, current = threading.Event(), threading.Event()
        self.gui.qr_pair_worker_events = [older, current]
        self.gui.qr_pair_worker_done = current
        self.gui._restart_and_redetect = Mock()
        self.start_manual()
        _, work, _, _ = self.gui._run_async.call_args.args
        work()
        self.assertEqual(self.gui._restart_and_redetect.call_args.args[1], (older, current))

    def test_qr_worker_completion_is_signalled_on_success_and_failure(self):
        for failure in (None, RuntimeError("pair failed")):
            with self.subTest(failure=failure):
                self.gui._run_async.reset_mock()
                self.gui.qr_pair_cancel_event = None
                self.gui.qr_pair_status_var = Value()
                self.gui._show_qr_pairing_window = Mock(return_value=True)
                self.gui._get_client = Mock(return_value=self.client)
                self.client.pair_by_qr.side_effect = failure
                self.client.pair_by_qr.return_value = "connected"
                AdbPilotGui.qr_pair_device(self.gui)
                self.assertFalse(self.gui.qr_pair_worker_done.is_set())
                _, work, _, _ = self.gui._run_async.call_args.args
                if failure is None:
                    self.assertEqual(work(), "connected")
                else:
                    with self.assertRaises(RuntimeError):
                        work()
                self.assertTrue(self.gui.qr_pair_worker_done.is_set())

    def test_unfinished_qr_task_does_not_restart_or_start_cooldown(self):
        qr_done = Mock()
        qr_done.wait.return_value = False
        with self.assertRaises(AdbPilotError):
            self.gui._restart_and_redetect(self.client, qr_done)
        self.client.restart_current_server.assert_not_called()
        self.assertIsNone(self.gui.mdns_recovery.last_restart)

    def test_manual_action_and_device_queries_share_server_lock(self):
        attempted = threading.Event()
        restarted = threading.Event()
        self.client.restart_current_server.side_effect = restarted.set
        def work():
            attempted.set()
            self.gui._restart_and_redetect(self.client, None)
        worker = threading.Thread(target=work)
        with self.gui.adb_service_lock:
            worker.start()
            self.assertTrue(attempted.wait(1))
            self.assertFalse(restarted.wait(0.1))
        worker.join(2)
        self.assertTrue(restarted.is_set())

    def test_restart_failure_is_reported_and_cools_down(self):
        self.client.restart_current_server.side_effect = subprocess.TimeoutExpired("adb", 30)
        self.start_manual()
        result, _ = self.finish_manual()
        self.assertEqual(result.status, "restart_failed")
        self.assertFalse(result.restarted)
        self.client.mdns_recovery_devices.assert_not_called()
        self.gui._render_devices.assert_not_called()
        self.assertIn("重启失败", self.gui.wireless_status_var.get())
        self.assertEqual(self.gui.mdns_recovery.state, MdnsRecoveryState.COOLDOWN)

    def test_verification_failure_preserves_known_devices_and_does_not_claim_restart_failure(self):
        self.client.mdns_recovery_services.side_effect = ValueError("private-instance")
        self.start_manual()
        result, _ = self.finish_manual()
        self.assertEqual(result.status, "verification_failed")
        self.assertTrue(result.restarted)
        self.gui._render_devices.assert_called_once()
        self.assertIn("已重启", self.gui.wireless_status_var.get())
        self.assertIn("重新检测未完成", self.gui.wireless_status_var.get())
        self.assertNotIn("private-instance", self.gui.wireless_status_var.get())

    def test_unknown_device_snapshot_does_not_clear_device_list(self):
        self.client.mdns_recovery_devices.side_effect = subprocess.TimeoutExpired("adb", 3)
        self.start_manual()
        result, _ = self.finish_manual()
        self.assertIsNone(result.devices)
        self.gui._render_devices.assert_not_called()

    def test_changed_client_or_closed_gui_prevents_manual_restart(self):
        for change in ("client", "close"):
            gui = self.gui
            if change == "client":
                gui.client = Mock()
            else:
                gui.client = self.client
                gui.mdns_stop.set()
            self.assertEqual(gui._restart_and_redetect(self.client, None).status, "cancelled")
        self.client.restart_current_server.assert_not_called()
        self.assertIsNone(self.gui.mdns_recovery.last_restart)

    def test_stale_manual_result_is_not_rendered(self):
        self.start_manual()
        _, work, done, _ = self.gui._run_async.call_args.args
        result = work()
        self.gui.client = Mock()
        done(result)
        self.gui._render_devices.assert_not_called()
        self.assertFalse(self.gui.mdns_manual_restart_running)

    def test_client_resolution_failure_reenables_action(self):
        self.gui._prepare_client_async.side_effect = lambda ready, failed, **kwargs: failed(None)
        self.start_manual()
        self.assertFalse(self.gui.mdns_manual_restart_running)
        self.button.configure.assert_called_with(state="normal")
        self.client.restart_current_server.assert_not_called()

    def test_manual_request_prevents_overlapping_auto_recovery_and_new_pairing(self):
        self.gui.mdns_manual_restart_running = True
        self.client.devices.return_value = []
        self.assertEqual(AdbPilotGui._read_devices_and_recover(self.gui, self.client), ([], None))
        self.client.recover_stale_mdns.assert_not_called()
        AdbPilotGui.qr_pair_device(self.gui)
        self.gui._run_async.assert_not_called()

    def test_manual_action_remains_available_when_auto_recovery_is_disabled(self):
        self.gui.mdns_recovery.config = MdnsRecoveryConfig(enabled=False)
        self.start_manual()
        result, _ = self.finish_manual()
        self.assertEqual(result.status, "checked")
        self.client.restart_current_server.assert_called_once()

    def test_empty_service_snapshot_does_not_claim_pairing_success(self):
        self.client.mdns_recovery_services.return_value = ()
        self.start_manual()
        result, _ = self.finish_manual()
        self.assertEqual(result.status, "checked")
        self.assertIn("0 个无线调试服务", self.gui.wireless_status_var.get())
        self.assertNotIn("配对成功", self.gui.wireless_status_var.get())


class CurrentServerRestartTests(unittest.TestCase):
    def test_current_server_restart_keeps_scope_and_independent_startup_budget(self):
        with patch("adbpilot.client.PlatformAdapter.resolve_adb", return_value=Path("/test/adb")):
            client = AdbClient()
        client.run = Mock()
        client._record = Mock()
        client._stop_competing_adb_servers = Mock()
        client.restart_current_server()
        self.assertEqual(client.run.call_args_list[0].args, (["kill-server"],))
        self.assertEqual(client.run.call_args_list[0].kwargs, {"check": False, "timeout": 3})
        self.assertEqual(client.run.call_args_list[1].args, (["start-server"],))
        self.assertEqual(client.run.call_args_list[1].kwargs, {"timeout": 30})
        client._stop_competing_adb_servers.assert_not_called()


if __name__ == "__main__":
    unittest.main()
