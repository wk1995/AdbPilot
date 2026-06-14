import unittest

from adbpilot.client import parse_devices, parse_foreground_package, parse_key_value_lines, parse_package_dump, parse_processes


class ClientParsingTests(unittest.TestCase):
    def test_parse_devices_with_details(self):
        output = """List of devices attached
emulator-5554 device product:sdk_gphone64 model:sdk_gphone64_x86_64 device:emu64x transport_id:1
192.168.1.5:5555 offline
ABC123 unauthorized usb:1-1
ZYX987 no permissions usb:2-1
"""

        devices = parse_devices(output)

        self.assertEqual(len(devices), 4)
        self.assertEqual(devices[0].serial, "emulator-5554")
        self.assertEqual(devices[0].state, "device")
        self.assertEqual(devices[0].details["product"], "sdk_gphone64")
        self.assertEqual(devices[1].state, "offline")
        self.assertEqual(devices[2].state, "unauthorized")
        self.assertEqual(devices[3].state, "no permissions")

    def test_parse_key_value_lines(self):
        output = """AC powered: false
USB powered: true
level: 86
status: 2
"""

        values = parse_key_value_lines(output)

        self.assertEqual(values["USB powered"], "true")
        self.assertEqual(values["level"], "86")
        self.assertEqual(values["status"], "2")

    def test_parse_package_dump(self):
        output = """Package [com.example.app] (abc123):
  firstCodePath=/data/app/~~hash/com.example.app/base.apk
  versionCode=42 minSdk=23 targetSdk=35
  versionName=1.2.3
  firstInstallTime=2026-06-14 12:00:00
  lastUpdateTime=2026-06-14 12:10:00
  dataDir=/data/user/0/com.example.app
"""

        info = parse_package_dump(output)

        self.assertEqual(info["package"], "com.example.app")
        self.assertEqual(info["versionCode"], "42")
        self.assertEqual(info["versionName"], "1.2.3")
        self.assertEqual(info["firstCodePath"], "/data/app/~~hash/com.example.app/base.apk")
        self.assertEqual(info["dataDir"], "/data/user/0/com.example.app")

    def test_parse_foreground_package_from_window_focus(self):
        output = """
  mCurrentFocus=Window{5fd1a1 u0 com.example.app/.MainActivity}
  mFocusedApp=ActivityRecord{abc u0 com.other/.Other t10}
"""

        self.assertEqual(parse_foreground_package(output), "com.example.app")

    def test_parse_foreground_package_from_top_resumed_activity(self):
        output = """
    topResumedActivity=ActivityRecord{123 u0 com.demo/.HomeActivity t88}
"""

        self.assertEqual(parse_foreground_package(output), "com.demo")

    def test_parse_processes_android_ps(self):
        output = """USER           PID  PPID     VSZ    RSS WCHAN            ADDR S NAME
u0_a123       1234   567 123456  78900 0                   0 S com.example.app
system         222     1  12345   3000 0                   0 S surfaceflinger
u0_a123       1235   567 123456  78900 0                   0 S com.example.app:remote
"""

        processes = parse_processes(output)

        self.assertEqual(len(processes), 3)
        self.assertEqual(processes[0].pid, "1234")
        self.assertEqual(processes[0].package, "com.example.app")
        self.assertEqual(processes[1].package, "")
        self.assertEqual(processes[2].package, "com.example.app:remote")


if __name__ == "__main__":
    unittest.main()
