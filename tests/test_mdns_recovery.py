import subprocess
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from adbpilot.client import AdbClient, parse_mdns_services
from adbpilot.errors import AdbCommandError
from adbpilot.mdns import (
    MdnsRecoveryConfig,
    MdnsRecoveryController,
    MdnsRecoveryState,
    MdnsService,
    SystemMdnsDiscovery,
    parse_dns_sd_services,
)
from adbpilot.models import CommandResult


EMPTY = "List of discovered mdns services\n"
CONNECT = "private-device-guid _adb-tls-connect._tcp. 192.0.2.8:32123\n"
CONFLICT = "private-device-guid (2) _adb-tls-pairing._tcp 192.0.2.8:32124\n"


class MdnsParsingTests(unittest.TestCase):
    def test_dns_sd_removals_and_interfaces_and_redaction(self):
        output = """Browsing for _adb-tls-pairing._tcp.local.
Timestamp A/R Flags if Domain Service Type Instance Name
11:01:02.123 Add 2 4 local. _adb-tls-pairing._tcp. private-guid (2)
11:01:02.124 Add 2 5 local. _adb-tls-pairing._tcp. private-guid (2)
11:01:02.125 Rmv 0 4 local. _adb-tls-pairing._tcp. private-guid (2)
11:01:02.126 Add 2 4 local. _adb-tls-connect._tcp. other-private-guid
11:01:02.127 Add 2 4 local. _http._tcp. irrelevant
11:01:02.128 Add invalid 4 local. _adb-tls-connect._tcp. malformed
11:01:02.129 Add 2 4 example.com. _adb-tls-connect._tcp. remote
"""
        services = parse_dns_sd_services(output)
        self.assertEqual(services, (
            MdnsService("_adb-tls-pairing._tcp", name_conflict=True),
            MdnsService("_adb-tls-connect._tcp"),
        ))
        self.assertNotIn("private", repr(services))
        self.assertEqual(parse_dns_sd_services(output + "11:01:02.130 Rmv 0 5 local. _adb-tls-pairing._tcp. private-guid (2)\n"),
                         (MdnsService("_adb-tls-connect._tcp"),))

    def test_empty_and_invalid_dns_sd_output_are_not_evidence(self):
        for output in ("", "Error code -65563", "garbage", "Browsing for _adb-tls-connect._tcp.local."):
            with self.subTest(output=output):
                self.assertEqual(parse_dns_sd_services(output), ())

    def test_adb_names_with_spaces_preserve_existing_pairing_lookup(self):
        services = parse_mdns_services(CONFLICT + CONNECT)
        self.assertEqual(services[0]["name"], "private-device-guid (2)")
        self.assertEqual(services[1]["address"], "192.0.2.8:32123")

    def test_config_defaults_and_disable(self):
        for value in (None, [], "invalid", 42):
            self.assertEqual(MdnsRecoveryConfig.from_gui_config({"wireless_recovery": value}), MdnsRecoveryConfig())
        self.assertFalse(MdnsRecoveryConfig.from_gui_config({"wireless_recovery": {"enabled": False}}).enabled)

    def test_config_invalid_values_fall_back_and_small_values_are_clamped(self):
        for value in (True, False, None, "10", -1, float("nan"), float("inf"), {}):
            config = {field: value for field in MdnsRecoveryConfig.__dataclass_fields__}
            with self.subTest(value=value):
                expected = MdnsRecoveryConfig(enabled=value) if isinstance(value, bool) else MdnsRecoveryConfig()
                self.assertEqual(MdnsRecoveryConfig.from_gui_config({"wireless_recovery": config}), expected)
        config = MdnsRecoveryConfig.from_gui_config({"wireless_recovery": {
            "probe_interval_seconds": 0, "restart_cooldown_seconds": 1,
            "system_browse_timeout_seconds": 9999, "confirm_delay_seconds": 0,
        }})
        self.assertEqual(config.probe_interval_seconds, 5)
        self.assertEqual(config.restart_cooldown_seconds, 30)
        self.assertEqual(config.system_browse_timeout_seconds, 5)
        self.assertEqual(config.confirm_delay_seconds, 0.1)


class MdnsRecoveryTests(unittest.TestCase):
    def setUp(self):
        with patch("adbpilot.client.PlatformAdapter.resolve_adb", return_value=Path("/test/adb")):
            self.client = AdbClient()
        self.controller = self.client.mdns_recovery
        self.adapter = Mock()
        self.adapter.discover_system_adb_mdns_services.return_value = SystemMdnsDiscovery(
            True, (MdnsService("_adb-tls-pairing._tcp"),),
        )
        self.mdns_outputs = [EMPTY, EMPTY, CONNECT]
        self.device_outputs = ["", "", ""]
        self.server_status = "mdns_enabled: true\nmdns_backend: LIBADBMDNS\n"
        self.commands = []
        self.now = 100.0
        clock = patch("adbpilot.mdns.time.monotonic", side_effect=lambda: self.now)
        sleep = patch("adbpilot.mdns.time.sleep")
        clock.start()
        sleep.start()
        self.addCleanup(clock.stop)
        self.addCleanup(sleep.stop)
        self.client.run = Mock(side_effect=self.command)
        self.client._stop_competing_adb_servers = Mock()

    def command(self, args, **kwargs):
        self.commands.append((tuple(args), self.controller.state))
        self.assertEqual(kwargs.get("timeout"), 30 if args == ["start-server"] else 3)
        if args == ["server-status"]:
            stdout = self.server_status
        elif args == ["mdns", "services"]:
            stdout = self.mdns_outputs.pop(0)
        elif args == ["devices", "-l"]:
            stdout = "List of devices attached\n" + self.device_outputs.pop(0)
        elif args == ["kill-server"]:
            self.assertFalse(kwargs["check"])
            return CommandResult(tuple(args), 1, "", "server not running")
        else:
            stdout = ""
        return CommandResult(tuple(args), 0, stdout, "")

    def recover(self, **kwargs):
        return self.client.recover_stale_mdns(adapter=self.adapter, **kwargs)

    def assert_no_restart(self):
        self.assertFalse(any(args[0] in {"kill-server", "start-server"} for args, _ in self.commands))

    def test_successful_recovery_uses_evidence_and_expected_states(self):
        messages = []
        result = self.recover(progress=messages.append)
        self.assertEqual(result.status, "recovered")
        self.assertTrue(result.restarted)
        self.assertEqual([state for args, state in self.commands if args == ("mdns", "services")], [
            MdnsRecoveryState.PROBING, MdnsRecoveryState.CONFIRMING, MdnsRecoveryState.VERIFYING,
        ])
        self.assertEqual([state for args, state in self.commands if args == ("kill-server",)], [MdnsRecoveryState.RESTARTING])
        self.assertEqual(self.controller.state, MdnsRecoveryState.COOLDOWN)
        self.assertEqual(result.adb_services_after, (MdnsService("_adb-tls-connect._tcp", "192.0.2.8:32123"),))
        self.client._stop_competing_adb_servers.assert_not_called()
        self.assertEqual(len(messages), 2)
        self.assertNotIn("private-device-guid", repr(result) + "".join(messages) + result.message)

    def test_any_device_state_protects_server(self):
        for state in ("device", "offline", "unauthorized", "no permissions"):
            with self.subTest(state=state):
                self.controller.last_probe = None
                self.device_outputs = [f"USB-SERIAL {state}\n"]
                result = self.recover()
                self.assertEqual(result.status, "device_present")
                self.assertEqual(result.devices_after[0].state, state)
                self.assertEqual(self.controller.state, MdnsRecoveryState.IDLE)
        self.adapter.discover_system_adb_mdns_services.assert_not_called()
        self.assert_no_restart()

    def test_device_appearing_during_probe_prevents_restart(self):
        self.device_outputs = ["", "USB-SERIAL unauthorized\n"]
        result = self.recover()
        self.assertEqual(result.status, "device_present")
        self.assert_no_restart()

    def test_adb_service_already_visible_skips_system_probe(self):
        self.mdns_outputs = [CONNECT]
        self.assertEqual(self.recover().status, "not_needed")
        self.adapter.discover_system_adb_mdns_services.assert_not_called()
        self.assert_no_restart()

    def test_adb_service_appears_on_confirmation_prevents_restart(self):
        self.mdns_outputs = [EMPTY, CONNECT]
        self.assertEqual(self.recover().status, "not_needed")
        self.assert_no_restart()

    def test_no_system_evidence_is_normal_and_rate_limited(self):
        self.adapter.discover_system_adb_mdns_services.return_value = SystemMdnsDiscovery(True)
        self.assertEqual(self.recover().status, "no_system_evidence")
        count = self.client.run.call_count
        self.now += 14.9
        self.assertEqual(self.recover().status, "interval")
        self.assertEqual(self.client.run.call_count, count)
        self.now += 0.1
        self.assertEqual(self.recover().status, "no_system_evidence")
        self.assert_no_restart()

    def test_missing_system_browser_does_not_restart(self):
        self.adapter.discover_system_adb_mdns_services.return_value = SystemMdnsDiscovery(False)
        self.assertEqual(self.recover().status, "unsupported")
        self.assert_no_restart()

    def test_mdns_disabled_or_unknown_never_triggers_recovery(self):
        for status in ("mdns_enabled: false", "", "mdns_enabled: unknown"):
            self.controller.last_probe = None
            self.device_outputs = [""]
            self.server_status = status
            self.assertEqual(self.recover().status, "unsupported")
        self.adapter.discover_system_adb_mdns_services.assert_not_called()
        self.assert_no_restart()

    def test_disabled_and_cancelled_do_not_issue_commands(self):
        self.controller.config = MdnsRecoveryConfig(enabled=False)
        self.assertEqual(self.recover().status, "disabled")
        self.assertEqual(self.recover(cancelled=lambda: True).status, "cancelled")
        self.client.run.assert_not_called()

    def test_cancellation_after_system_discovery_prevents_restart(self):
        stop = threading.Event()
        def discover(**_kwargs):
            stop.set()
            return SystemMdnsDiscovery(True, (MdnsService("_adb-tls-pairing._tcp"),))
        self.adapter.discover_system_adb_mdns_services.side_effect = discover
        self.assertEqual(self.recover(cancelled=stop.is_set).status, "cancelled")
        self.assert_no_restart()

    def test_conflict_visible_before_recovery_only_gives_advice(self):
        self.mdns_outputs = [CONFLICT]
        result = self.recover()
        self.assertEqual(result.status, "suspected_name_conflict")
        self.assertFalse(result.restarted)
        self.assertIn("配对码", result.message)
        self.assertNotIn("private", repr(result))
        self.assert_no_restart()

    def test_conflict_after_recovery_is_not_another_restart_trigger(self):
        self.mdns_outputs = [EMPTY, EMPTY, CONFLICT]
        result = self.recover()
        self.assertEqual(result.status, "suspected_name_conflict")
        self.assertTrue(result.restarted)
        self.now += 60
        self.device_outputs = [""]
        self.mdns_outputs = [CONFLICT]
        self.assertEqual(self.recover().status, "suspected_name_conflict")
        self.assertEqual(sum(args == ("kill-server",) for args, _ in self.commands), 1)

    def test_unverified_restart_still_enters_cooldown(self):
        self.mdns_outputs = [EMPTY, EMPTY, EMPTY]
        self.assertEqual(self.recover().status, "restarted_unverified")
        count = self.client.run.call_count
        self.now += 59.9
        self.assertEqual(self.recover().status, "cooldown")
        self.assertEqual(self.client.run.call_count, count)
        self.assertEqual(self.controller.state, MdnsRecoveryState.COOLDOWN)
        self.now += 0.1
        self.device_outputs = ["USB-SERIAL device\n"]
        self.assertEqual(self.recover().status, "device_present")
        self.assertEqual(self.controller.state, MdnsRecoveryState.IDLE)

    def test_start_failure_is_structured_redacted_and_cooled_down(self):
        def run(args, **kwargs):
            if args == ["start-server"]:
                raise AdbCommandError("private-guid secret", 1, "password", "guid")
            return self.command(args, **kwargs)
        self.client.run.side_effect = run
        result = self.recover()
        self.assertEqual(result.status, "restart_failed")
        self.assertFalse(result.restarted)
        self.assertEqual(self.controller.state, MdnsRecoveryState.COOLDOWN)
        self.assertNotIn("secret", repr(result) + result.message)
        self.assertEqual(self.recover().status, "cooldown")

    def test_slow_server_start_exceeds_probe_timeout_and_recovers(self):
        # Exercise the real command runner with a delayed startup ACK. A 3s
        # startup budget kills this child, just as it killed the ADB client.
        script = "import time; time.sleep(3.2); print('startup ACK', flush=True)"

        def run(args, **kwargs):
            if args == ["start-server"]:
                with patch.object(self.client, "_build_command", return_value=[sys.executable, "-c", script]):
                    return AdbClient.run(self.client, args, **kwargs)
            return self.command(args, **kwargs)

        self.client.run.side_effect = run
        result = self.recover()
        self.assertEqual(result.status, "recovered")
        self.assertTrue(result.restarted)
        self.assertEqual(result.adb_services_after[0].service_type, "_adb-tls-connect._tcp")
        self.assertEqual(self.controller.state, MdnsRecoveryState.COOLDOWN)

    def test_start_timeout_remains_bounded_and_enters_cooldown(self):
        def run(args, **kwargs):
            if args == ["start-server"]:
                self.assertEqual(kwargs["timeout"], 30)
                raise subprocess.TimeoutExpired("private-guid", kwargs["timeout"], "password")
            return self.command(args, **kwargs)

        self.client.run.side_effect = run
        result = self.recover()
        self.assertEqual(result.status, "restart_failed")
        self.assertFalse(result.restarted)
        self.assertEqual(self.controller.state, MdnsRecoveryState.COOLDOWN)
        self.assertNotIn("password", repr(result) + result.message)
        calls = self.client.run.call_count
        self.assertEqual(self.recover().status, "cooldown")
        self.assertEqual(self.client.run.call_count, calls)

    def test_pre_restart_timeout_never_restarts(self):
        self.client.run.side_effect = subprocess.TimeoutExpired("private-guid", 3, "password")
        result = self.recover()
        self.assertEqual(result.status, "probe_failed")
        self.assertEqual(self.controller.state, MdnsRecoveryState.IDLE)
        self.assertNotIn("password", result.message)
        self.assert_no_restart()

    def test_post_restart_timeout_preserves_restart_and_cooldown(self):
        def run(args, **kwargs):
            if self.controller.state == MdnsRecoveryState.VERIFYING:
                raise subprocess.TimeoutExpired("adb", 3)
            return self.command(args, **kwargs)
        self.client.run.side_effect = run
        result = self.recover()
        self.assertEqual(result.status, "restarted_unverified")
        self.assertTrue(result.restarted)
        self.assertEqual(self.recover().status, "cooldown")

    def test_malformed_service_output_never_becomes_empty_evidence(self):
        for output in ("unknown command mdns services\n", EMPTY + "unexpected error\n"):
            self.controller.last_probe = None
            self.device_outputs = [""]
            self.mdns_outputs = [output]
            self.assertEqual(self.recover().status, "probe_failed")
        self.assert_no_restart()

    def test_device_appears_after_restart_without_services(self):
        self.mdns_outputs = [EMPTY, EMPTY, EMPTY]
        self.device_outputs = ["", "", "example:5555 device\n"]
        result = self.recover()
        self.assertEqual(result.status, "recovered")
        self.assertEqual(result.devices_after[0].serial, "example:5555")

    def test_nonzero_server_status_is_unsupported(self):
        def run(args, **kwargs):
            if args == ["server-status"]:
                return CommandResult(tuple(args), 1, "mdns_enabled: true", "unsupported")
            return self.command(args, **kwargs)
        self.client.run.side_effect = run
        self.assertEqual(self.recover().status, "unsupported")
        self.assert_no_restart()

    def test_cancellation_on_final_device_check_prevents_kill(self):
        stop = threading.Event()
        def run(args, **kwargs):
            result = self.command(args, **kwargs)
            if args == ["devices", "-l"] and self.controller.state == MdnsRecoveryState.CONFIRMING:
                stop.set()
            return result
        self.client.run.side_effect = run
        self.assertEqual(self.recover(cancelled=stop.is_set).status, "cancelled")
        self.assert_no_restart()

    def test_cancellation_during_kill_finishes_start_and_keeps_cooldown(self):
        stop = threading.Event()
        def run(args, **kwargs):
            result = self.command(args, **kwargs)
            if args == ["kill-server"]:
                stop.set()
            return result
        self.client.run.side_effect = run
        result = self.recover(cancelled=stop.is_set)
        self.assertEqual(result.status, "cancelled")
        self.assertTrue(result.restarted)
        self.assertIn(("start-server",), [args for args, _ in self.commands])
        self.assertEqual(self.controller.state, MdnsRecoveryState.COOLDOWN)

    def test_manual_restart_cooldown_is_shared_across_clients(self):
        self.controller.note_restart()
        with patch("adbpilot.client.PlatformAdapter.resolve_adb", return_value=Path("/other/adb")):
            other = AdbClient()
        other.run = Mock()
        result = other.recover_stale_mdns(controller=self.controller, adapter=self.adapter)
        self.assertEqual(result.status, "cooldown")
        other.run.assert_not_called()

    def test_overlapping_attempt_does_not_run_commands(self):
        entered = threading.Event()
        release = threading.Event()
        results = []
        def discover(**_kwargs):
            entered.set()
            release.wait(2)
            return SystemMdnsDiscovery(True)
        self.adapter.discover_system_adb_mdns_services.side_effect = discover
        worker = threading.Thread(target=lambda: results.append(self.recover()))
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            count = self.client.run.call_count
            self.assertEqual(self.recover().status, "busy")
            self.assertEqual(self.client.run.call_count, count)
        finally:
            release.set()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(results[0].status, "no_system_evidence")
        self.assert_no_restart()


if __name__ == "__main__":
    unittest.main()
