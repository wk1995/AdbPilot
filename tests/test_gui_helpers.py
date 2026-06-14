import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from adbpilot.gui import (
    device_connection_summary,
    device_connection_type,
    device_display_name,
    gui_config_path,
    load_gui_config,
    save_gui_config,
)


class GuiHelperTests(unittest.TestCase):
    def test_device_connection_type(self):
        self.assertEqual(device_connection_type("emulator-5554"), "USB")
        self.assertEqual(device_connection_type("5ENDU19B01011261"), "USB")
        self.assertEqual(device_connection_type("192.168.1.10:5555"), "Wi-Fi")

    def test_device_connection_summary(self):
        self.assertEqual(device_connection_summary(["abc", "192.168.1.10:5555"]), "USB 1 · Wi-Fi 1")

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


if __name__ == "__main__":
    unittest.main()
