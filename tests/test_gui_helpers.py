import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from adbpilot.gui import (
    device_connection_summary,
    device_connection_type,
    device_display_name,
    floating_helper_payload,
    floating_topmost_badge_text,
    floating_topmost_locked_from_config,
    floating_topmost_style,
    gui_config_path,
    load_gui_config,
    macos_floating_helper_build_path,
    macos_floating_helper_needs_build,
    macos_floating_helper_source_path,
    mono_font,
    save_gui_config,
    ui_font,
)


class GuiHelperTests(unittest.TestCase):
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
