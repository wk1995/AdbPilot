import os
import queue
import threading
import unittest
from tkinter import TclError
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from adbpilot.gui import (
    AdbPilotGui,
    device_connection_summary,
    device_connection_type,
    device_display_name,
    floating_helper_payload,
    floating_topmost_badge_text,
    floating_topmost_locked_from_config,
    floating_topmost_style,
    generate_adb_qr_pairing_payload,
    qr_pairing_mosaic,
    gui_config_path,
    load_gui_config,
    merge_adb_paths,
    saved_adb_paths,
    macos_floating_helper_build_path,
    macos_floating_helper_needs_build,
    macos_floating_helper_source_path,
    mono_font,
    save_gui_config,
    ui_font,
)


class FakeVar:
    def __init__(self, value: str = "") -> None:
        self.value = value

    def get(self) -> str:
        return self.value

    def set(self, value: str) -> None:
        self.value = value


class FakeWidget:
    def __init__(self, text: str = "", *, parent: "FakeContainer | None" = None, exists: bool = True) -> None:
        self.text = text
        self.exists = exists
        self.parent = parent

    def winfo_exists(self) -> bool:
        return self.exists

    def destroy(self) -> None:
        self.exists = False
        if self.parent is not None:
            self.parent.children = [child for child in self.parent.children if child is not self]

    def pack(self, *_args: object, **_kwargs: object) -> None:
        if self.parent is not None and self not in self.parent.children:
            self.parent.children.append(self)


class FakeContainer:
    def __init__(self, *children: FakeWidget) -> None:
        self.children = list(children)
        for child in self.children:
            child.parent = self

    def winfo_children(self) -> list[FakeWidget]:
        return list(self.children)

    def winfo_exists(self) -> bool:
        return True


class GuiHelperTests(unittest.TestCase):
    def test_adb_browser_omits_extensionless_filter_on_unix(self):
        for system in ("Darwin", "Linux"):
            with self.subTest(system=system):
                gui = SimpleNamespace(adb_path_var=Mock(), _remember_adb_path=Mock())
                with patch("adbpilot.gui.platform.system", return_value=system), patch(
                    "adbpilot.gui.filedialog.askopenfilename", return_value="/sdk/platform-tools/adb"
                ) as dialog:
                    AdbPilotGui._browse_adb(gui)
                dialog.assert_called_once_with(parent=gui, title="选择 adb")
                gui.adb_path_var.set.assert_called_once_with("/sdk/platform-tools/adb")
                gui._remember_adb_path.assert_called_once_with("/sdk/platform-tools/adb")

    def test_adb_browser_cancel_preserves_path(self):
        gui = SimpleNamespace(adb_path_var=Mock(), _remember_adb_path=Mock())
        with patch("adbpilot.gui.platform.system", return_value="Darwin"), patch(
            "adbpilot.gui.filedialog.askopenfilename", return_value=""
        ):
            AdbPilotGui._browse_adb(gui)
        gui.adb_path_var.set.assert_not_called()
        gui._remember_adb_path.assert_not_called()

    def test_windows_adb_browser_filters_executables(self):
        gui = SimpleNamespace(adb_path_var=Mock(), _remember_adb_path=Mock())
        with patch("adbpilot.gui.platform.system", return_value="Windows"), patch(
            "adbpilot.gui.filedialog.askopenfilename", return_value=""
        ) as dialog:
            AdbPilotGui._browse_adb(gui)
        dialog.assert_called_once_with(
            parent=gui, title="选择 adb.exe", filetypes=[("ADB", "*.exe"), ("所有文件", "*.*")]
        )

    def test_windows_adb_browser_retries_without_filter_on_tcl_error(self):
        gui = SimpleNamespace(adb_path_var=Mock(), _remember_adb_path=Mock())
        with patch("adbpilot.gui.platform.system", return_value="Windows"), patch(
            "adbpilot.gui.filedialog.askopenfilename", side_effect=[TclError("filter error"), ""]
        ) as dialog:
            AdbPilotGui._browse_adb(gui)
        self.assertEqual(dialog.call_count, 2)
        self.assertEqual(dialog.call_args.kwargs, {"parent": gui, "title": "选择 adb.exe"})

    def test_device_query_waits_until_server_restart_finishes(self):
        restart_started = threading.Event()
        finish_restart = threading.Event()
        query_attempted = threading.Event()
        query_started = threading.Event()

        def restart():
            restart_started.set()
            finish_restart.wait(2)

        client = Mock()
        client.restart_server.side_effect = restart
        client.devices.side_effect = lambda: query_started.set() or []
        gui = SimpleNamespace(adb_service_lock=threading.Lock(), _get_client=lambda: client)

        def query():
            query_attempted.set()
            AdbPilotGui._read_devices(gui)

        restart_thread = threading.Thread(target=AdbPilotGui._restart_adb_server, args=(gui,))
        query_thread = threading.Thread(target=query)
        restart_thread.start()
        try:
            self.assertTrue(restart_started.wait(1))
            query_thread.start()
            self.assertTrue(query_attempted.wait(1))
            self.assertFalse(query_started.wait(0.1))
        finally:
            finish_restart.set()
            restart_thread.join(2)
            if query_thread.ident is not None:
                query_thread.join(2)
        self.assertTrue(query_started.is_set())
        client.restart_server.assert_called_once()
        client.devices.assert_called_once()
    def test_generate_adb_qr_pairing_payload(self):
        service_name, password, payload = generate_adb_qr_pairing_payload()

        self.assertTrue(service_name.startswith("studio-"))
        self.assertEqual(len(service_name), len("studio-") + 10)
        self.assertEqual(len(password), 12)
        self.assertEqual(payload, f"WIFI:T:ADB;S:{service_name};P:{password};;")

    def test_qr_pairing_mosaic_hides_the_expired_code(self):
        mosaic = qr_pairing_mosaic()

        self.assertNotIn("WIFI:T:ADB", mosaic)
        self.assertGreater(len(mosaic.splitlines()), 1)
        self.assertIn("██", mosaic)
        self.assertIn("░░", mosaic)

    def test_finished_qr_pairing_covers_code_and_offers_restart(self):
        old_code = FakeWidget("可扫描二维码")
        content = FakeContainer(old_code)
        status = FakeVar("等待手机扫描")
        actions = FakeContainer()
        restart = FakeWidget("重新生成", parent=actions)
        labels: list[FakeWidget] = []

        def label_factory(parent, **kwargs):
            widget = FakeWidget(str(kwargs.get("text", "")), parent=parent)
            labels.append(widget)
            return widget

        gui = SimpleNamespace(
            qr_pair_content=content,
            qr_pair_window=FakeWidget(exists=True),
            qr_pair_status_var=status,
            qr_pair_restart_button=restart,
        )

        with patch("adbpilot.gui.ttk.Label", side_effect=label_factory), patch("adbpilot.gui.mono_font", return_value=("Menlo", 11)):
            AdbPilotGui._finish_qr_pairing(gui, "扫码配对已结束，这个二维码不能再用。")

        self.assertFalse(old_code.exists)
        self.assertTrue(labels)
        self.assertTrue(any("██" in widget.text for widget in labels))
        self.assertNotIn("WIFI:T:ADB", " ".join(widget.text for widget in labels))
        self.assertIn("不能再用", status.value)
        self.assertIn(restart, actions.children)

    def test_successful_qr_pairing_closes_window_without_cancelling(self):
        window = FakeWidget(exists=True)
        cancel_event = threading.Event()
        gui = SimpleNamespace(
            qr_pair_session=0,
            qr_pair_cancel_event=None,
            qr_pair_window=window,
            qr_pair_status_var=FakeVar(),
            _append_output=Mock(),
            _show_qr_pairing_window=Mock(return_value=True),
            _get_client=Mock(return_value=Mock()),
            refresh_devices=Mock(),
            _close_qr_pairing_window=lambda cancel=True: AdbPilotGui._close_qr_pairing_window(gui, cancel),
        )
        gui.qr_pair_cancel_event = cancel_event

        done = None

        def capture_run_async(_self, _label, _work, on_done, _on_error):
            nonlocal done
            done = on_done

        gui._run_async = lambda *args: capture_run_async(gui, *args)
        with patch(
            "adbpilot.gui.generate_adb_qr_pairing_payload", return_value=("studio-test", "secret", "payload")
        ):
            AdbPilotGui.qr_pair_device(gui)

        self.assertIsNotNone(done)
        cancel_event.clear()
        done("已连接 192.168.1.8:5555")

        self.assertFalse(cancel_event.is_set())
        self.assertFalse(window.exists)
        gui.refresh_devices.assert_called_once()

    def test_device_connection_type(self):
        self.assertEqual(device_connection_type("emulator-5554"), "USB")
        self.assertEqual(device_connection_type("5ENDU19B01011261"), "USB")
        self.assertEqual(device_connection_type("5ENDU19B01011261", {"usb": "1-1"}), "USB")
        self.assertEqual(device_connection_type("192.168.1.10:5555"), "Wi-Fi")
        self.assertEqual(device_connection_type("adb-5ENDU19B01011261._adb-tls-connect._tcp."), "Wi-Fi")
        self.assertEqual(device_connection_type("5ENDU19B01011261", {"local": "tcp"}), "Wi-Fi")

    def test_device_connection_summary(self):
        rows = [
            ("abc", {"details": {"usb": "1-1"}}),
            ("adb-abc._adb-tls-connect._tcp.", {"details": {}}),
        ]
        self.assertEqual(device_connection_summary(rows), "USB 1 · Wi-Fi 1")

    def test_device_display_name_prefers_model(self):
        self.assertEqual(
            device_display_name(
                "abc",
                {"model": "Pixel_8", "product": "sdk_gphone64", "device": "emu64"},
            ),
            "Pixel 8",
        )

    def test_device_display_name_falls_back_to_serial(self):
        self.assertEqual(device_display_name("abc", {}), "abc")

    def test_saved_adb_paths_keep_history_and_current_path(self):
        paths = saved_adb_paths(
            {
                "adb_path": "/sdk/current/adb",
                "adb_paths": ["/sdk/old/adb", "/sdk/current/adb", "  "],
            }
        )
        self.assertEqual(paths, ["/sdk/old/adb", "/sdk/current/adb"])

    def test_merge_adb_paths_drops_duplicates_and_blanks(self):
        self.assertEqual(
            merge_adb_paths(["/a/adb", ""], ["/a/adb", "/b/adb"], "/b/adb"),
            ["/a/adb", "/b/adb"],
        )

    def test_download_progress_updates_bar_without_spamming_log(self):
        output = Mock()

        class Bar(dict):
            def winfo_exists(self) -> bool:
                return True

            def stop(self) -> None:
                return None

            def configure(self, **_kwargs: object) -> None:
                return None

        bar = Bar()
        status = Mock()
        gui = SimpleNamespace(
            qr_pair_session=0,
            result_queue=Mock(),
            status_var=Mock(),
            output=output,
            adb_download_status_var=status,
            adb_download_progress=bar,
            _append_output=output,
            _update_adb_download_progress=lambda message: AdbPilotGui._update_adb_download_progress(gui, message),
            _update_dependency_progress=Mock(),
            after=lambda _delay, callback: callback,
        )
        gui.result_queue.get_nowait.side_effect = [("progress", "下载 adb", "下载进度 40%", None), queue.Empty()]
        with patch("adbpilot.gui.time.strftime", return_value="14:00:00"):
            AdbPilotGui._process_results(gui)
        status.set.assert_called_once_with("下载进度 40%")
        self.assertEqual(bar["value"], 40)
        output.assert_not_called()

    def test_download_completion_switches_to_new_adb(self):
        gui = SimpleNamespace(
            adb_version_var=Mock(**{"get.return_value": "37.0.0"}),
            adb_download_dir_var=Mock(**{"get.return_value": "/work/AdbPilot"}),
            adb_path_var=Mock(),
            client="old",
            result_queue=Mock(),
            _saved_download_dir=lambda: "/work/AdbPilot",
            _remember_download_dir=Mock(),
            _remember_adb_path=Mock(),
            _refresh_adb_path_choices=Mock(),
            _append_output=Mock(),
        )
        captured: dict[str, object] = {}
        gui._run_async = lambda label, func, on_success=None, on_error=None: captured.update(done=on_success)
        AdbPilotGui._start_adb_download(gui)
        done = captured["done"]
        path = Path("/work/AdbPilot/tools/platform-tools-37.0.0/platform-tools/adb")
        done(path)
        gui.adb_path_var.set.assert_called_once_with(str(path))
        gui._remember_adb_path.assert_called_once_with(str(path))
        self.assertIsNone(gui.client)

    def test_remember_adb_path_appends_without_replacing_history(self):
        gui = SimpleNamespace(gui_config={"adb_path": "/old/adb", "adb_paths": ["/old/adb"]})
        with patch("adbpilot.gui.save_gui_config") as save:
            AdbPilotGui._remember_adb_path(gui, "/new/adb")
        self.assertEqual(gui.gui_config["adb_path"], "/new/adb")
        self.assertEqual(gui.gui_config["adb_paths"], ["/old/adb", "/new/adb"])
        save.assert_called_once_with(gui.gui_config)

    def test_gui_config_round_trip(self):
        with TemporaryDirectory() as temp_dir:
            with patch("pathlib.Path.home", return_value=Path(temp_dir)):
                self.assertEqual(gui_config_path(), Path(temp_dir) / ".adbpilot" / "gui.json")
                save_gui_config({"floating_window": {"x": 12, "y": 34}})

                self.assertEqual(load_gui_config()["floating_window"], {"x": 12, "y": 34})

    def test_floating_topmost_lock_defaults_to_enabled(self):
        self.assertTrue(floating_topmost_locked_from_config({}))
        self.assertTrue(floating_topmost_locked_from_config({"floating_window": {}}))
        self.assertTrue(floating_topmost_locked_from_config({"floating_window": {"topmost_locked": "false"}}))

    def test_floating_topmost_lock_reads_boolean_false(self):
        self.assertFalse(floating_topmost_locked_from_config({"floating_window": {"topmost_locked": False}}))
        self.assertTrue(floating_topmost_locked_from_config({"floating_window": {"topmost_locked": True}}))

    def test_platform_fonts_are_macos_friendly(self):
        self.assertEqual(ui_font(12, platform_name="darwin"), ("SF Pro Text", 12))
        self.assertEqual(ui_font(12, "bold", platform_name="darwin"), ("SF Pro Text", 12, "bold"))
        self.assertEqual(mono_font(11, platform_name="darwin"), ("Menlo", 11))

    def test_floating_window_config_preserves_topmost_lock(self):
        with TemporaryDirectory() as temp_dir:
            with patch("pathlib.Path.home", return_value=Path(temp_dir)):
                save_gui_config({"floating_window": {"x": 12, "y": 34, "topmost_locked": False}})

                loaded = load_gui_config()

                self.assertFalse(floating_topmost_locked_from_config(loaded))
                self.assertEqual(loaded["floating_window"], {"x": 12, "y": 34, "topmost_locked": False})

    def test_floating_topmost_style_uses_macos_floating_window(self):
        self.assertEqual(
            floating_topmost_style(True, platform_name="darwin"),
            ("floating", "noActivates canJoinAllSpaces doesNotHide"),
        )
        self.assertEqual(floating_topmost_style(False, platform_name="darwin"), ("plain", "none"))
        self.assertIsNone(floating_topmost_style(True, platform_name="windows"))

    def test_floating_topmost_badge_text_is_compact(self):
        self.assertEqual(floating_topmost_badge_text(True), "置顶")
        self.assertEqual(floating_topmost_badge_text(False), "普通")

    def test_floating_helper_payload_matches_connected_state(self):
        payload = floating_helper_payload(
            [("abc", {"details": {"model": "Pixel_8", "usb": "1-1"}})],
            [],
            locked_serial="",
            topmost_locked=True,
            x=12,
            y=34,
        )

        self.assertEqual(payload["type"], "update")
        self.assertEqual(payload["title"], "1 台设备")
        self.assertEqual(payload["subtitle"], "Pixel 8")
        self.assertEqual(payload["footer"], "USB 1")
        self.assertEqual(payload["state"], "connected")
        self.assertEqual(payload["topmostLabel"], "置顶")
        self.assertEqual(payload["x"], 12)
        self.assertEqual(payload["y"], 34)

    def test_floating_helper_payload_marks_warning_state(self):
        payload = floating_helper_payload(
            [],
            [("abc", {"state": "已断开", "details": {"usb": "1-1"}})],
            locked_serial="abc",
            topmost_locked=False,
            x=None,
            y=None,
        )

        self.assertEqual(payload["title"], "警示：已断开")
        self.assertEqual(payload["footer"], "USB · 保持锁定")
        self.assertEqual(payload["state"], "warning")
        self.assertEqual(payload["topmostLabel"], "普通")

    def test_floating_helper_payload_can_omit_position_for_state_updates(self):
        payload = floating_helper_payload(
            [],
            [],
            locked_serial="",
            topmost_locked=True,
            x=12,
            y=34,
            include_position=False,
        )

        self.assertNotIn("x", payload)
        self.assertNotIn("y", payload)

    def test_macos_floating_helper_paths_are_stable(self):
        with TemporaryDirectory() as temp_dir:
            with patch("pathlib.Path.home", return_value=Path(temp_dir)):
                self.assertEqual(
                    macos_floating_helper_build_path(),
                    Path(temp_dir) / ".adbpilot" / "AdbPilotFloatingHelper",
                )
        self.assertEqual(macos_floating_helper_source_path().name, "macos_floating_helper.swift")

    def test_macos_floating_helper_rebuild_detection(self):
        with TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "helper.swift"
            binary = Path(temp_dir) / "helper"
            source.write_text("source", encoding="utf-8")

            self.assertTrue(macos_floating_helper_needs_build(source, binary))

            binary.write_text("binary", encoding="utf-8")
            os.utime(source, (1, 1))
            os.utime(binary, (2, 2))
            self.assertFalse(macos_floating_helper_needs_build(source, binary))

            os.utime(source, (3, 3))
            self.assertTrue(macos_floating_helper_needs_build(source, binary))


if __name__ == "__main__":
    unittest.main()
