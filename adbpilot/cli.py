"""Command line interface for AdbPilot."""

from __future__ import annotations

import argparse
import getpass
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import __version__
from . import lag, lag_report
from .client import AdbClient, capability_summary, pairing_status_text
from .errors import AdbPilotError, suggestion_for_device_state
from .history import format_device_row, load_devices, log_path, recent_events
from .models import AdbWifiCapabilities, Device, DeviceInfo, RunningProcess, WirelessPairingResult


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not hasattr(args, "handler") and not hasattr(args, "local_handler"):
        getattr(args, "help_parser", parser).print_help()
        return 0

    try:
        if hasattr(args, "local_handler"):
            return args.local_handler(args)
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

    history = subparsers.add_parser("history", help="查看已保存的连接历史和诊断日志")
    history.add_argument("--limit", type=int, default=40, help="显示最近多少条日志")
    history.add_argument("--json", action="store_true", help="以 JSON 输出设备历史")
    history.set_defaults(handler=handle_history)

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

    capabilities = subparsers.add_parser("wifi-capabilities", help="查看本机 adb 的无线调试能力")
    capabilities.add_argument("--json", action="store_true", help="以 JSON 输出")
    capabilities.set_defaults(handler=handle_wifi_capabilities)

    pair = subparsers.add_parser("pair", help="配对 Android 11+ 无线调试设备；配对码会单独询问，不进入命令参数")
    pair.add_argument("address", help="手机配对页面上的地址，例如 192.168.1.10:37123")
    pair.add_argument("--connect", dest="connect_address", help="可选。无线调试主页上的连接地址")
    pair.set_defaults(handler=handle_pair)

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

    diagnostic = subparsers.add_parser("lag", help="采集和对比无线调试卡顿证据")
    diagnostic.set_defaults(help_parser=diagnostic)
    diagnostic_commands = diagnostic.add_subparsers(dest="lag_command")
    capture = diagnostic_commands.add_parser("record", help="短时采集 ADB 命令耗时和可选的应用近期帧数据")
    capture.add_argument("-s", "--serial", help="adb devices -l 中的完整传输标识；多条在线时必须指定")
    capture.add_argument("--duration", type=int, default=lag.DEFAULT_DURATION,
                         help=f"采集秒数，{lag.MIN_DURATION}–{lag.MAX_DURATION} 秒，默认 {lag.DEFAULT_DURATION} 秒")
    capture.add_argument("--package", help="额外读取此应用的近期 HWUI 帧数据")
    capture.add_argument("--label", help="场景标签，例如 wifi-idle")
    capture.add_argument("--mark-on-enter", action="store_true", help="在交互终端按 Enter 标记卡顿时刻")
    capture.add_argument("--out", type=Path, required=True, help="本地 JSON 报告文件路径")
    capture.add_argument("--json", action="store_true", help="同时将报告以 JSON 输出到 stdout")
    capture.set_defaults(handler=handle_lag_record)

    compare = diagnostic_commands.add_parser("compare", help="离线比较两份诊断报告")
    compare.add_argument("baseline", type=Path, help="基线 JSON 报告")
    compare.add_argument("current", type=Path, help="当前场景 JSON 报告")
    compare.add_argument("--same-device", action="store_true", help="确认两份报告来自同一台手机")
    compare.add_argument("--json", action="store_true", help="以 JSON 输出比较结果")
    compare.set_defaults(local_handler=handle_lag_compare)

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


def handle_history(client: AdbClient, args: argparse.Namespace) -> int:
    devices = load_devices()
    if args.json:
        print(json.dumps({"log": str(log_path()), "devices": devices}, ensure_ascii=False, indent=2))
        return 0
    print(f"日志文件：{log_path()}")
    if not devices:
        print("还没有连接历史。")
    else:
        print("连接历史：")
        for device in devices:
            print(format_device_row(device))
    events = recent_events(max(args.limit, 0))
    if events:
        print("最近日志：")
        for line in events:
            print(line)
    return 0


def handle_connect(client: AdbClient, args: argparse.Namespace) -> int:
    print(client.connect(args.address))
    return 0


def handle_wifi_capabilities(client: AdbClient, args: argparse.Namespace) -> int:
    capabilities = client.wifi_capabilities(probe_network=True)
    if args.json:
        print(json.dumps(capabilities_to_dict(capabilities), ensure_ascii=False, indent=2))
        return 0
    print(capability_summary(capabilities))
    print(f"adb: {capabilities.adb_path}")
    print(f"revision: {capabilities.platform_tools_revision or '未知'}")
    for warning in capabilities.warnings:
        print(f"提示：{warning}")
    return 0


def handle_pair(client: AdbClient, args: argparse.Namespace) -> int:
    code = getpass.getpass("配对码: ")
    result = client.pair(args.address, code, connect_address=args.connect_address)
    print(pairing_status_text(result))
    return 0


def capabilities_to_dict(capabilities: AdbWifiCapabilities) -> dict[str, Any]:
    return {
        "adb_path": capabilities.adb_path,
        "platform_tools_revision": capabilities.platform_tools_revision,
        "supports_connect": capabilities.supports_connect,
        "supports_pair": capabilities.supports_pair,
        "mdns_enabled": capabilities.mdns_enabled,
        "mdns_backend": capabilities.mdns_backend,
        "network_mdns_status": capabilities.network_mdns_status,
        "wifi2_host_compatible": capabilities.wifi2_host_compatible,
        "device_generation": capabilities.device_generation,
        "warnings": list(capabilities.warnings),
        "summary": capability_summary(capabilities),
    }


def pairing_result_to_dict(result: WirelessPairingResult) -> dict[str, Any]:
    return {
        "status": result.status,
        "paired": result.paired,
        "connected": result.connected,
        "pairing_address": result.pairing_address,
        "connection_address": result.connection_address,
        "device_serial": result.device_serial,
        "next_action": result.next_action,
        "capabilities": capabilities_to_dict(result.capabilities) if result.capabilities else None,
    }


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


def handle_lag_record(client: AdbClient, args: argparse.Namespace) -> int:
    def show_event(event: str, detail: str) -> None:
        if event == "started":
            message = f"开始 {args.duration} 秒 ADB 采集，请在手机上复现卡顿。"
            if args.mark_on_enter:
                message += " 按 Enter 标记卡顿时刻。"
        elif event == "unresponsive":
            message = f"{lag_report.UNRESPONSIVE_MESSAGE} 后续每 {lag.BACKOFF_INTERVAL} 秒最多探测一次。"
        elif event == "checkpoint_saved":
            message = f"超时快照已保存至 {detail}"
        else:
            message = f"超时快照保存失败：{detail}"
        print(message, file=sys.stderr, flush=True)

    report = lag.record_to_path(client, args.out, serial=args.serial, package=args.package, label=args.label,
                                duration=args.duration, mark_on_enter=args.mark_on_enter, on_event=show_event)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_lag_summary(report)
        print(f"报告已保存至 {args.out}")

    reason = report["stop_reason"]
    if reason:
        print(lag.STOP_MESSAGES[reason], file=sys.stderr)
        return 130 if reason == "interrupted" else 2
    return 0


def handle_lag_compare(args: argparse.Namespace) -> int:
    baseline = lag_report.load_report(args.baseline)
    current = lag_report.load_report(args.current)
    comparison = lag_report.compare_reports(baseline, current, same_device=args.same_device)
    if args.json:
        print(json.dumps(comparison, ensure_ascii=False, indent=2))
        return 0

    print("基线")
    _print_lag_summary(comparison["baseline"])
    print("\n当前")
    _print_lag_summary(comparison["current"])
    delta = comparison["delta_ms"]
    if delta is None:
        print("\n仅并排展示。" + "；".join(comparison["comparison_limits"]) + "。")
    else:
        print(f"\nADB 命令耗时变化（当前减基线）  中位数 {delta['median_ms']:+.3f} ms  最大值 {delta['max_ms']:+.3f} ms")
    return 0


def _print_lag_summary(report: dict[str, Any]) -> None:
    def duration(value: float | None) -> str:
        return "无数据" if value is None else f"{value:.3f} ms"

    context = report["context"]
    counts = report["adb"]["counts"]
    frames = report["frames"]
    label = context["label"] or "未命名场景"
    transport = {"usb": "USB", "wifi": "Wi-Fi", "tcpip": "TCP/IP（物理链路未知）", "emulator": "模拟器"}
    statuses = {"complete": "已完成", "partial": "部分完成", "failed": "采集失败"}
    print(f"{label} · {transport.get(context['transport'], '未知传输')} · {statuses[report['status']]}"
          f" · {context['elapsed_ms'] / 1000:.1f}/{context['duration_seconds']:g} 秒")
    if report.get("diagnosis"):
        print(f"采集过程中检测到 {lag_report.UNRESPONSIVE_MESSAGE}")
    print(f"ADB 命令耗时  成功 {counts['ok']}  超时 {counts['timeout']}  失败 {counts['failed']}  跳过 {counts['skipped']}")
    skipped = report["adb"]["skipped_by_reason"]
    if any(skipped.values()):
        print(f"跳过原因  主机列表查询失败 {skipped['host_query_failed']}  探测降频 {skipped['backoff']}")
    print(f"中位数 {duration(report['adb']['median_ms'])}  最大值 {duration(report['adb']['max_ms'])}")
    if counts["ok"] < lag_report.MIN_SUCCESSFUL_PROBES:
        print(f"成功样本少于 {lag_report.MIN_SUCCESSFUL_PROBES} 条。")
    if frames["status"] != "not_requested":
        print(f"{context['package']} 近期 HWUI 帧  {frames['frame_count']} 帧 · {frames['window_count']} 个窗口"
              f" · 样本跨度 {duration(frames['span_ms'])}")
        print(f"帧完成耗时  中位数 {duration(frames['median_ms'])}  最大值 {duration(frames['max_ms'])}")
        frame_reasons = {
            "invalid_frame_data": "帧数据格式不完整", "windows_changed": "窗口或进程发生变化",
            "buffer_discontinuity": "前后帧缓冲无交集", "no_new_frames": "未取得新的有效帧",
            "capture_interrupted": "未完成前后帧采集", "frame_query_timeout": "帧查询超时",
            "frame_query_failed": "帧查询失败", "unsupported_frame_output": "设备未返回可识别的帧数据",
            "device_unresponsive": "设备命令连续超时，已跳过结束帧查询",
        }
        if frames["reasons"]:
            print("帧数据限制  " + "；".join(frame_reasons.get(reason, "其他限制") for reason in frames["reasons"]))
    if report["marks"]:
        print("卡顿标记（相对采集开始）  " + "、".join(f"{mark / 1000:.3f} 秒" for mark in report["marks"]))


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
