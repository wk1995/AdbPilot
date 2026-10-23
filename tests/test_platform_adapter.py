import unittest
import subprocess
import sys
import threading
from pathlib import Path
from unittest.mock import Mock, patch

from adbpilot.platform_adapter import PlatformAdapter
from adbpilot.mdns import MdnsService


class PlatformAdapterTests(unittest.TestCase):
    def test_mdns_missing_browser_is_unsupported(self):
        adapter = PlatformAdapter(env={})
        adapter.system = "linux"
        with patch("adbpilot.platform_adapter.shutil.which", return_value=None), patch(
            "adbpilot.platform_adapter.subprocess.Popen"
        ) as spawn:
            self.assertFalse(adapter.discover_system_adb_mdns_services().supported)
        spawn.assert_not_called()

    def test_mdns_spawn_failure_cleans_up_already_started_browser(self):
        adapter = PlatformAdapter(env={})
        process = Mock()
        process.poll.return_value = None
        process.communicate.return_value = ("", "")
        with patch("adbpilot.platform_adapter.shutil.which", return_value="/test/dns-sd"), patch(
            "adbpilot.platform_adapter.subprocess.Popen", side_effect=[process, OSError("unavailable")]
        ):
            self.assertFalse(adapter.discover_system_adb_mdns_services().supported)
        process.terminate.assert_called_once()
        process.communicate.assert_called_once()
        self.assertFalse(adapter._mdns_processes)

    def test_mdns_timeout_escalates_to_kill_and_preserves_output(self):
        adapter = PlatformAdapter(env={})
        processes = [Mock(), Mock()]
        for process in processes:
            process.poll.return_value = None
            output = "12:00:00.123 Add 2 4 local. _adb-tls-connect._tcp. private-guid\n"
            process.communicate.side_effect = [
                subprocess.TimeoutExpired("dns-sd", 1),
                subprocess.TimeoutExpired("dns-sd", 0.3),
                (output, ""), (output, ""),
            ]
        with patch("adbpilot.platform_adapter.shutil.which", return_value="/test/dns-sd"), patch(
            "adbpilot.platform_adapter.subprocess.Popen", side_effect=processes
        ) as spawn:
            result = adapter.discover_system_adb_mdns_services()
        self.assertTrue(result.supported)
        self.assertEqual(result.services, (MdnsService("_adb-tls-connect._tcp"),))
        self.assertEqual(spawn.call_count, 2)
        for process in processes:
            process.kill.assert_called_once()
        self.assertFalse(adapter._mdns_processes)

    def test_mdns_browser_failure_discards_partial_evidence(self):
        adapter = PlatformAdapter(env={})
        failed = Mock(returncode=1)
        failed.poll.return_value = 1
        failed.communicate.return_value = ("Error code -65563", "failure")
        other = Mock()
        other.poll.return_value = None
        other.communicate.return_value = ("", "")
        with patch("adbpilot.platform_adapter.shutil.which", return_value="/test/dns-sd"), patch(
            "adbpilot.platform_adapter.subprocess.Popen", side_effect=[failed, other]
        ):
            result = adapter.discover_system_adb_mdns_services()
        self.assertFalse(result.supported)
        self.assertEqual(result.services, ())
        other.terminate.assert_called_once()

    def test_mdns_real_children_are_parallel_and_reaped_on_deadline(self):
        # Surrogate browsers test OS process cleanup without touching Bonjour/ADB.
        adapter = PlatformAdapter(env={})
        processes = []
        real_popen = subprocess.Popen

        def spawn(command, **kwargs):
            service_type = command[2]
            row = f"12:00:00.123 Add 2 4 local. {service_type}. private-guid"
            script = f"import time; print({row!r}, flush=True); time.sleep(30)"
            process = real_popen([sys.executable, "-u", "-c", script], **kwargs)
            processes.append(process)
            return process

        with patch("adbpilot.platform_adapter.shutil.which", return_value="/test/dns-sd"), patch(
            "adbpilot.platform_adapter.subprocess.Popen", side_effect=spawn
        ):
            result = adapter.discover_system_adb_mdns_services(timeout=0.5)
        self.assertTrue(result.supported)
        self.assertEqual({s.service_type for s in result.services}, {"_adb-tls-pairing._tcp", "_adb-tls-connect._tcp"})
        self.assertEqual(len(processes), 2)
        for process in processes:
            self.assertIsNotNone(process.poll())
            self.assertTrue(process.stdout.closed)
            self.assertTrue(process.stderr.closed)
        self.assertFalse(adapter._mdns_processes)

    def test_shutdown_terminates_real_children_and_prevents_new_browsers(self):
        adapter = PlatformAdapter(env={})
        processes = []
        real_popen = subprocess.Popen
        started = threading.Event()
        results = []

        def spawn(_command, **kwargs):
            process = real_popen([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
            processes.append(process)
            if len(processes) == 2:
                started.set()
            return process

        with patch("adbpilot.platform_adapter.shutil.which", return_value="/test/dns-sd"), patch(
            "adbpilot.platform_adapter.subprocess.Popen", side_effect=spawn
        ) as popen:
            worker = threading.Thread(target=lambda: results.append(adapter.discover_system_adb_mdns_services(timeout=5)))
            worker.start()
            try:
                self.assertTrue(started.wait(2))
                adapter.cancel_mdns_discovery()
            finally:
                adapter.cancel_mdns_discovery()
                worker.join(2)
            self.assertFalse(worker.is_alive())
            self.assertFalse(adapter.discover_system_adb_mdns_services().supported)
            self.assertEqual(popen.call_count, 2)
        self.assertEqual(len(results), 1)
        self.assertTrue(all(p.poll() is not None for p in processes))
        self.assertFalse(adapter._mdns_processes)

    def test_discover_adb_paths_keeps_existing_unique_files(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "one" / "adb"
            second = root / "two" / "adb"
            first.parent.mkdir()
            second.parent.mkdir()
            first.write_text("", encoding="utf-8")
            second.write_text("", encoding="utf-8")
            missing = root / "missing" / "adb"

            adapter = PlatformAdapter(env={})
            with patch.object(adapter, "_candidate_paths", return_value=[first, first, missing, second]), patch(
                "adbpilot.platform_adapter.shutil.which", return_value=str(second)
            ), patch.object(adapter, "_which_from_login_shell", return_value=None):
                self.assertEqual(adapter.discover_adb_paths(), [first, second])

    def test_configured_path_takes_priority(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            adb = Path(tmp) / "adb"
            adb.write_text("", encoding="utf-8")

            adapter = PlatformAdapter(env={})

            self.assertEqual(adapter.resolve_adb(str(adb)), adb)

    def test_android_home_candidate(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            executable = "adb.exe" if PlatformAdapter().executable_name == "adb.exe" else "adb"
            adb = root / "platform-tools" / executable
            adb.parent.mkdir()
            adb.write_text("", encoding="utf-8")

            adapter = PlatformAdapter(env={"ANDROID_HOME": str(root)})

            self.assertEqual(adapter.resolve_adb(), Path(adb))

    def test_packaged_adb_candidate_from_pyinstaller_root(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            executable = PlatformAdapter().executable_name
            adb = root / "platform-tools" / executable
            adb.parent.mkdir()
            adb.write_text("", encoding="utf-8")

            adapter = PlatformAdapter(env={})
            with patch("sys._MEIPASS", str(root), create=True):
                self.assertEqual(adapter.resolve_adb(), adb)

    def test_macos_login_shell_path_is_used_when_gui_path_is_minimal(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shell = root / "zsh"
            shell.write_text("", encoding="utf-8")
            adb = root / "platform-tools" / "adb"
            adb.parent.mkdir()
            adb.write_text("", encoding="utf-8")

            adapter = PlatformAdapter(env={"SHELL": str(shell)})
            completed = subprocess.CompletedProcess([str(shell), "-lc", "command -v adb"], 0, stdout=f"{adb}\n", stderr="")
            # This exercises the shell fallback, regardless of ADB installations on the host.
            with patch.object(adapter, "_candidate_paths", return_value=[]), patch(
                "adbpilot.platform_adapter.shutil.which", return_value=None
            ), patch("adbpilot.platform_adapter.subprocess.run", return_value=completed):
                adapter.system = "darwin"
                self.assertEqual(adapter.resolve_adb(), adb)


if __name__ == "__main__":
    unittest.main()
