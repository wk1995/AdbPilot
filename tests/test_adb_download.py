import io
import stat
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from adbpilot.adb_download import (
    ADB_VERSIONS,
    AdbDownloadError,
    default_download_root,
    download_platform_tools,
    platform_tools_url,
    version_directory,
)


class FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = io.BytesIO(payload)
        self.headers = {"Content-Length": str(len(payload))}

    def read(self, size: int = -1) -> bytes:
        return self._payload.read(size)

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def package_bytes(name: str = "adb", content: bytes = b"adb") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        info = zipfile.ZipInfo(f"platform-tools/{name}")
        info.external_attr = (0o755 & 0xFFFF) << 16
        archive.writestr(info, content)
    return buffer.getvalue()


class AdbDownloadTests(unittest.TestCase):
    def test_platform_tools_url_uses_current_host(self):
        self.assertEqual(
            platform_tools_url("37.0.0", "Darwin"),
            "https://dl.google.com/android/repository/platform-tools_r37.0.0-darwin.zip",
        )
        self.assertIn("34.0.5", ADB_VERSIONS)

    def test_platform_tools_url_rejects_bad_version(self):
        with self.assertRaises(AdbDownloadError):
            platform_tools_url("latest", "Linux")

    def test_source_checkout_defaults_to_project_directory(self):
        source = Path("/work/AdbPilot/adbpilot/adb_download.py")
        with patch("adbpilot.adb_download.sys.frozen", False, create=True):
            self.assertEqual(default_download_root(source), Path("/work/AdbPilot"))

    def test_packaged_macos_app_defaults_beside_the_app(self):
        executable = Path("/Applications/AdbPilot.app/Contents/MacOS/AdbPilot")
        with patch("adbpilot.adb_download.sys.frozen", True, create=True), patch(
            "adbpilot.adb_download.sys.executable", str(executable)
        ), patch("adbpilot.adb_download.platform.system", return_value="Darwin"):
            self.assertEqual(default_download_root(), Path("/Applications"))

    def test_download_unpacks_adb_and_reports_progress(self):
        messages: list[str] = []
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            result = download_platform_tools(
                "37.0.0",
                root,
                system_name="Linux",
                progress=messages.append,
                opener=lambda *_args, **_kwargs: FakeResponse(package_bytes()),
            )
            self.assertEqual(result, version_directory(root, "37.0.0") / "platform-tools" / "adb")
            self.assertTrue(result.is_file())
            self.assertTrue(result.stat().st_mode & stat.S_IXUSR)
        self.assertIn("正在下载 37.0.0", messages)
        self.assertIn("下载完成", messages)

    def test_existing_download_is_reused(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            existing = version_directory(root, "37.0.0") / "platform-tools" / "adb"
            existing.parent.mkdir(parents=True)
            existing.write_text("ready", encoding="utf-8")
            result = download_platform_tools("37.0.0", root, system_name="Linux", opener=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not download")))
        self.assertEqual(result, existing)

    def test_failed_extraction_is_not_reused_as_a_complete_download(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("platform-tools/adb", b"incomplete")
            archive.writestr("../invalid-member", b"invalid")
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(AdbDownloadError):
                download_platform_tools(
                    "37.0.0", root, system_name="Linux",
                    opener=lambda *_args, **_kwargs: FakeResponse(buffer.getvalue()),
                )
            self.assertFalse(version_directory(root, "37.0.0").exists())
            result = download_platform_tools(
                "37.0.0", root, system_name="Linux",
                opener=lambda *_args, **_kwargs: FakeResponse(package_bytes(content=b"complete")),
            )
            self.assertEqual(result.read_bytes(), b"complete")

    def test_package_without_adb_does_not_publish_an_installation(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(AdbDownloadError):
                download_platform_tools(
                    "37.0.0", root, system_name="Linux",
                    opener=lambda *_args, **_kwargs: FakeResponse(package_bytes(name="NOTICE.txt")),
                )
            self.assertFalse(version_directory(root, "37.0.0").exists())

    def test_download_drops_special_permission_bits(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            info = zipfile.ZipInfo("platform-tools/adb")
            info.external_attr = (0o4755 & 0xFFFF) << 16
            archive.writestr(info, b"adb")
        with TemporaryDirectory() as temp_dir:
            result = download_platform_tools(
                "37.0.0",
                Path(temp_dir),
                system_name="Linux",
                opener=lambda *_args, **_kwargs: FakeResponse(buffer.getvalue()),
            )
            self.assertFalse(result.stat().st_mode & stat.S_ISUID)
            self.assertTrue(result.stat().st_mode & stat.S_IXUSR)

    def test_download_rejects_archive_paths_outside_destination(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("../outside.txt", "nope")
        with TemporaryDirectory() as temp_dir:
            with self.assertRaises(AdbDownloadError):
                download_platform_tools(
                    "37.0.0",
                    Path(temp_dir),
                    system_name="Linux",
                    opener=lambda *_args, **_kwargs: FakeResponse(buffer.getvalue()),
                )


if __name__ == "__main__":
    unittest.main()
