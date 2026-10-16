import subprocess
import unittest
from unittest.mock import Mock, patch

from adbpilot.client import (
    AdbClient,
    capability_summary,
    classify_pair_failure,
    classify_pair_output,
    normalize_adb_address,
    normalize_pairing_code,
    pairing_status_text,
    parse_platform_tools_revision,
    parse_server_status,
    wifi_error_message,
)
from adbpilot.errors import (
    AdbConnectionError,
    AdbPairingError,
    AdbWifiCapabilityError,
    PairingCodeError,
    WirelessAddressError,
)
from adbpilot.models import AdbWifiCapabilities, Device, WirelessPairingResult


VERSION_37 = "Android Debug Bridge version 1.0.41\nVersion 37.0.1-123456\nInstalled as /test/adb\n"
STATUS_READY = "version: 37.0.1\nmdns_enabled: true\nmdns_backend: LIBADBMDNS\n"


class WifiParsingTests(unittest.TestCase):
    def test_revision_ignores_protocol_version(self):
        samples = {
            "30.0.0": (30, 0, 0),
            "34.0.4": (34, 0, 4),
            "34.0.5": (34, 0, 5),
            "36.0.0": (36, 0, 0),
            "37.0.0": (37, 0, 0),
            "37.0.1": (37, 0, 1),
        }
        for revision, expected in samples.items():
            with self.subTest(revision=revision):
                output = f"Android Debug Bridge version 1.0.41\nVersion {revision}-abc\n"
                self.assertEqual(parse_platform_tools_revision(output), expected)
        self.assertIsNone(parse_platform_tools_revision("Android Debug Bridge version 1.0.41\n"))
        self.assertIsNone(parse_platform_tools_revision("not a version"))

    def test_server_status_fields(self):
        values = parse_server_status(STATUS_READY)
        self.assertEqual(values["mdns_enabled"], "true")
        self.assertEqual(values["mdns_backend"], "LIBADBMDNS")

    def test_address_and_code_validation(self):
        self.assertEqual(normalize_adb_address("192.168.1.10:5555"), "192.168.1.10:5555")
        self.assertEqual(normalize_adb_address("[fe80::1]:5555"), "[fe80::1]:5555")
        with self.assertRaises(WirelessAddressError):
            normalize_adb_address("192.168.1.10")
        with self.assertRaises(WirelessAddressError):
            normalize_adb_address("192.168.1.10:22\n")
        self.assertEqual(normalize_pairing_code("123456"), "123456")
        for value in ("12345", "1234567", "12345a", "123 456", ""):
            with self.subTest(value=value), self.assertRaises(PairingCodeError):
                normalize_pairing_code(value)
        for value in ("", "192.168.1.10:0", "192.168.1.10:65536", "192.168.1.10:abc", "bad\x00host:1"):
            with self.subTest(value=value), self.assertRaises(WirelessAddressError):
                normalize_adb_address(value)
        self.assertEqual(normalize_adb_address(" 192.168.1.10:1 "), "192.168.1.10:1")

    def test_pair_output_classification(self):
        self.assertEqual(classify_pair_output(0, "Successfully paired to 192.168.1.10:1", ""), "success")
        self.assertEqual(classify_pair_output(1, "", "Failed: wrong code"), "failed")
        self.assertEqual(classify_pair_output(0, "", ""), "ambiguous")
        self.assertEqual(classify_pair_failure("", "connection refused"), "pair_unreachable")
        self.assertEqual(classify_pair_failure("", "timed out"), "pair_unreachable")
        self.assertEqual(classify_pair_failure("Failed: incorrect code", ""), "pair_rejected")
        self.assertEqual(classify_pair_failure("", "something else"), "unknown")
        for category in (
            "adb_missing",
            "pair_unsupported",
            "mdns_disabled",
            "mdns_backend_obsolete",
            "network_mdns_not_visible",
            "invalid_address",
            "invalid_code",
            "pair_rejected",
            "pair_unreachable",
            "paired_not_connected",
            "connect_failed",
            "unknown",
        ):
            message = wifi_error_message(category)
            self.assertLessEqual(len(message), 120)
            self.assertNotIn("123456", message)


class WifiStateMachineTests(unittest.TestCase):
    def setUp(self):
        with patch("adbpilot.client.PlatformAdapter.resolve_adb", return_value="/test/adb"):
            self.client = AdbClient(timeout=7)

    def capabilities(self, revision="37.0.1", enabled="true", backend="LIBADBMDNS"):
        return subprocess.CompletedProcess(
            [],
            0,
            f"version: {revision}\nmdns_enabled: {enabled}\nmdns_backend: {backend}\n",
            "",
        )

    def version(self, revision):
        return subprocess.CompletedProcess([], 0, f"Android Debug Bridge version 1.0.41\nVersion {revision}-abc\n", "")

    def test_old_adb_does_not_pair(self):
        with patch("adbpilot.client.subprocess.run") as run:
            run.side_effect = [self.version("34.0.4"), self.capabilities("34.0.4")]
            with self.assertRaises(AdbWifiCapabilityError):
                self.client.pair("192.168.1.10:37123", "123456")
            self.assertNotIn("123456", str(run.call_args_list))

    def test_pairing_code_uses_stdin_and_waits_for_tls_device(self):
        tls = Device("adb-phone._adb-tls-connect._tcp", "device")
        self.client.devices = Mock(side_effect=[[], [tls]])
        with patch("adbpilot.client.subprocess.run") as run:
            run.side_effect = [
                self.version("37.0.1"),
                self.capabilities(),
                subprocess.CompletedProcess(["adb", "pair", "192.168.1.10:37123"], 0, "Successfully paired\n", ""),
            ]
            result = self.client.pair("192.168.1.10:37123", "123456", wait_for_device_seconds=0)
        pair_call = run.call_args_list[-1]
        self.assertEqual(pair_call.args[0], ["/test/adb", "pair", "192.168.1.10:37123"])
        self.assertEqual(pair_call.kwargs["input"], "123456\n")
        self.assertNotIn("123456", pair_call.args[0])
        self.assertEqual(result.status, "paired_and_connected")
        self.assertEqual(result.device_serial, tls.serial)

    def test_existing_usb_device_does_not_count_as_online(self):
        usb = Device("PHONE123", "device")
        self.client.devices = Mock(return_value=[usb])
        with patch("adbpilot.client.subprocess.run") as run:
            run.side_effect = [
                self.version("37.0.1"),
                self.capabilities(),
                subprocess.CompletedProcess([], 0, "Successfully paired\n", ""),
            ]
            result = self.client.pair("192.168.1.10:37123", "123456", wait_for_device_seconds=0)
        self.assertEqual(result.status, "paired_waiting_for_connect")
        self.assertFalse(result.connected)

    def test_explicit_connect_is_a_separate_state(self):
        self.client.devices = Mock(side_effect=[[], [], [Device("192.168.1.10:5555", "device")]])
        self.client.connect = Mock(return_value="connected to 192.168.1.10:5555")
        with patch("adbpilot.client.subprocess.run") as run:
            run.side_effect = [
                self.version("36.0.0"),
                self.capabilities("36.0.0", backend="unknown"),
                subprocess.CompletedProcess([], 0, "Successfully paired\n", ""),
            ]
            result = self.client.pair(
                "192.168.1.10:37123",
                "123456",
                connect_address="192.168.1.10:5555",
                wait_for_device_seconds=0,
            )
        self.client.connect.assert_called_once_with("192.168.1.10:5555")
        self.assertEqual(result.status, "paired_connected_explicitly")
        self.assertFalse(result.capabilities.wifi2_host_compatible)

    def test_rejected_code_is_redacted(self):
        with patch("adbpilot.client.subprocess.run") as run:
            run.side_effect = [
                self.version("37.0.1"),
                self.capabilities(),
                subprocess.CompletedProcess([], 1, "", "Failed: 123456"),
            ]
            with self.assertRaises(AdbPairingError) as raised:
                self.client.pair("192.168.1.10:37123", "123456")
        self.assertEqual(raised.exception.category, "pair_rejected")
        self.assertNotIn("123456", str(raised.exception))
        self.assertNotIn("123456", raised.exception.stderr)

    def test_wifi2_requires_mdns_enabled(self):
        with patch("adbpilot.client.subprocess.run") as run:
            run.side_effect = [self.version("37.0.1"), self.capabilities(enabled="false")]
            capabilities = self.client.wifi_capabilities()
        self.assertTrue(capabilities.supports_pair)
        self.assertFalse(capabilities.wifi2_host_compatible)
        self.assertTrue(any("ADB_MDNS=1" in warning for warning in capabilities.warnings))

    def test_capability_matrix(self):
        cases = [
            ("29.0.6", "true", "LIBADBMDNS", False, False),
            ("34.0.4", "true", "LIBADBMDNS", False, False),
            ("34.0.5", "true", "LIBADBMDNS", True, False),
            ("36.0.0", "false", "unknown", True, False),
            ("37.0.0", "true", "OPENSCREEN", True, False),
            ("37.0.0", "false", "LIBADBMDNS", True, False),
            ("37.0.1", "true", "LIBADBMDNS", True, True),
        ]
        for revision, enabled, backend, can_pair, wifi2 in cases:
            with self.subTest(revision=revision, enabled=enabled, backend=backend):
                with patch("adbpilot.client.subprocess.run") as run:
                    run.side_effect = [self.version(revision), self.capabilities(revision, enabled, backend)]
                    capabilities = self.client.wifi_capabilities()
                self.assertEqual(capabilities.supports_pair, can_pair)
                self.assertEqual(capabilities.wifi2_host_compatible, wifi2)
                self.assertTrue(capabilities.supports_connect)
                commands = [" ".join(call.args[0]) for call in run.call_args_list]
                self.assertFalse(any("mdns check" in command for command in commands))

    def test_37_0_0_obsolete_backend_names_the_setting(self):
        with patch("adbpilot.client.subprocess.run") as run:
            run.side_effect = [self.version("37.0.0"), self.capabilities("37.0.0", backend="OPENSCREEN")]
            capabilities = self.client.wifi_capabilities()
        self.assertTrue(any("ADB_MDNS_OPENSCREEN=0" in warning for warning in capabilities.warnings))
        self.assertFalse(capabilities.wifi2_host_compatible)

    def test_network_probe_uses_service_version_not_android_version(self):
        service = "service: _adb-tls-connect\nmdns_service_version: 2.0\nbuild_version_sdk_full: 37\n"
        older = "service: _adb-tls-connect\nbuild_version_sdk_full: 37\n"
        with patch("adbpilot.client.subprocess.run") as run:
            run.side_effect = [
                self.version("37.0.1"),
                self.capabilities(),
                subprocess.CompletedProcess([], 0, service, ""),
            ]
            wifi2 = self.client.wifi_capabilities(probe_network=True)
            run.side_effect = [
                self.version("37.0.1"),
                self.capabilities(),
                subprocess.CompletedProcess([], 0, older, ""),
            ]
            wifi1 = self.client.wifi_capabilities(probe_network=True)
        self.assertEqual(wifi2.device_generation, "wifi2")
        self.assertEqual(wifi1.device_generation, "wifi1")
        self.assertEqual(wifi2.network_mdns_status, "visible")

    def test_ambiguous_success_does_not_claim_paired(self):
        with patch("adbpilot.client.subprocess.run") as run:
            run.side_effect = [
                self.version("34.0.5"),
                self.capabilities("34.0.5"),
                subprocess.CompletedProcess([], 0, "", ""),
            ]
            result = self.client.pair("192.168.1.10:37123", "123456")
        self.assertEqual(result.status, "ambiguous")
        self.assertFalse(result.paired)

    def test_pair_timeout_does_not_list_devices(self):
        self.client.devices = Mock(side_effect=AssertionError("should not list devices"))
        with patch("adbpilot.client.subprocess.run", side_effect=subprocess.TimeoutExpired(["adb", "pair"], 60)):
            with patch.object(self.client, "wifi_capabilities", return_value=self._ready()):
                with self.assertRaises(subprocess.TimeoutExpired):
                    self.client.pair("192.168.1.10:37123", "123456")
        self.client.devices.assert_not_called()

    def test_explicit_connect_failure_stays_pairing_success(self):
        self.client.devices = Mock(return_value=[])
        self.client.connect = Mock(side_effect=AdbConnectionError("failed", 1, category="connect_failed"))
        with patch.object(self.client, "wifi_capabilities", return_value=self._ready()):
            with patch("adbpilot.client.subprocess.run") as run:
                run.return_value = subprocess.CompletedProcess([], 0, "Successfully paired\n", "")
                with self.assertRaises(AdbConnectionError):
                    self.client.pair(
                        "192.168.1.10:37123",
                        "123456",
                        connect_address="192.168.1.10:5555",
                        wait_for_device_seconds=0,
                    )

    def test_classic_address_is_not_treated_as_tls_device(self):
        classic = Device("192.168.1.10:5555", "device")
        self.client.devices = Mock(return_value=[classic])
        with patch.object(self.client, "wifi_capabilities", return_value=self._ready()):
            with patch("adbpilot.client.subprocess.run") as run:
                run.return_value = subprocess.CompletedProcess([], 0, "Successfully paired\n", "")
                result = self.client.pair("192.168.1.10:37123", "123456", wait_for_device_seconds=0)
        self.assertEqual(result.status, "paired_waiting_for_connect")

    def test_qr_secret_is_not_forced_to_six_digits(self):
        tls = Device("adb-phone._adb-tls-connect._tcp", "device")
        self.client.devices = Mock(side_effect=[[], [tls]])
        with patch.object(self.client, "wifi_capabilities", return_value=self._ready()):
            with patch("adbpilot.client.subprocess.run") as run:
                run.return_value = subprocess.CompletedProcess([], 0, "Successfully paired\n", "secret-value")
                result = self.client.pair(
                    "192.168.1.10:37123",
                    "secret-value",
                    wait_for_device_seconds=0,
                    allow_qr_secret=True,
                )
        self.assertEqual(run.call_args.kwargs["input"], "secret-value\n")
        self.assertNotIn("secret-value", run.call_args.args[0])
        self.assertNotIn("secret-value", result.next_action)
        self.assertEqual(result.status, "paired_and_connected")

    def test_status_text_covers_each_controlled_state(self):
        capabilities = AdbWifiCapabilities("/adb", "", "37.0.1", wifi2_host_compatible=True, device_generation="wifi2")
        self.assertIn("主机具备 Wi-Fi 2.0 条件", capability_summary(capabilities))
        self.assertIn("设备支持 Wi-Fi 2.0", capability_summary(capabilities))
        for status in ("paired_and_connected", "paired_waiting_for_connect", "paired_connected_explicitly", "ambiguous"):
            text = pairing_status_text(WirelessPairingResult(status, False, False, "192.168.1.10:1", next_action="下一步"))
            self.assertIn("下一步", text)
            self.assertNotIn("123456", text)

    def _ready(self):
        return AdbWifiCapabilities("/test/adb", "37.0.1", "37.0.1", True, True, "true", "LIBADBMDNS", wifi2_host_compatible=True)


if __name__ == "__main__":
    unittest.main()
