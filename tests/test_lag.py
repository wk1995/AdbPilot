import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from adbpilot import cli, lag, lag_report
from adbpilot.client import AdbClient, parse_devices
from adbpilot.errors import AdbPilotError
from adbpilot.models import CommandResult


WIFI = "adb-test-unit (2)._adb-tls-connect._tcp"
USB = "test-usb"
DEVICES = f"List of devices attached\n{USB} device usb:1 transport_id:7\n{WIFI} device transport_id:8\n"
PACKAGE = "com.example.app"


def frame_dump(*rows, pid=123, title="test-window"):
    return "\n".join([
        f"** Graphics info for pid {pid} [{PACKAGE}] **",
        f"Window: {title}", "---PROFILEDATA---", "Flags,IntendedVsync,FrameCompleted,",
        *rows, "---PROFILEDATA---",
    ])


class FakeClock:
    nanoseconds = 0

    def now(self):
        return self.nanoseconds

    def sleep(self, seconds):
        self.nanoseconds += round(seconds * lag.NANOSECONDS_PER_SECOND)


class FakeClient:
    def __init__(self, clock):
        self.clock = clock
        self.commands = []
        self.probes = 0
        self.frames = 0
        self.delay = 0.02
        self.timeout_first = False
        self.timeout_all = False
        self.host_error_after = None
        self.interrupt_first = False
        self.replace_after = None

    def run(self, command, *, timeout, check=True, transport_id=None):
        self.commands.append((command, transport_id, timeout))
        if command == ["devices", "-l"]:
            if self.host_error_after == self.probes:
                self.clock.sleep(timeout)
                raise subprocess.TimeoutExpired(command, timeout)
            output = DEVICES
            if self.replace_after == self.probes:
                output = output.replace("transport_id:8", "transport_id:9")
        elif command == ["version"]:
            output = "Android Debug Bridge version 1.0.41\nVersion 37.0.0-12345\nInstalled as private-path\n"
        elif command[2] == "getprop":
            output = "35\n"
        elif command[2] == "dumpsys":
            self.frames += 1
            rows = ["0,100000000,110000000,"]
            if self.frames == 2:
                rows.append("0,200000000,230000000,")
            output = frame_dump(*rows)
        else:
            self.probes += 1
            if self.timeout_all or (self.probes == 1 and self.timeout_first):
                self.clock.sleep(timeout)
                raise subprocess.TimeoutExpired(command, timeout, output="private device output")
            if self.probes == 1 and self.interrupt_first:
                raise KeyboardInterrupt
            self.clock.sleep(self.delay)
            output = "private device output"
        return CommandResult(tuple(command), 0, output, "")


class LagTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.client = FakeClient(self.clock)

    def capture(self, *, package=None, serial=WIFI, duration=15, on_checkpoint=None):
        with patch.object(lag.time, "monotonic_ns", self.clock.now), patch.object(lag.time, "sleep", self.clock.sleep):
            with redirect_stderr(io.StringIO()):
                return lag.record_session(self.client, serial=serial, package=package, label="wifi-test",
                                          duration=duration, mark_on_enter=False, on_checkpoint=on_checkpoint)

    def test_mixed_transports_require_explicit_selection(self):
        devices = parse_devices(DEVICES)
        self.assertEqual([device.serial for device in devices], [USB, WIFI])
        self.assertTrue(all(device.is_ready for device in devices))
        with self.assertRaisesRegex(AdbPilotError, "--serial"):
            self.capture(serial=None)
        self.assertEqual(len(self.client.commands), 1)

    def test_client_emits_transport_selector_before_command(self):
        with patch("adbpilot.client.PlatformAdapter.resolve_adb", return_value="adb"):
            client = AdbClient()
        completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        with patch("adbpilot.client.subprocess.run", return_value=completed) as run:
            client.run(["shell", "-n", "true"], transport_id=8, timeout=0.5)
        self.assertEqual(run.call_args.args[0], ["adb", "-t", "8", "shell", "-n", "true"])
        self.assertEqual(run.call_args.kwargs["timeout"], 0.5)
        with self.assertRaises(ValueError):
            client.run(["shell", "true"], serial=USB, transport_id=8)

    def test_report_collects_latency_and_only_new_frame_data(self):
        report = self.capture(package=PACKAGE)
        self.assertEqual(report["status"], "complete")
        self.assertEqual(report["context"]["transport"], "wifi")
        self.assertEqual(report["context"]["adb_version"], "37.0.0-12345")
        self.assertEqual(report["context"]["elapsed_ms"], 15000)
        self.assertEqual(report["adb"]["counts"], {"ok": 5, "timeout": 0, "failed": 0, "skipped": 0})
        self.assertEqual(report["adb"]["median_ms"], 20)
        self.assertEqual(report["frames"]["frame_count"], 1)
        self.assertEqual(report["frames"]["median_ms"], 30)
        for command, transport_id, _ in self.client.commands:
            if command[0] == "shell":
                self.assertEqual(transport_id, 8)
                self.assertEqual(command[1], "-n")
        encoded = json.dumps(report)
        for value in (WIFI, USB, "private device output", "private-path", "test-window"):
            self.assertNotIn(value, encoded)

    def test_transport_replacement_ends_session_without_switching(self):
        self.client.replace_after = 1
        report = self.capture()
        self.assertEqual((report["status"], report["stop_reason"]), ("partial", "target_lost"))
        self.assertEqual(self.client.probes, 1)
        self.assertEqual(report["adb"]["counts"]["ok"], 1)
        self.assertEqual(report["adb"]["counts"]["skipped"], 1)

    def test_cli_record_writes_partial_report_and_returns_failure(self):
        self.client.replace_after = 1
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "partial.json"
            output = io.StringIO()
            with patch.object(cli, "AdbClient", return_value=self.client), patch.object(
                lag.time, "monotonic_ns", self.clock.now
            ), patch.object(lag.time, "sleep", self.clock.sleep), redirect_stdout(output), redirect_stderr(io.StringIO()):
                status = cli.main(["lag", "record", "--serial", WIFI, "--out", str(path), "--json"])
            self.assertEqual(status, 2)
            report = lag_report.load_report(path)
            self.assertEqual(json.loads(output.getvalue()), report)
            self.assertEqual(report["stop_reason"], "target_lost")

    def test_timeouts_do_not_pollute_successful_latency_or_trigger_bursts(self):
        self.client.timeout_first = True
        report = self.capture()
        self.assertEqual(report["adb"]["counts"]["timeout"], 1)
        self.assertEqual(report["adb"]["median_ms"], 20)
        starts = [sample["started_ms"] for sample in report["adb"]["samples"]]
        self.assertTrue(all(right - left >= 3000 for left, right in zip(starts, starts[1:])))

    def test_repeated_timeouts_checkpoint_and_back_off_without_final_frame_query(self):
        self.client.timeout_all = True
        checkpoint = []

        def save(report):
            self.assertEqual(self.client.probes, 2)
            checkpoint.append(report)

        report = self.capture(package=PACKAGE, duration=35, on_checkpoint=save)
        self.assertEqual(len(checkpoint), 1)
        self.assertEqual(checkpoint[0]["status"], "partial")
        self.assertEqual(checkpoint[0]["adb"]["counts"]["timeout"], 2)
        self.assertEqual(checkpoint[0]["diagnosis"]["first_detected_ms"], 5000)
        self.assertEqual(report["session_id"], checkpoint[0]["session_id"])
        starts = [sample["started_ms"] for sample in report["adb"]["samples"] if sample["status"] == "timeout"]
        self.assertEqual(starts, [0, 3000, 18000, 33000])
        self.assertEqual(report["adb"]["skipped_by_reason"], {"host_query_failed": 0, "backoff": 8})
        self.assertEqual(report["frames"]["reasons"], ["device_unresponsive"])
        self.assertEqual(self.client.frames, 1)

    def test_host_query_failures_do_not_count_as_device_timeouts(self):
        self.client.timeout_first = True
        self.client.host_error_after = 1
        report = self.capture()
        self.assertNotIn("diagnosis", report)
        self.assertEqual(report["adb"]["counts"]["timeout"], 1)
        self.assertEqual(report["adb"]["skipped_by_reason"]["host_query_failed"], 4)

    def test_cli_publishes_separate_timeout_snapshot_and_final_report(self):
        self.client.timeout_all = True
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            stderr = io.StringIO()
            with patch.object(cli, "AdbClient", return_value=self.client), patch.object(
                lag.time, "monotonic_ns", self.clock.now
            ), patch.object(lag.time, "sleep", self.clock.sleep), redirect_stdout(io.StringIO()), redirect_stderr(stderr):
                self.assertEqual(cli.main(["lag", "record", "--serial", WIFI, "--out", str(path), "--json"]), 0)
            checkpoint, = Path(directory).glob("report.timeout-*.json")
            saved = lag_report.load_report(checkpoint)
            final = lag_report.load_report(path)
            self.assertEqual(saved["status"], "partial")
            self.assertEqual(saved["diagnosis"], final["diagnosis"])
            self.assertIn(lag_report.UNRESPONSIVE_MESSAGE, stderr.getvalue())
            self.assertEqual(final["adb"]["counts"]["timeout"], 3)

    def test_external_mark_and_stop_share_the_recording_clock(self):
        control = lag.RecordingControl()
        self.assertFalse(control.mark())
        run = self.client.run

        def stop_after_probe(command, **options):
            result = run(command, **options)
            if command == ["shell", "-n", "true"]:
                self.assertTrue(control.mark())
                control.cancelled.set()
            return result

        self.client.run = stop_after_probe
        with patch.object(lag.time, "monotonic_ns", self.clock.now):
            report = lag.record_session(self.client, serial=WIFI, package=None, label=None, duration=30,
                                        mark_on_enter=False, control=control, expected_transport_id="8")
        self.assertEqual(report["stop_reason"], "interrupted")
        self.assertEqual(report["adb"]["counts"]["ok"], 1)
        self.assertEqual(report["marks"], [20])
        self.assertFalse(control.mark())

    def test_device_selection_rejects_changed_transport_before_recording(self):
        with self.assertRaisesRegex(AdbPilotError, "传输已变化"):
            lag.record_session(self.client, serial=WIFI, package=None, label=None, duration=30,
                               mark_on_enter=False, expected_transport_id="9")
        self.assertEqual(len(self.client.commands), 1)

    def test_interruption_produces_serializable_partial_report(self):
        self.client.interrupt_first = True
        report = self.capture()
        self.assertEqual((report["status"], report["stop_reason"]), ("partial", "interrupted"))
        self.assertEqual(report["adb"]["counts"]["skipped"], 1)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "partial.json"
            lag_report.write_report(path, report)
            self.assertEqual(lag_report.load_report(path), report)

    def test_recent_buffer_with_no_overlap_reports_partial_coverage(self):
        before = lag_report.parse_frame_stats(frame_dump("0,100000000,110000000,"))
        after = lag_report.parse_frame_stats(frame_dump("0,200000000,230000000,", "8,300000000,330000000,"))
        result = lag_report.summarize_frames(before, after)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["reasons"], ["buffer_discontinuity"])
        self.assertEqual(result["frame_count"], 1)

    def test_missing_baseline_for_recreated_window_never_compares_stale_frames(self):
        before = lag_report.parse_frame_stats(frame_dump("0,100000000,110000000,"))
        after = lag_report.parse_frame_stats(frame_dump("0,200000000,230000000,", pid=124))
        result = lag_report.summarize_frames(before, after)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reasons"], ["windows_changed"])
        self.assertEqual(result["frame_count"], 0)

    def test_compare_reuses_report_without_adb_and_checks_compatibility(self):
        baseline = self.capture()
        self.client.delay = 0.05
        current = self.capture()
        with tempfile.TemporaryDirectory() as directory:
            left, right = Path(directory) / "left.json", Path(directory) / "right.json"
            lag_report.write_report(left, baseline)
            lag_report.write_report(right, current)
            output = io.StringIO()
            with patch.object(cli, "AdbClient", side_effect=AssertionError("unexpected ADB access")), redirect_stdout(output):
                self.assertEqual(cli.main(["lag", "compare", str(left), str(right), "--same-device", "--json"]), 0)
            self.assertEqual(json.loads(output.getvalue())["delta_ms"], {"median_ms": 30, "max_ms": 30})
        current["context"]["adb_version"] = "36.0.0"
        comparison = lag_report.compare_reports(baseline, current, same_device=True)
        self.assertIsNone(comparison["delta_ms"])
        self.assertIn("ADB 版本不同或未知", comparison["comparison_limits"])

    def test_atomic_report_rejects_existing_file_and_invalid_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "old.json"
            path.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(AdbPilotError, "文件已存在"):
                lag_report.write_report(path, self.capture())
            self.assertEqual(path.read_text(encoding="utf-8"), "{}")
            self.assertEqual(list(Path(directory).iterdir()), [path])
            with self.assertRaisesRegex(AdbPilotError, "版本"):
                lag_report.load_report(path)


if __name__ == "__main__":
    unittest.main()
