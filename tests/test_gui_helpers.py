import unittest

from adbpilot.gui import device_connection_type, device_display_name


class GuiHelperTests(unittest.TestCase):
    def test_device_connection_type(self):
        self.assertEqual(device_connection_type("emulator-5554"), "USB")
        self.assertEqual(device_connection_type("5ENDU19B01011261"), "USB")
        self.assertEqual(device_connection_type("192.168.1.10:5555"), "无线")

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


if __name__ == "__main__":
    unittest.main()
