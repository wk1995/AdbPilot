import json
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from adbpilot import lag, lag_incident
from adbpilot.errors import AdbPilotError
from adbpilot.models import CommandResult, Device


USB = Device("fixture-phone-usb", "device", {"usb": "1", "transport_id": "7"})
WIFI = Device("fixture._adb-tls-connect._tcp", "device", {"transport_id": "8"})
RECOVERED = Device(USB.serial, "device", {"usb": "1", "transport_id": "13"})
OLD_BOOT = "11111111-2222-3333-4444-555555555555"
NEW_BOOT = "aaaa1111-2222-3333-4444-555555555555"


class FakeClient:
    def __init__(self):
        self.now = 0
        self.probes = []
        self.device_commands = []
        self.mismatch = False
        self.change_wifi_id = False
        self.host_gap = False
        self.recovered = False

    def wait(self, deadline, cancelled):
        lag._check_cancelled(cancelled)
        self.now = max(self.now, deadline)

    def run(self, command, *, timeout, transport_id=None):
        if command == ["devices", "-l"]:
            if self.host_gap and len(self.probes) == 1:
                self.host_gap = False
                raise subprocess.TimeoutExpired(command, timeout)
            usb_id = 13 if self.recovered else 7
            wifi_id = 10 if self.change_wifi_id and self.probes else 8
            output = f"List of devices attached\n{USB.serial} device usb:1 transport_id:{usb_id}\n"
            if not self.recovered:
                output += f"{WIFI.serial} device transport_id:{wifi_id}\n192.0.2.10:37001 device transport_id:9\n"
        else:
            args = command[2:]
            self.device_commands.append((transport_id, args))
            if args == ["true"]:
                self.probes.append((transport_id, self.now))
                self.now += 20_000_000
                if 3 <= len(self.probes) <= 4:
                    raise subprocess.TimeoutExpired(command, timeout)
                output = ""
            elif args == ["getprop", "ro.serialno"]:
                output = "different-hardware" if self.mismatch and transport_id != 7 else "private-hardware"
            elif args[-1] == "/proc/sys/kernel/random/boot_id":
                output = NEW_BOOT if self.recovered else OLD_BOOT
            elif args == ["cat", "/proc/uptime"]:
                output = "35.12 80.22"
            elif args[0] == "getprop":
                output = "reboot"
            elif args[0] == "date":
                output = f"{int(datetime.now(timezone.utc).timestamp())},+0000"
            else:
                self.assert_dropbox(args)
                stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                output = (f"Drop box contents: 3 entries\n{stamp} SYSTEM_BOOT (text)\n"
                          f"{stamp} system_server_watchdog (text)\n{stamp} private_app_crash (text)\n")
        return CommandResult(tuple(command), 0, output, "")

    @staticmethod
    def assert_dropbox(args):
        assert args == ["dumpsys", "dropbox"]


class IncidentTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeClient()
        self.control = lag.RecordingControl()
        self.events = []
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def record(self, duration=35):
        def event(kind, detail):
            self.events.append((kind, detail))
            if kind == "started" or kind == "unresponsive":
                self.assertTrue(self.control.mark())
        with patch.object(lag_incident.time, "monotonic_ns", side_effect=lambda: self.client.now), patch.object(
            lag, "_wait_until", side_effect=self.client.wait
        ):
            return lag_incident.record_incident(self.client, self.directory, usb=USB, wifi=WIFI, label="妙想桌面",
                                                control=self.control, on_event=event, duration=duration)

    def test_alternation_freezes_checkpoint_and_keeps_recording_on_mac(self):
        incident = self.record()
        timeline = json.loads((incident.directory / "timeline.json").read_text())
        snapshot = json.loads((incident.directory / "timeout.json").read_text())
        self.assertEqual(timeline["status"], "complete")
        self.assertEqual([identifier for identifier, _ in self.client.probes], [7, 8, 7, 8, 7])
        self.assertGreaterEqual(self.client.probes[-1][1] - self.client.probes[-2][1], 15 * lag_incident.SECOND)
        self.assertEqual(snapshot["fault"]["last_success_ms"], {"usb": 20, "wifi": 3020})
        self.assertEqual(snapshot["fault"]["first_timeout_ms"], {"usb": 6000, "wifi": 9000})
        self.assertEqual(snapshot["marks_ms"], [0])
        self.assertEqual(timeline["marks_ms"], [0, 9020])
        self.assertGreater(len(timeline["timeline"]), len(snapshot["timeline"]))
        self.assertIn(("unresponsive", "both"), self.events)
        self.assertEqual(timeline["timeline"][0]["unverified_wireless"], {"unverified_wireless_1": "device"})
        for value in (USB.serial, WIFI.serial, "192.0.2.10", "private-hardware", OLD_BOOT):
            self.assertNotIn(value, json.dumps(timeline))

    def test_mismatched_phone_is_rejected_before_recording(self):
        self.client.mismatch = True
        with self.assertRaisesRegex(AdbPilotError, "不同设备"):
            self.record()
        self.assertFalse(self.client.probes)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_changed_transport_is_never_probed_but_remains_in_timeline(self):
        self.client.change_wifi_id = True
        incident = self.record(duration=8)
        timeline = json.loads((incident.directory / "timeline.json").read_text())["timeline"]
        self.assertEqual(timeline[1]["transports"]["wifi"], "transport_changed")
        self.assertEqual(timeline[1]["eligible"], ["usb"])
        self.assertTrue(all(identifier == 7 for identifier, _ in self.client.probes))

    def test_host_query_gap_leaves_transport_continuity_unverified(self):
        self.client.host_gap = True
        incident = self.record(duration=8)
        timeline = json.loads((incident.directory / "timeline.json").read_text())["timeline"]
        self.assertEqual([entry["host"] for entry in timeline], ["ok", "timeout", "ok"])
        self.assertEqual(timeline[-1]["eligible"], [])
        self.assertEqual(len(self.client.probes), 1)

    def test_recovery_checks_new_usb_id_and_writes_only_system_metadata(self):
        incident = self.record(duration=8)
        self.client.recovered = True
        self.client.device_commands.clear()
        path, report = lag_incident.collect_recovery(self.client, incident, RECOVERED)
        self.assertEqual(json.loads(path.read_text()), report)
        self.assertEqual(report["session_id"], incident.session_id)
        self.assertEqual(report["boot_cycle"], "new_boot")
        self.assertEqual(report["uptime_seconds"], 35.12)
        self.assertEqual(report["boot_reasons"]["sys.boot.reason"], "reboot")
        self.assertEqual([entry["tag"] for entry in report["dropbox_entries"]], ["SYSTEM_BOOT", "system_server_watchdog"])
        self.assertTrue(all(identifier == 13 for identifier, _ in self.client.device_commands))
        self.assertNotIn(NEW_BOOT, path.read_text())

        self.client.mismatch = True
        self.client.device_commands.clear()
        with self.assertRaisesRegex(AdbPilotError, "身份不匹配"):
            lag_incident.collect_recovery(self.client, incident, RECOVERED)
        self.assertEqual(self.client.device_commands, [(13, ["getprop", "ro.serialno"])])


if __name__ == "__main__":
    unittest.main()
