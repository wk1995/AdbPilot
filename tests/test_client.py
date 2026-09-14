import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from adbpilot.client import (
    AdbClient,
    find_mdns_service,
    parse_devices,
    parse_foreground_package,
    parse_key_value_lines,
    parse_mdns_services,
    mdns_address_host,
    parse_package_dump,
    parse_processes,
)
from adbpilot.models import Device


class ClientParsingTests(unittest.TestCase):
    def test_parse_mdns_services(self):
        output = """List of discovered mdns services
adb-14141FDF600081-QXjCrW  _adb-tls-pairing._tcp  192.168.86.38:33861
studio-abc123XYZ9          _adb-tls-pairing._tcp  192.168.86.39:55861
adb-14141FDF600081-TnSdi9  _adb-tls-connect._tcp.  192.168.86.38:33015
adb-e8d3d34b-1grL4          adb-tls-connect._tcp   192.168.1.71:44177
"""

        services = parse_mdns_services(output)
        service = find_mdns_service(
            services,
            name="studio-abc123XYZ9",
            service_type="_adb-tls-pairing._tcp",
        )
        connect = find_mdns_service(
            services,
            service_type="_adb-tls-connect._tcp",
            host="192.168.86.38",
        )

        self.assertEqual(len(services), 4)
        self.assertEqual(service["address"], "192.168.86.39:55861")
        self.assertEqual(connect["name"], "adb-14141FDF600081-TnSdi9")
        connect_without_leading_underscore = find_mdns_service(
            services,
            service_type="_adb-tls-connect._tcp",
            host="192.168.1.71",
        )
        self.assertEqual(connect_without_leading_underscore["address"], "192.168.1.71:44177")

    def test_mdns_address_host(self):
        self.assertEqual(mdns_address_host("192.168.86.38:33015"), "192.168.86.38")
        self.assertEqual(mdns_address_host("[fe80::abcd]:33015"), "fe80::abcd")

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

    def test_deduplicate_devices_by_hardware_serial(self):
        client = AdbClient("adb")
        client.run = Mock(
            side_effect=lambda args, **kwargs: SimpleNamespace(
                stdout={"adb-phone._adb-tls-connect._tcp.": "PHONE-001", "192.168.1.71:44177": "PHONE-001"}[
                    kwargs["serial"]
                ]
            )
        )
        devices = [
            Device("adb-phone._adb-tls-connect._tcp.", "device"),
            Device("192.168.1.71:44177", "device"),
        ]

        result = client._deduplicate_devices(devices)

        self.assertEqual([device.serial for device in result], ["192.168.1.71:44177"])
        self.assertEqual(client.run.call_count, 2)

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
