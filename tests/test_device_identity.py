import subprocess
import unittest
from unittest.mock import Mock, patch

from adbpilot.client import AdbClient
from adbpilot.device_identity import DeviceIdentityCache
from adbpilot.models import CommandResult, Device


USB = Device("test-usb", "device", {"transport_id": "1"})
WIFI = Device("192.0.2.10:37001", "device", {"transport_id": "2"})


class DeviceIdentityTests(unittest.TestCase):
    def setUp(self):
        self.pending = []
        self.cache = DeviceIdentityCache()

        def thread(*, target, args, daemon):
            return Mock(start=lambda: self.pending.append(lambda: target(*args)))

        patcher = patch("adbpilot.device_identity.threading.Thread", side_effect=thread)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_worker(self):
        self.pending.pop(0)()

    def test_background_observation_caches_identities_and_keeps_selected_transport(self):
        client = AdbClient.__new__(AdbClient)
        output = f"List of devices attached\n{USB.serial} device transport_id:1\n{WIFI.serial} device transport_id:2\n"

        def run(args, **options):
            if args == ["devices", "-l"]:
                return CommandResult(tuple(args), 0, output, "")
            self.assertEqual(args, ["shell", "-n", "getprop", "ro.serialno"])
            self.assertEqual(options["timeout"], 2)
            return CommandResult(tuple(args), 0, "same-device", "")

        client.run = Mock(side_effect=run)
        for _ in range(2):
            devices = client.devices(deduplicate=False, observe_identities=True)
            self.assertEqual(devices, [USB, WIFI])
            self.assertEqual(client.deduplicate_cached_devices(devices), devices)
            self.run_worker()
        client.run.reset_mock()
        devices = client.devices(deduplicate=False, observe_identities=True)
        self.assertEqual(client.deduplicate_cached_devices(devices), [WIFI])
        self.assertEqual(client.deduplicate_cached_devices(devices, preferred_serial=USB.serial), [USB])
        client.run.assert_called_once_with(["devices", "-l"])
        self.assertFalse(self.pending)

    def test_explicit_restart_discards_pending_identity_and_reused_transport_id(self):
        lookup = Mock(return_value="first-device")
        self.cache.observe([USB, WIFI], lookup)
        with self.cache.restarting():
            self.assertIsNone(self.cache.lookup(USB))
        self.run_worker()
        lookup.assert_not_called()
        self.cache.observe([USB, WIFI], lookup)
        self.run_worker()
        self.assertEqual(self.cache.lookup(USB), "first-device")
        self.cache.observe([], lookup)
        self.cache.observe([USB, WIFI], lookup)
        self.assertIsNone(self.cache.lookup(USB))

    def test_failed_identity_lookup_has_backoff_without_blocking_other_transport(self):
        lookup = Mock(side_effect=[None, "second-device", "first-device"])
        with patch("adbpilot.device_identity.time.monotonic") as clock:
            clock.return_value = 100
            for _ in range(2):
                self.cache.observe([USB, WIFI], lookup)
                self.run_worker()
            clock.return_value = 129
            self.cache.observe([USB, WIFI], lookup)
            self.assertFalse(self.pending)
            self.assertIsNone(self.cache.lookup(USB))
            self.assertEqual(self.cache.lookup(WIFI), "second-device")
            clock.return_value = 130
            self.cache.observe([USB, WIFI], lookup)
            self.run_worker()
            self.assertEqual(self.cache.lookup(USB), "first-device")

    def test_host_query_failure_invalidates_resolved_entries(self):
        client = AdbClient.__new__(AdbClient)
        client._device_identities = self.cache
        self.cache.observe([USB, WIFI], lambda _identifier: "old-device")
        self.run_worker()
        client.run = Mock(side_effect=subprocess.TimeoutExpired(["adb", "devices"], 2))
        with self.assertRaises(subprocess.TimeoutExpired):
            client.devices(deduplicate=False, observe_identities=True)
        self.assertIsNone(self.cache.lookup(USB))


if __name__ == "__main__":
    unittest.main()
