import contextlib
import io
import json
import unittest

from adbpilot import ai_cli
from adbpilot.models import Device, RunningProcess


class FakeClient:
    def devices(self):
        return [Device("emulator-5554", "device", {"model": "Pixel_8"})]

    def shell(self, command, serial=None):
        return f"{serial}:{' '.join(command)}"

    def connect(self, address):
        return f"connected to {address}"

    def foreground_package(self, serial=None):
        return "com.example.app"

    def running_processes(self, serial=None):
        return [
            RunningProcess("1234", "u0_a123", "com.example.app", "com.example.app"),
            RunningProcess("222", "system", "surfaceflinger", ""),
        ]


class AiCliTests(unittest.TestCase):
    def test_schema_contains_current_operations(self):
        schema = ai_cli.operation_schema()

        self.assertIn("devices", schema["operations"])
        self.assertIn("install", schema["operations"])
        self.assertIn("foreground", schema["operations"])
        self.assertIn("processes", schema["operations"])
        self.assertIn("logcat-dump", schema["operations"])

    def test_schema_command_outputs_json_without_adb(self):
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            code = ai_cli.main(["schema"])

        data = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(data["program"], "adbpilot-ai")

    def test_devices_operation_returns_json_ready_data(self):
        data = ai_cli.op_devices(FakeClient(), {})

        self.assertEqual(data[0]["serial"], "emulator-5554")
        self.assertEqual(data[0]["details"]["model"], "Pixel_8")

    def test_shell_operation_accepts_string_and_array(self):
        client = FakeClient()

        string_result = ai_cli.op_shell(client, {"serial": "abc", "command": "getprop ro.product.model"})
        array_result = ai_cli.op_shell(client, {"serial": "abc", "command": ["getprop", "ro.product.model"]})

        self.assertEqual(string_result["stdout"], "abc:getprop ro.product.model")
        self.assertEqual(array_result["stdout"], "abc:getprop ro.product.model")

    def test_foreground_and_process_operations(self):
        client = FakeClient()

        foreground = ai_cli.op_foreground(client, {"serial": "abc"})
        processes = ai_cli.op_processes(client, {"serial": "abc", "packages_only": True})

        self.assertEqual(foreground["package"], "com.example.app")
        self.assertEqual(len(processes), 1)
        self.assertEqual(processes[0]["package"], "com.example.app")

    def test_parse_params_rejects_non_object(self):
        with self.assertRaises(ValueError):
            ai_cli.parse_params("[]")


if __name__ == "__main__":
    unittest.main()
