"""Command line interface for AdbPilot."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .client import AdbClient
from .errors import AdbPilotError, suggestion_for_device_state
from .models import Device, DeviceInfo, RunningProcess


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not hasattr(args, "handler"):
        parser.print_help()
        return 0

    try:
        client = AdbClient(adb_path=args.adb_path, timeout=args.timeout)
        return args.handler(client, args)
    except KeyboardInterrupt:
        print("\n已中断。", file=sys.stderr)
        return 130
    except AdbPilotError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    except (TimeoutError, subprocess.TimeoutExpired) as exc:
        print(f"错误：命令超时：{exc}", file=sys.stderr)
        return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="adbpilot", description="跨平台 ADB 设备管理工具")
    parser.add_argument("--adb-path", help="自定义 adb 可执行文件路径，也可使用 ADBPILOT_ADB 环境变量")
    parser.add_argument("--timeout", type=int, default=30, help="ADB 命令默认超时时间，单位秒")
    parser.add_argument("--version", action="version", version=f"adbpilot {__version__}")

    subparsers = parser.add_subparsers(dest="command")

    add_simple_command(subparsers, "version", "显示 adb 版本", handle_version)
    add_simple_command(subparsers, "restart-server", "重启 adb server", handle_restart_server)

    devices = subparsers.add_parser("devices", help="列出设备")
    devices.add_argument("--json", action="store_true", help="以 JSON 输出")
    devices.set_defaults(handler=handle_devices)

    info = subparsers.add_parser("info", help="显示设备基础信息")
    add_serial_arg(info)
    info.add_argument("--json", action="store_true", help="以 JSON 输出")
    info.set_defaults(handler=handle_info)

    connect = subparsers.add_parser("connect", help="连接无线调试设备，例如 192.168.1.10:5555")
    connect.add_argument("address")
    connect.set_defaults(handler=handle_connect)

    disconnect = subparsers.add_parser("disconnect", help="断开无线调试设备")
    disconnect.add_argument("address", nargs="?")
    disconnect.set_defaults(handler=handle_disconnect)

    install = subparsers.add_parser("install", help="安装 APK")
    add_serial_arg(install)
    install.add_argument("apk")
    install.add_argument("--no-replace", action="store_true", help="不使用 -r 覆盖安装")
    install.add_argument("--downgrade", action="store_true", help="允许降级安装")
    install.add_argument("--grant", action="store_true", help="安装时授予运行时权限")
    install.set_defaults(handler=handle_install)

    uninstall = subparsers.add_parser("uninstall", help="卸载应用")
    add_serial_arg(uninstall)
    uninstall.add_argument("package")
    uninstall.add_argument("--keep-data", action="store_true", help="保留应用数据")
    uninstall.set_defaults(handler=handle_uninstall)

    start = subparsers.add_parser("start", help="启动应用")
    add_serial_arg(start)
    start.add_argument("package")
    start.add_argument("--activity", help="完整 Activity 名称，例如 com.demo/.MainActivity")
    start.set_defaults(handler=handle_start)

    stop = subparsers.add_parser("stop", help="停止应用")
    add_serial_arg(stop)
    stop.add_argument("package")
    stop.set_defaults(handler=handle_stop)

    clear = subparsers.add_parser("clear", help="清除应用数据")
    add_serial_arg(clear)
    clear.add_argument("package")
    clear.set_defaults(handler=handle_clear)

    apps = subparsers.add_parser("apps", help="列出已安装应用")
    add_serial_arg(apps)
    apps.add_argument("--system", action="store_true", help="只显示系统应用")
    apps.add_argument("--third-party", action="store_true", help="只显示第三方应用")
    apps.add_argument("--filter", help="按包名过滤")
    apps.add_argument("--json", action="store_true", help="以 JSON 输出")
    apps.set_defaults(handler=handle_apps)

    app_info = subparsers.add_parser("app-info", help="显示应用详情")
    add_serial_arg(app_info)
    app_info.add_argument("package")
    app_info.add_argument("--json", action="store_true", help="以 JSON 输出")
    app_info.set_defaults(handler=handle_app_info)

    foreground = subparsers.add_parser("foreground", help="显示当前前台应用包名")
    add_serial_arg(foreground)
    foreground.add_argument("--json", action="store_true", help="以 JSON 输出")
    foreground.set_defaults(handler=handle_foreground)

    processes = subparsers.add_parser("processes", help="列出设备运行进程")
    add_serial_arg(processes)
    processes.add_argument("--json", action="store_true", help="以 JSON 输出")
    processes.add_argument("--packages-only", action="store_true", help="只显示看起来像应用包名的进程")
    processes.set_defaults(handler=handle_processes)

    export_apk = subparsers.add_parser("export-apk", help="导出应用 APK")
    add_serial_arg(export_apk)
    export_apk.add_argument("package")
    export_apk.add_argument("output_dir", type=Path)
    export_apk.set_defaults(handler=handle_export_apk)

    push = subparsers.add_parser("push", help="上传文件或目录到设备")
    add_serial_arg(push)
    push.add_argument("local")
    push.add_argument("remote")
    push.set_defaults(handler=handle_push)

    pull = subparsers.add_parser("pull", help="从设备下载文件或目录")
    add_serial_arg(pull)
    pull.add_argument("remote")
    pull.add_argument("local")
    pull.set_defaults(handler=handle_pull)

    shot = subparsers.add_parser("screencap", help="截屏并保存到本机")
    add_serial_arg(shot)
    shot.add_argument("output", type=Path)
    shot.set_defaults(handler=handle_screencap)

    record = subparsers.add_parser("screenrecord", help="录屏并保存到本机")
    add_serial_arg(record)
    record.add_argument("output", type=Path)
    record.add_argument("--seconds", type=int, default=10, help="录屏时长，单位秒")
    record.set_defaults(handler=handle_screenrecord)

    bugreport = subparsers.add_parser("bugreport", help="导出 bugreport")
    add_serial_arg(bugreport)
    bugreport.add_argument("output", type=Path)
    bugreport.set_defaults(handler=handle_bugreport)

    tap = subparsers.add_parser("tap", help="模拟点击")
    add_serial_arg(tap)
    tap.add_argument("x", type=int)
    tap.add_argument("y", type=int)
    tap.set_defaults(handler=handle_tap)

    swipe = subparsers.add_parser("swipe", help="模拟滑动")
    add_serial_arg(swipe)
    swipe.add_argument("x1", type=int)
    swipe.add_argument("y1", type=int)
    swipe.add_argument("x2", type=int)
    swipe.add_argument("y2", type=int)
    swipe.add_argument("--duration-ms", type=int, help="滑动持续时间，单位毫秒")
    swipe.set_defaults(handler=handle_swipe)

    text = subparsers.add_parser("text", help="输入文本")
    add_serial_arg(text)
    text.add_argument("text")
    text.set_defaults(handler=handle_text)

    key = subparsers.add_parser("key", help="发送按键事件，例如 HOME、BACK、KEYCODE_POWER 或 26")
    add_serial_arg(key)
    key.add_argument("key")
    key.set_defaults(handler=handle_key)

    shell = subparsers.add_parser("shell", help="执行 adb shell 命令")
    add_serial_arg(shell)
    shell.add_argument("shell_args", nargs=argparse.REMAINDER, help="shell 命令，例如 -- getprop ro.product.model")
    shell.set_defaults(handler=handle_shell)

    logcat = subparsers.add_parser("logcat", help="实时查看 logcat")
    add_serial_arg(logcat)
    logcat.add_argument("-o", "--output", type=Path, help="保存日志到文件")
    logcat.add_argument("--package", help="按运行中的包名过滤")
    logcat.add_argument("--tag", help="按 tag 过滤")
    logcat.add_argument("--level", choices=["V", "D", "I", "W", "E", "F", "S"], help="日志级别")
    logcat.set_defaults(handler=handle_logcat)

    return parser


def add_simple_command(subparsers: argparse._SubParsersAction[argparse.ArgumentParser], name: str, help_text: str, handler: Any) -> None:
    command = subparsers.add_parser(name, help=help_text)
    command.set_defaults(handler=handler)


def add_serial_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-s", "--serial", help="目标设备序列号；多设备场景必须指定")


def handle_version(client: AdbClient, args: argparse.Namespace) -> int:
    print(client.version())
    return 0


def handle_restart_server(client: AdbClient, args: argparse.Namespace) -> int:
    client.restart_server()
    print("adb server 已重启。")
    return 0


def handle_devices(client: AdbClient, args: argparse.Namespace) -> int:
    devices = client.devices()
    if args.json:
        print(json.dumps([device_to_dict(device) for device in devices], ensure_ascii=False, indent=2))
        return 0

    if not devices:
        print("未检测到设备。")
        return 0

    for device in devices:
        details = " ".join(f"{key}={value}" for key, value in device.details.items())
        suffix = f" {details}" if details else ""
        print(f"{device.serial}\t{device.state}{suffix}")
        if device.state != "device":
            print(f"  提示：{suggestion_for_device_state(device.state)}")
    return 0


def handle_info(client: AdbClient, args: argparse.Namespace) -> int:
    info = client.device_info(args.serial)
    data = device_info_to_dict(info)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0

    for key, value in data.items():
        print(f"{key}: {value}")
    return 0


def handle_connect(client: AdbClient, args: argparse.Namespace) -> int:
    print(client.connect(args.address))
    return 0


def handle_disconnect(client: AdbClient, args: argparse.Namespace) -> int:
    print(client.disconnect(args.address))
    return 0


def handle_install(client: AdbClient, args: argparse.Namespace) -> int:
    print(client.install(args.apk, args.serial, replace=not args.no_replace, downgrade=args.downgrade, grant=args.grant))
    return 0


def handle_uninstall(client: AdbClient, args: argparse.Namespace) -> int:
    print(client.uninstall(args.package, args.serial, keep_data=args.keep_data))
    return 0


def handle_start(client: AdbClient, args: argparse.Namespace) -> int:
    output = client.start_app(args.package, args.serial, args.activity)
    if output:
        print(output)
    return 0


def handle_stop(client: AdbClient, args: argparse.Namespace) -> int:
    output = client.stop_app(args.package, args.serial)
    if output:
        print(output)
    return 0


def handle_clear(client: AdbClient, args: argparse.Namespace) -> int:
    print(client.clear_app(args.package, args.serial))
    return 0


def handle_apps(client: AdbClient, args: argparse.Namespace) -> int:
    packages = client.list_packages(
        args.serial,
        system=args.system,
        third_party=args.third_party,
        filter_text=args.filter,
    )
    if args.json:
        print(json.dumps(packages, ensure_ascii=False, indent=2))
    else:
        for package in packages:
            print(package)
    return 0


def handle_app_info(client: AdbClient, args: argparse.Namespace) -> int:
    info = client.app_info(args.package, args.serial)
    if args.json:
        print(json.dumps(info, ensure_ascii=False, indent=2))
    else:
        for key, value in info.items():
            print(f"{key}: {value}")
    return 0


def handle_foreground(client: AdbClient, args: argparse.Namespace) -> int:
    package = client.foreground_package(args.serial)
    if args.json:
        print(json.dumps({"package": package}, ensure_ascii=False, indent=2))
    else:
        print(package)
    return 0


def handle_processes(client: AdbClient, args: argparse.Namespace) -> int:
    processes = client.running_processes(args.serial)
    if args.packages_only:
        processes = [process for process in processes if process.package]
    if args.json:
        print(json.dumps([process_to_dict(process) for process in processes], ensure_ascii=False, indent=2))
    else:
        for process in processes:
            package = f"\t{process.package}" if process.package else ""
            print(f"{process.pid}\t{process.user}\t{process.name}{package}")
    return 0


def handle_export_apk(client: AdbClient, args: argparse.Namespace) -> int:
    outputs = client.export_apk(args.package, args.output_dir, args.serial)
    for output in outputs:
        print(f"已导出：{output}")
    return 0


def handle_push(client: AdbClient, args: argparse.Namespace) -> int:
    print(client.push(args.local, args.remote, args.serial))
    return 0


def handle_pull(client: AdbClient, args: argparse.Namespace) -> int:
    print(client.pull(args.remote, args.local, args.serial))
    return 0


def handle_screencap(client: AdbClient, args: argparse.Namespace) -> int:
    output = client.screencap(args.output, args.serial)
    print(f"截图已保存：{output}")
    return 0


def handle_screenrecord(client: AdbClient, args: argparse.Namespace) -> int:
    output = client.screenrecord(args.output, args.serial, seconds=args.seconds)
    print(f"录屏已保存：{output}")
    return 0


def handle_bugreport(client: AdbClient, args: argparse.Namespace) -> int:
    output = client.bugreport(args.output, args.serial)
    print(f"bugreport 已导出：{output}")
    return 0


def handle_tap(client: AdbClient, args: argparse.Namespace) -> int:
    client.input_tap(args.x, args.y, args.serial)
    return 0


def handle_swipe(client: AdbClient, args: argparse.Namespace) -> int:
    client.input_swipe(args.x1, args.y1, args.x2, args.y2, args.serial, duration_ms=args.duration_ms)
    return 0


def handle_text(client: AdbClient, args: argparse.Namespace) -> int:
    client.input_text(args.text, args.serial)
    return 0


def handle_key(client: AdbClient, args: argparse.Namespace) -> int:
    client.input_keyevent(args.key, args.serial)
    return 0


def handle_shell(client: AdbClient, args: argparse.Namespace) -> int:
    command = args.shell_args
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        raise AdbPilotError("请提供 shell 命令，例如：adbpilot shell -- getprop ro.product.model")
    print(client.shell(command, args.serial), end="")
    return 0


def handle_logcat(client: AdbClient, args: argparse.Namespace) -> int:
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("w", encoding="utf-8", errors="replace") as output:
            return client.logcat(args.serial, output=output, package=args.package, tag=args.tag, level=args.level)
    return client.logcat(args.serial, package=args.package, tag=args.tag, level=args.level)


def device_to_dict(device: Device) -> dict[str, Any]:
    return {"serial": device.serial, "state": device.state, "details": dict(device.details)}


def device_info_to_dict(info: DeviceInfo) -> dict[str, str]:
    return {
        "serial": info.serial,
        "state": info.state,
        "android_version": info.android_version,
        "sdk": info.sdk,
        "brand": info.brand,
        "model": info.model,
        "product": info.product,
        "abi": info.abi,
        "battery_level": info.battery_level,
        "battery_status": info.battery_status,
    }


def process_to_dict(process: RunningProcess) -> dict[str, str]:
    return {
        "pid": process.pid,
        "user": process.user,
        "name": process.name,
        "package": process.package,
    }


if __name__ == "__main__":
    raise SystemExit(main())
