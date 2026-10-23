import subprocess
import unittest
from unittest.mock import Mock, patch

from adbpilot.packages import (
    DependencyError,
    DependencyRequest,
    format_pip_progress,
    install_dependency,
    missing_imports,
)


class PackageTests(unittest.TestCase):
    def test_missing_imports_reports_only_absent_modules(self):
        with patch("builtins.__import__", side_effect=lambda name, *args: (_ for _ in ()).throw(ImportError(name)) if name == "qrcode" else object()):
            self.assertEqual(missing_imports(("qrcode", "PIL")), ("qrcode",))

    def test_install_skips_pip_when_imports_already_exist(self):
        runner = Mock()
        with patch("adbpilot.packages.missing_imports", return_value=()):
            result = install_dependency(
                DependencyRequest(requirement="qrcode[pil]", import_names=("qrcode", "PIL")),
                runner=runner,
            )
        runner.assert_not_called()
        self.assertIn("已安装", result)

    def test_install_runs_pip_and_rechecks_imports(self):
        messages: list[str] = []
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="ok", stderr="")
        runner = Mock(return_value=completed)
        with patch("adbpilot.packages.missing_imports", side_effect=[("qrcode",), ()]):
            result = install_dependency(
                DependencyRequest(requirement="qrcode[pil]", import_names=("qrcode", "PIL")),
                progress=messages.append,
                runner=runner,
            )
        command = runner.call_args.args[0]
        self.assertEqual(command[-1], "qrcode[pil]")
        self.assertIn("--progress-bar", command)
        self.assertEqual(messages, ["准备下载 qrcode[pil]", "安装完成"])
        self.assertIn("安装完成", result)

    def test_install_raises_pip_stderr(self):
        completed = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="network down")
        with patch("adbpilot.packages.missing_imports", return_value=("qrcode",)):
            with self.assertRaises(DependencyError) as caught:
                install_dependency(
                    DependencyRequest(requirement="qrcode[pil]", import_names=("qrcode",)),
                    runner=Mock(return_value=completed),
                )
        self.assertIn("network down", str(caught.exception))

    def test_format_pip_progress_separates_download_and_install(self):
        state: dict[str, int] = {}
        lines = [
            "Collecting qrcode",
            "  Downloading qrcode-8.2-py3-none-any.whl (45 kB)",
            "Progress 1024 of 46080",
            "Progress 46080 of 46080",
            "Installing collected packages: qrcode",
            "Installing qrcode...",
            "Progress 1 of 2",
            "Successfully installed qrcode-8.2",
            "Requirement already satisfied: pillow",
        ]
        messages = [format_pip_progress(line, state) for line in lines]
        self.assertEqual(
            messages,
            [
                "准备下载 qrcode",
                "正在下载 qrcode-8.2-py3-none-any.whl（45 kB）",
                "下载进度 2%（1.0 KB / 45.0 KB）",
                "下载进度 100%（45.0 KB / 45.0 KB）",
                "开始安装",
                "正在安装 qrcode",
                "正在安装 50%（1 / 2）",
                "安装完成",
                None,
            ],
        )


if __name__ == "__main__":
    unittest.main()
