import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from adbpilot import cli
from adbpilot.errors import AdbWifiCapabilityError
from adbpilot.models import AdbWifiCapabilities, WirelessPairingResult


class CliWifiTests(unittest.TestCase):
    def test_parser_exposes_wifi_commands_without_code_argument(self):
        parser = cli.build_parser()
        capabilities = parser.parse_args(["wifi-capabilities", "--json"])
        pair = parser.parse_args(["pair", "192.168.1.10:37123", "--connect", "192.168.1.10:5555"])

        self.assertEqual(capabilities.command, "wifi-capabilities")
        self.assertTrue(capabilities.json)
        self.assertEqual(pair.address, "192.168.1.10:37123")
        self.assertEqual(pair.connect_address, "192.168.1.10:5555")
        self.assertFalse(hasattr(pair, "code"))

    def test_pair_reads_code_from_getpass(self):
        seen = {}

        class FakeClient:
            def pair(self, address, pairing_code, connect_address=None, wait_for_device_seconds=10.0):
                seen["code"] = pairing_code
                seen["connect"] = connect_address
                return WirelessPairingResult(
                    "paired_waiting_for_connect",
                    True,
                    False,
                    address,
                    next_action="请填写连接地址",
                )

        output = io.StringIO()
        with patch("adbpilot.cli.getpass.getpass", return_value="123456"), redirect_stdout(output):
            code = cli.handle_pair(FakeClient(), type("Args", (), {"address": "192.168.1.10:1", "connect_address": None})())

        self.assertEqual(code, 0)
        self.assertEqual(seen["code"], "123456")
        self.assertNotIn("123456", output.getvalue())

    def test_capability_error_exits_with_code_2(self):
        error = io.StringIO()
        with patch("adbpilot.cli.AdbClient", side_effect=AdbWifiCapabilityError("当前 adb 不能配对")), redirect_stderr(error):
            code = cli.main(["wifi-capabilities"])
        self.assertEqual(code, 2)
        self.assertIn("当前 adb 不能配对", error.getvalue())

    def test_capabilities_json_contains_summary(self):
        capabilities = AdbWifiCapabilities("/test/adb", "37.0.1", "37.0.1", True, True, "true", "LIBADBMDNS", "unknown", True)
        data = cli.capabilities_to_dict(capabilities)
        self.assertIn("主机具备 Wi-Fi 2.0 条件", data["summary"])


if __name__ == "__main__":
    unittest.main()
