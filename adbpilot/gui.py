"""Tkinter desktop GUI for AdbPilot."""

from __future__ import annotations

import queue
import subprocess
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable

from . import __version__
from .client import AdbClient
from .errors import AdbPilotError
from .models import Device, RunningProcess

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except ImportError:  # Drag and drop is optional; browse buttons still work.
    DND_FILES = None
    TkinterDnD = None


ResultCallback = Callable[[Any], None]
BaseTk = TkinterDnD.Tk if TkinterDnD else tk.Tk

COLORS = {
    "app": "#f3f6fb",
    "surface": "#ffffff",
    "surface_alt": "#f8fafc",
    "border": "#d9e2ec",
    "text": "#111827",
    "muted": "#64748b",
    "accent": "#2563eb",
    "accent_hover": "#1d4ed8",
    "accent_soft": "#dbeafe",
    "success": "#047857",
    "warning": "#b45309",
    "danger": "#b91c1c",
}


class AdbPilotGui(BaseTk):
    """Windows-friendly visual interface backed by the existing AdbClient."""

    DEVICE_POLL_INTERVAL_MS = 3000

    def __init__(self) -> None:
        super().__init__()
        self.title(f"AdbPilot {__version__}")
        self.geometry("1220x800")
        self.minsize(1040, 700)
        self.configure(bg=COLORS["app"])

        self.client: AdbClient | None = None
        self.devices: list[Device] = []
        self.device_rows: dict[str, dict[str, Any]] = {}
        self.device_poll_running = False
        self.manual_refresh_in_progress = False
        self.floating_window: tk.Toplevel | None = None
        self.floating_devices_frame: ttk.Frame | None = None
        self.floating_status_var = tk.StringVar(value="监听中")
        self.locked_serial_var = tk.StringVar()
        self.lock_status_var = tk.StringVar(value="未锁定")
        self.result_queue: queue.Queue[tuple[str, str, Any, ResultCallback | None]] = queue.Queue()

        self.adb_path_var = tk.StringVar()
        self.timeout_var = tk.StringVar(value="30")
        self.selected_serial_var = tk.StringVar()
        self.status_var = tk.StringVar(value="就绪")

        self._configure_style()
        self.protocol("WM_DELETE_WINDOW", self._close_app)
        self._build_ui()
        if DND_FILES is None:
            self._append_output("提示：安装 tkinterdnd2 后可将文件拖到路径输入框。\n")
        self.after(100, self._process_results)
        self.after(500, self._poll_devices)

    def _configure_style(self) -> None:
        self.option_add("*Font", ("Microsoft YaHei UI", 10))
        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")

        style.configure("App.TFrame", background=COLORS["app"])
        style.configure("TFrame", background=COLORS["surface"])
        style.configure("Surface.TFrame", background=COLORS["surface"], relief=tk.FLAT)
        style.configure("Sidebar.TFrame", background=COLORS["surface"], relief=tk.FLAT)
        style.configure("Toolbar.TFrame", background=COLORS["surface"])
        style.configure("CommandBar.TFrame", background=COLORS["surface"])
        style.configure("TLabel", background=COLORS["surface"], foreground=COLORS["text"], padding=(0, 2))
        style.configure("Muted.TLabel", background=COLORS["surface"], foreground=COLORS["muted"])
        style.configure("AppMuted.TLabel", background=COLORS["app"], foreground=COLORS["muted"])
        style.configure("Header.TLabel", background=COLORS["surface"], foreground=COLORS["text"], font=("Microsoft YaHei UI", 13, "bold"))
        style.configure("Title.TLabel", background=COLORS["surface"], foreground=COLORS["text"], font=("Microsoft YaHei UI", 18, "bold"))
        style.configure("Section.TLabel", background=COLORS["surface"], foreground=COLORS["text"], font=("Microsoft YaHei UI", 11, "bold"))
        style.configure("Status.TLabel", background=COLORS["surface"], foreground=COLORS["muted"])
        style.configure("TButton", padding=(12, 6), background=COLORS["surface_alt"], foreground=COLORS["text"], bordercolor=COLORS["border"])
        style.map("TButton", background=[("active", "#eef2f7"), ("pressed", "#e5e7eb")])
        style.configure("Primary.TButton", padding=(14, 6), background=COLORS["accent"], foreground="#ffffff", bordercolor=COLORS["accent"])
        style.map("Primary.TButton", background=[("active", COLORS["accent_hover"]), ("pressed", COLORS["accent_hover"])])
        style.configure("Quiet.TButton", padding=(10, 5), background=COLORS["surface"], foreground=COLORS["text"], bordercolor=COLORS["border"])
        style.configure("Danger.TButton", padding=(10, 5), background="#fef2f2", foreground=COLORS["danger"], bordercolor="#fecaca")
        style.configure("TEntry", fieldbackground="#ffffff", bordercolor=COLORS["border"], lightcolor=COLORS["border"], darkcolor=COLORS["border"], padding=(6, 5))
        style.configure("TCombobox", fieldbackground="#ffffff", bordercolor=COLORS["border"], padding=(6, 5))
        style.configure("TLabelframe", background=COLORS["surface"], bordercolor=COLORS["border"], relief=tk.SOLID)
        style.configure("TLabelframe.Label", background=COLORS["surface"], foreground=COLORS["text"], font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("TNotebook", background=COLORS["surface"], borderwidth=0)
        style.configure("TNotebook.Tab", padding=(14, 8), background=COLORS["surface_alt"], foreground=COLORS["muted"])
        style.map("TNotebook.Tab", background=[("selected", COLORS["surface"])], foreground=[("selected", COLORS["accent"])])
        style.configure("Treeview", background="#ffffff", fieldbackground="#ffffff", foreground=COLORS["text"], rowheight=26, bordercolor=COLORS["border"], borderwidth=1)
        style.configure("Treeview.Heading", background=COLORS["surface_alt"], foreground=COLORS["muted"], font=("Microsoft YaHei UI", 9, "bold"), padding=(4, 6))
        style.map("Treeview", background=[("selected", COLORS["accent_soft"])], foreground=[("selected", COLORS["text"])])
        style.configure("TCheckbutton", background=COLORS["surface"], foreground=COLORS["text"])

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=(16, 14, 16, 12), style="App.TFrame")
        root.pack(fill=tk.BOTH, expand=True)

        self._build_toolbar(root)

        body = ttk.Frame(root, style="App.TFrame")
        body.pack(fill=tk.BOTH, expand=True)
        self._build_device_panel(body)
        self._build_tabs(body)
        self._build_output(root)

    def _build_toolbar(self, parent: ttk.Frame) -> None:
        toolbar = ttk.Frame(parent, padding=(16, 14), style="Toolbar.TFrame")
        toolbar.pack(fill=tk.X, pady=(0, 12))

        title_area = ttk.Frame(toolbar, style="Toolbar.TFrame")
        title_area.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(title_area, text="AdbPilot", style="Title.TLabel").pack(anchor=tk.W)
        ttk.Label(title_area, text="Android 设备调试、日志和文件操作工作台", style="Muted.TLabel").pack(anchor=tk.W, pady=(2, 0))

        commands = ttk.Frame(toolbar, style="CommandBar.TFrame")
        commands.pack(side=tk.RIGHT, anchor=tk.NE)

        ttk.Button(commands, text="检测版本", command=self.show_version, style="Quiet.TButton").pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(commands, text="重启服务", command=self.restart_server, style="Quiet.TButton").pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(commands, text="刷新设备", command=self.refresh_devices, style="Primary.TButton").pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(commands, text="悬浮窗", command=self.enter_floating_mode, style="Quiet.TButton").pack(side=tk.LEFT)

        path_row = ttk.Frame(toolbar, style="Toolbar.TFrame")
        path_row.pack(fill=tk.X, pady=(12, 0))

        ttk.Label(path_row, text="ADB 路径", style="Muted.TLabel").pack(side=tk.LEFT)
        adb_entry = ttk.Entry(path_row, textvariable=self.adb_path_var, width=48)
        adb_entry.pack(side=tk.LEFT, padx=(8, 4), fill=tk.X, expand=True)
        self._register_file_drop(adb_entry, self.adb_path_var)
        ttk.Button(path_row, text="浏览", command=self._browse_adb, style="Quiet.TButton").pack(side=tk.LEFT, padx=4)

        ttk.Label(path_row, text="超时", style="Muted.TLabel").pack(side=tk.LEFT, padx=(12, 4))
        ttk.Entry(path_row, textvariable=self.timeout_var, width=6).pack(side=tk.LEFT)
        ttk.Label(path_row, text="秒", style="Muted.TLabel").pack(side=tk.LEFT, padx=(2, 0))

    def _build_device_panel(self, parent: ttk.Frame) -> None:
        panel = ttk.Frame(parent, padding=(14, 14), style="Sidebar.TFrame")
        panel.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 12))
        panel.pack_propagate(False)
        panel.configure(width=360)

        header = ttk.Frame(panel, style="Sidebar.TFrame")
        header.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(header, text="设备中心", style="Header.TLabel").pack(side=tk.LEFT)
        ttk.Label(header, text="自动监听", style="Status.TLabel").pack(side=tk.RIGHT)

        columns = ("serial", "state", "product", "model", "device", "transport")
        self.device_table = ttk.Treeview(panel, columns=columns, show="headings", height=5)
        headings = {
            "serial": "序列号",
            "state": "状态",
            "product": "Product",
            "model": "Model",
            "device": "Device",
            "transport": "Transport",
        }
        widths = {
            "serial": 132,
            "state": 74,
            "product": 0,
            "model": 118,
            "device": 0,
            "transport": 52,
        }
        for column in columns:
            self.device_table.heading(column, text=headings[column])
            self.device_table.column(column, width=widths[column], anchor=tk.W)
            if widths[column] == 0:
                self.device_table.column(column, minwidth=0, stretch=False)
        self.device_table.pack(fill=tk.X, pady=(0, 12))
        self.device_table.bind("<<TreeviewSelect>>", self._on_device_selected)

        side = ttk.Frame(panel, style="Sidebar.TFrame")
        side.pack(fill=tk.X)
        ttk.Label(side, text="当前设备", style="Section.TLabel").pack(anchor=tk.W)
        ttk.Entry(side, textvariable=self.selected_serial_var, width=28).pack(fill=tk.X, pady=(6, 8))
        ttk.Button(side, text="设备详情", command=self.show_device_info, style="Quiet.TButton").pack(fill=tk.X, pady=2)
        ttk.Button(side, text="锁定当前设备", command=self.lock_current_device, style="Primary.TButton").pack(fill=tk.X, pady=(8, 2))
        ttk.Button(side, text="解除锁定", command=self.unlock_device, style="Quiet.TButton").pack(fill=tk.X, pady=2)
        ttk.Label(side, textvariable=self.lock_status_var, style="Status.TLabel", wraplength=320).pack(anchor=tk.W, pady=(6, 0))

        wireless = ttk.LabelFrame(side, text="无线调试")
        wireless.pack(fill=tk.X, pady=(14, 0))
        self.connect_addr_var = tk.StringVar(value="192.168.1.10:5555")
        ttk.Entry(wireless, textvariable=self.connect_addr_var, width=28).pack(fill=tk.X, padx=6, pady=(6, 4))
        wireless_actions = ttk.Frame(wireless, style="Surface.TFrame")
        wireless_actions.pack(fill=tk.X, padx=6, pady=(2, 6))
        ttk.Button(wireless_actions, text="连接", command=self.connect_device, style="Primary.TButton").pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        ttk.Button(wireless_actions, text="断开", command=self.disconnect_device, style="Quiet.TButton").pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))

    def _build_tabs(self, parent: ttk.Frame) -> None:
        self.tabs = ttk.Notebook(parent)
        self.tabs.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._build_apps_tab(self.tabs)
        self._build_file_tab(self.tabs)
        self._build_screen_tab(self.tabs)
        self._build_shell_tab(self.tabs)
        self._build_log_tab(self.tabs)

    def _build_apps_tab(self, tabs: ttk.Notebook) -> None:
        tab = ttk.Frame(tabs, padding=16, style="Surface.TFrame")
        tabs.add(tab, text="应用")

        ttk.Label(tab, text="APK 文件").grid(row=0, column=0, sticky=tk.W)
        self.apk_path_var = tk.StringVar()
        apk_entry = ttk.Entry(tab, textvariable=self.apk_path_var)
        apk_entry.grid(row=0, column=1, sticky=tk.EW, padx=8)
        self._register_file_drop(apk_entry, self.apk_path_var)
        ttk.Button(tab, text="选择 APK", command=self._browse_apk).grid(row=0, column=2, sticky=tk.EW)

        install_options = ttk.Frame(tab)
        install_options.grid(row=1, column=1, sticky=tk.W, padx=8, pady=6)
        self.install_replace_var = tk.BooleanVar(value=True)
        self.install_grant_var = tk.BooleanVar(value=False)
        self.install_downgrade_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(install_options, text="覆盖安装", variable=self.install_replace_var).pack(side=tk.LEFT)
        ttk.Checkbutton(install_options, text="授予权限", variable=self.install_grant_var).pack(side=tk.LEFT, padx=10)
        ttk.Checkbutton(install_options, text="允许降级", variable=self.install_downgrade_var).pack(side=tk.LEFT)
        ttk.Button(tab, text="安装", command=self.install_apk).grid(row=1, column=2, sticky=tk.EW)

        ttk.Label(tab, text="包名").grid(row=2, column=0, sticky=tk.W, pady=(12, 0))
        self.package_var = tk.StringVar()
        ttk.Entry(tab, textvariable=self.package_var).grid(row=2, column=1, sticky=tk.EW, padx=8, pady=(12, 0))

        actions = ttk.Frame(tab)
        actions.grid(row=3, column=1, columnspan=2, sticky=tk.W, padx=8, pady=8)
        for text, command in [
            ("启动", self.start_app),
            ("停止", self.stop_app),
            ("清除数据", self.clear_app),
            ("卸载", self.uninstall_app),
            ("详情", self.app_info),
            ("导出 APK", self.export_apk),
        ]:
            ttk.Button(actions, text=text, command=command).pack(side=tk.LEFT, padx=(0, 6))

        list_bar = ttk.Frame(tab)
        list_bar.grid(row=4, column=0, columnspan=3, sticky=tk.EW, pady=(8, 4))
        self.package_filter_var = tk.StringVar()
        ttk.Label(list_bar, text="过滤").pack(side=tk.LEFT)
        ttk.Entry(list_bar, textvariable=self.package_filter_var, width=28).pack(side=tk.LEFT, padx=8)
        self.third_party_only_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(list_bar, text="仅第三方", variable=self.third_party_only_var).pack(side=tk.LEFT)
        ttk.Button(list_bar, text="列出应用", command=self.list_apps).pack(side=tk.LEFT, padx=8)

        self.package_list = tk.Listbox(tab, height=12)
        self._style_listbox(self.package_list)
        self.package_list.grid(row=5, column=0, columnspan=3, sticky=tk.NSEW)
        self.package_list.bind("<<ListboxSelect>>", self._on_package_selected)

        tab.columnconfigure(1, weight=1)
        tab.rowconfigure(5, weight=1)

    def _build_file_tab(self, tabs: ttk.Notebook) -> None:
        tab = ttk.Frame(tabs, padding=16, style="Surface.TFrame")
        tabs.add(tab, text="文件")

        ttk.Label(tab, text="本机路径").grid(row=0, column=0, sticky=tk.W)
        self.local_path_var = tk.StringVar()
        local_entry = ttk.Entry(tab, textvariable=self.local_path_var)
        local_entry.grid(row=0, column=1, sticky=tk.EW, padx=8)
        self._register_file_drop(local_entry, self.local_path_var)
        ttk.Button(tab, text="选择文件", command=self._browse_local_file).grid(row=0, column=2, sticky=tk.EW)

        ttk.Label(tab, text="设备路径").grid(row=1, column=0, sticky=tk.W, pady=8)
        self.remote_path_var = tk.StringVar(value="/sdcard/Download/")
        ttk.Entry(tab, textvariable=self.remote_path_var).grid(row=1, column=1, sticky=tk.EW, padx=8, pady=8)

        file_actions = ttk.Frame(tab)
        file_actions.grid(row=2, column=1, columnspan=2, sticky=tk.W, padx=8)
        ttk.Button(file_actions, text="上传 Push", command=self.push_file).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(file_actions, text="下载 Pull", command=self.pull_file).pack(side=tk.LEFT)

        ttk.Separator(tab).grid(row=3, column=0, columnspan=3, sticky=tk.EW, pady=16)

        ttk.Label(tab, text="截图保存").grid(row=4, column=0, sticky=tk.W)
        self.screencap_path_var = tk.StringVar(value=str(Path.cwd() / "screenshots" / "screen.png"))
        screencap_entry = ttk.Entry(tab, textvariable=self.screencap_path_var)
        screencap_entry.grid(row=4, column=1, sticky=tk.EW, padx=8)
        self._register_file_drop(screencap_entry, self.screencap_path_var)
        ttk.Button(tab, text="截屏", command=self.screencap).grid(row=4, column=2, sticky=tk.EW)

        ttk.Label(tab, text="Bugreport").grid(row=5, column=0, sticky=tk.W, pady=8)
        self.bugreport_path_var = tk.StringVar(value=str(Path.cwd() / "reports" / "bugreport.zip"))
        bugreport_entry = ttk.Entry(tab, textvariable=self.bugreport_path_var)
        bugreport_entry.grid(row=5, column=1, sticky=tk.EW, padx=8, pady=8)
        self._register_file_drop(bugreport_entry, self.bugreport_path_var)
        ttk.Button(tab, text="导出", command=self.bugreport).grid(row=5, column=2, sticky=tk.EW)

        tab.columnconfigure(1, weight=1)

    def _build_screen_tab(self, tabs: ttk.Notebook) -> None:
        tab = ttk.Frame(tabs, padding=16, style="Surface.TFrame")
        tabs.add(tab, text="屏幕与输入")

        ttk.Label(tab, text="录屏保存").grid(row=0, column=0, sticky=tk.W)
        self.record_path_var = tk.StringVar(value=str(Path.cwd() / "screenshots" / "record.mp4"))
        record_entry = ttk.Entry(tab, textvariable=self.record_path_var)
        record_entry.grid(row=0, column=1, sticky=tk.EW, padx=8)
        self._register_file_drop(record_entry, self.record_path_var)
        ttk.Label(tab, text="秒数").grid(row=0, column=2, sticky=tk.E)
        self.record_seconds_var = tk.StringVar(value="10")
        ttk.Entry(tab, textvariable=self.record_seconds_var, width=6).grid(row=0, column=3, sticky=tk.W, padx=4)
        ttk.Button(tab, text="录屏", command=self.screenrecord).grid(row=0, column=4, sticky=tk.EW)

        tap_frame = ttk.LabelFrame(tab, text="点击")
        tap_frame.grid(row=1, column=0, columnspan=5, sticky=tk.EW, pady=12)
        self.tap_x_var = tk.StringVar(value="300")
        self.tap_y_var = tk.StringVar(value="800")
        ttk.Label(tap_frame, text="X").pack(side=tk.LEFT, padx=(8, 4))
        ttk.Entry(tap_frame, textvariable=self.tap_x_var, width=8).pack(side=tk.LEFT)
        ttk.Label(tap_frame, text="Y").pack(side=tk.LEFT, padx=(12, 4))
        ttk.Entry(tap_frame, textvariable=self.tap_y_var, width=8).pack(side=tk.LEFT)
        ttk.Button(tap_frame, text="点击", command=self.tap).pack(side=tk.LEFT, padx=12)

        swipe_frame = ttk.LabelFrame(tab, text="滑动")
        swipe_frame.grid(row=2, column=0, columnspan=5, sticky=tk.EW, pady=4)
        self.swipe_vars = [tk.StringVar(value=value) for value in ("500", "1500", "500", "300", "400")]
        for label, var in zip(("X1", "Y1", "X2", "Y2", "毫秒"), self.swipe_vars):
            ttk.Label(swipe_frame, text=label).pack(side=tk.LEFT, padx=(8, 4))
            ttk.Entry(swipe_frame, textvariable=var, width=8).pack(side=tk.LEFT)
        ttk.Button(swipe_frame, text="滑动", command=self.swipe).pack(side=tk.LEFT, padx=12)

        text_frame = ttk.LabelFrame(tab, text="文本与按键")
        text_frame.grid(row=3, column=0, columnspan=5, sticky=tk.EW, pady=12)
        self.input_text_var = tk.StringVar(value="hello")
        ttk.Entry(text_frame, textvariable=self.input_text_var, width=28).pack(side=tk.LEFT, padx=8, pady=8)
        ttk.Button(text_frame, text="输入文本", command=self.input_text).pack(side=tk.LEFT)
        for key in ("HOME", "BACK", "MENU", "POWER", "VOLUME_UP", "VOLUME_DOWN"):
            ttk.Button(text_frame, text=key, command=lambda key=key: self.keyevent(key)).pack(side=tk.LEFT, padx=(8, 0))

        tab.columnconfigure(1, weight=1)

    def _build_shell_tab(self, tabs: ttk.Notebook) -> None:
        tab = ttk.Frame(tabs, padding=16, style="Surface.TFrame")
        tabs.add(tab, text="Shell")

        ttk.Label(tab, text="adb shell").pack(anchor=tk.W)
        command_bar = ttk.Frame(tab)
        command_bar.pack(fill=tk.X, pady=8)
        self.shell_command_var = tk.StringVar(value="getprop ro.product.model")
        ttk.Entry(command_bar, textvariable=self.shell_command_var).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(command_bar, text="执行", command=self.run_shell).pack(side=tk.LEFT, padx=(8, 0))

        self.shell_output = tk.Text(tab, height=14, wrap=tk.WORD)
        self._style_text(self.shell_output)
        self.shell_output.pack(fill=tk.BOTH, expand=True)

    def _build_log_tab(self, tabs: ttk.Notebook) -> None:
        tab = ttk.Frame(tabs, padding=16, style="Surface.TFrame")
        tabs.add(tab, text="日志")

        filters = ttk.Frame(tab)
        filters.pack(fill=tk.X)
        self.log_package_var = tk.StringVar()
        self.log_tag_var = tk.StringVar()
        self.log_level_var = tk.StringVar(value="")
        ttk.Label(filters, text="包名").pack(side=tk.LEFT)
        ttk.Entry(filters, textvariable=self.log_package_var, width=28).pack(side=tk.LEFT, padx=6)
        ttk.Button(filters, text="当前前台", command=self.use_foreground_package).pack(side=tk.LEFT)
        ttk.Label(filters, text="Tag").pack(side=tk.LEFT, padx=(10, 0))
        ttk.Entry(filters, textvariable=self.log_tag_var, width=20).pack(side=tk.LEFT, padx=6)
        ttk.Label(filters, text="级别").pack(side=tk.LEFT, padx=(10, 0))
        ttk.Combobox(filters, textvariable=self.log_level_var, values=("", "V", "D", "I", "W", "E", "F"), width=5).pack(side=tk.LEFT, padx=6)
        ttk.Button(filters, text="读取当前日志", command=self.dump_logcat).pack(side=tk.LEFT, padx=8)
        ttk.Button(filters, text="清空设备日志", command=self.clear_logcat).pack(side=tk.LEFT)

        save_bar = ttk.Frame(tab)
        save_bar.pack(fill=tk.X, pady=8)
        self.log_output_path_var = tk.StringVar(value=str(Path.cwd() / "logs" / "logcat.txt"))
        ttk.Label(save_bar, text="保存路径").pack(side=tk.LEFT)
        log_entry = ttk.Entry(save_bar, textvariable=self.log_output_path_var)
        log_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        self._register_file_drop(log_entry, self.log_output_path_var)
        ttk.Button(save_bar, text="保存日志", command=self.save_logcat).pack(side=tk.LEFT)

        process_frame = ttk.LabelFrame(tab, text="运行进程")
        process_frame.pack(fill=tk.X, pady=(0, 8))
        process_toolbar = ttk.Frame(process_frame)
        process_toolbar.pack(fill=tk.X, padx=8, pady=(6, 4))
        ttk.Label(process_toolbar, text="点击包名可切换日志过滤包名").pack(side=tk.LEFT)
        ttk.Button(process_toolbar, text="刷新进程", command=self.refresh_processes).pack(side=tk.RIGHT)

        process_columns = ("pid", "user", "package", "name")
        self.process_table = ttk.Treeview(process_frame, columns=process_columns, show="headings", height=6)
        process_headings = {"pid": "PID", "user": "用户", "package": "包名", "name": "进程名"}
        process_widths = {"pid": 80, "user": 110, "package": 300, "name": 430}
        for column in process_columns:
            self.process_table.heading(column, text=process_headings[column])
            self.process_table.column(column, width=process_widths[column], anchor=tk.W)
        self.process_table.pack(fill=tk.X, padx=8, pady=(0, 8))
        self.process_table.bind("<<TreeviewSelect>>", self._on_process_selected)

        self.log_output = tk.Text(tab, height=14, wrap=tk.NONE)
        self._style_text(self.log_output)
        self.log_output.pack(fill=tk.BOTH, expand=True)

    def _build_output(self, parent: ttk.Frame) -> None:
        footer = ttk.Frame(parent, padding=(14, 10), style="Surface.TFrame")
        footer.pack(fill=tk.BOTH, pady=(12, 0))

        header = ttk.Frame(footer, style="Surface.TFrame")
        header.pack(fill=tk.X)
        ttk.Label(header, text="输出", style="Header.TLabel").pack(side=tk.LEFT)
        ttk.Label(header, textvariable=self.status_var, style="Status.TLabel").pack(side=tk.RIGHT)

        self.output = tk.Text(footer, height=6, wrap=tk.WORD)
        self._style_text(self.output)
        self.output.pack(fill=tk.BOTH, expand=False, pady=(4, 0))

    def _browse_adb(self) -> None:
        path = filedialog.askopenfilename(title="选择 adb.exe", filetypes=[("ADB", "adb.exe"), ("所有文件", "*.*")])
        if path:
            self.adb_path_var.set(path)

    def _browse_apk(self) -> None:
        path = filedialog.askopenfilename(title="选择 APK", filetypes=[("APK", "*.apk"), ("所有文件", "*.*")])
        if path:
            self.apk_path_var.set(path)

    def _browse_local_file(self) -> None:
        path = filedialog.askopenfilename(title="选择本机文件")
        if path:
            self.local_path_var.set(path)

    def _style_text(self, widget: tk.Text) -> None:
        widget.configure(
            background="#0f172a",
            foreground="#e5e7eb",
            insertbackground="#e5e7eb",
            selectbackground=COLORS["accent"],
            selectforeground="#ffffff",
            relief=tk.FLAT,
            borderwidth=0,
            padx=10,
            pady=8,
            font=("Consolas", 10),
        )

    def _style_listbox(self, widget: tk.Listbox) -> None:
        widget.configure(
            background="#ffffff",
            foreground=COLORS["text"],
            selectbackground=COLORS["accent_soft"],
            selectforeground=COLORS["text"],
            activestyle="none",
            relief=tk.FLAT,
            borderwidth=1,
            highlightthickness=1,
            highlightbackground=COLORS["border"],
            highlightcolor=COLORS["accent"],
        )

    def enter_floating_mode(self) -> None:
        self._show_floating_window()
        self.withdraw()

    def restore_main_window(self) -> None:
        self.deiconify()
        self.lift()
        self.focus_force()
        if self.floating_window is not None:
            self.floating_window.withdraw()

    def _show_floating_window(self) -> None:
        if self.floating_window is None or not self.floating_window.winfo_exists():
            self.floating_window = tk.Toplevel(self)
            self.floating_window.title("AdbPilot 设备")
            self.floating_window.geometry("360x240+80+80")
            self.floating_window.minsize(320, 180)
            self.floating_window.configure(bg=COLORS["app"])
            self.floating_window.attributes("-topmost", True)
            self.floating_window.protocol("WM_DELETE_WINDOW", self.restore_main_window)

            root = ttk.Frame(self.floating_window, padding=12, style="Surface.TFrame")
            root.pack(fill=tk.BOTH, expand=True)

            header = ttk.Frame(root, style="Surface.TFrame")
            header.pack(fill=tk.X)
            ttk.Label(header, text="设备监听", style="Header.TLabel").pack(side=tk.LEFT)
            ttk.Label(header, textvariable=self.floating_status_var, style="Status.TLabel").pack(side=tk.RIGHT)

            self.floating_devices_frame = ttk.Frame(root, style="Surface.TFrame")
            self.floating_devices_frame.pack(fill=tk.BOTH, expand=True, pady=(8, 8))

            actions = ttk.Frame(root, style="Surface.TFrame")
            actions.pack(fill=tk.X)
            ttk.Button(actions, text="打开主界面", command=self.restore_main_window, style="Primary.TButton").pack(side=tk.LEFT)
            ttk.Button(actions, text="刷新", command=self.refresh_devices, style="Quiet.TButton").pack(side=tk.RIGHT)
        else:
            self.floating_window.deiconify()

        self.floating_window.lift()
        self._render_floating_devices()

    def _render_floating_devices(self) -> None:
        if self.floating_window is None or not self.floating_window.winfo_exists():
            return
        if self.floating_devices_frame is None:
            return

        for child in self.floating_devices_frame.winfo_children():
            child.destroy()

        connected = [
            (serial, row)
            for serial, row in self.device_rows.items()
            if row.get("state") != "已断开"
        ]
        disconnected = [
            (serial, row)
            for serial, row in self.device_rows.items()
            if row.get("state") == "已断开"
        ]
        locked_serial = self.locked_serial_var.get().strip()
        if locked_serial:
            locked_row = self.device_rows.get(locked_serial)
            connected = []
            disconnected = []
            if locked_row is None:
                disconnected = [(locked_serial, {"state": "已断开", "details": {}})]
            elif locked_row.get("state") == "已断开":
                disconnected = [(locked_serial, locked_row)]
            else:
                connected = [(locked_serial, locked_row)]

        if connected:
            self.floating_status_var.set(f"已锁定 1 台" if locked_serial else f"已连接 {len(connected)} 台")
            for serial, row in sorted(connected):
                self._add_floating_device_row(serial, row, warning=False)
        else:
            self.floating_status_var.set("锁定设备已断开" if locked_serial else "无在线设备")
            ttk.Label(
                self.floating_devices_frame,
                text="警示：锁定设备已断开" if locked_serial else "未检测到已连接设备",
                foreground="#b91c1c",
            ).pack(anchor=tk.W, pady=4)

        for serial, row in sorted(disconnected):
            self._add_floating_device_row(serial, row, warning=True)

    def _add_floating_device_row(self, serial: str, row: dict[str, Any], *, warning: bool) -> None:
        assert self.floating_devices_frame is not None
        details = dict(row.get("details", {}))
        name = device_display_name(serial, details)
        connection_type = device_connection_type(serial)
        state = str(row.get("state", ""))

        wrapper = ttk.Frame(self.floating_devices_frame, style="Surface.TFrame")
        wrapper.pack(fill=tk.X, pady=3)

        title = f"{name}  ·  {connection_type}"
        if warning:
            title = f"警示：设备已断开  ·  {name}  ·  {connection_type}"
        ttk.Label(
            wrapper,
            text=title,
            foreground="#b91c1c" if warning else "#111827",
        ).pack(anchor=tk.W)
        ttk.Label(
            wrapper,
            text=f"{serial}  ·  {state}",
            foreground="#6b7280",
        ).pack(anchor=tk.W)

    def _close_app(self) -> None:
        if self.floating_window is not None and self.floating_window.winfo_exists():
            self.floating_window.destroy()
        self.destroy()

    def _register_file_drop(self, widget: tk.Widget, variable: tk.StringVar) -> None:
        if DND_FILES is None or not hasattr(widget, "drop_target_register"):
            return
        widget.drop_target_register(DND_FILES)
        widget.dnd_bind("<<Drop>>", lambda event: self._handle_file_drop(event, variable))

    def _handle_file_drop(self, event: tk.Event[Any], variable: tk.StringVar) -> None:
        paths = self.tk.splitlist(str(event.data))
        if not paths:
            return
        path = str(paths[0]).removeprefix("file:///")
        variable.set(path)
        self._append_output(f"已拖入文件：{path}\n")

    def _get_client(self) -> AdbClient:
        timeout = self._int_value(self.timeout_var, default=30)
        adb_path = self.adb_path_var.get().strip() or None
        if self.client is None or str(self.client.adb_path) != str(adb_path or self.client.adb_path) or self.client.timeout != timeout:
            self.client = AdbClient(adb_path=adb_path, timeout=timeout)
            self.adb_path_var.set(str(self.client.adb_path))
        return self.client

    def _selected_serial(self) -> str | None:
        locked = self.locked_serial_var.get().strip()
        if locked:
            return locked
        return self.selected_serial_var.get().strip() or None

    def lock_current_device(self) -> None:
        selection = self.device_table.selection()
        serial = self.selected_serial_var.get().strip()
        if selection:
            values = self.device_table.item(selection[0], "values")
            if values:
                serial = str(values[0]).strip()
        if not serial:
            messagebox.showwarning("AdbPilot", "请先选择一个设备再锁定。")
            return
        self.locked_serial_var.set(serial)
        self.selected_serial_var.set(serial)
        self._update_lock_status()
        self._append_output(f"已锁定设备：{serial}\n")
        self._render_floating_devices()

    def unlock_device(self) -> None:
        locked = self.locked_serial_var.get().strip()
        if locked:
            self._append_output(f"已解除设备锁定：{locked}\n")
        self.locked_serial_var.set("")
        self._update_lock_status()
        self._render_floating_devices()

    def _update_lock_status(self) -> None:
        locked = self.locked_serial_var.get().strip()
        if not locked:
            self.lock_status_var.set("未锁定")
            return
        row = self.device_rows.get(locked, {})
        state = row.get("state", "未知")
        self.lock_status_var.set(f"已锁定：{locked}（{state}）")

    def _run_async(self, label: str, func: Callable[[], Any], on_success: ResultCallback | None = None) -> None:
        self.status_var.set(f"执行中：{label}")
        self._append_output(f"$ {label}\n")

        def worker() -> None:
            try:
                result = func()
                self.result_queue.put(("ok", label, result, on_success))
            except Exception as exc:  # noqa: BLE001 - GUI must show unexpected worker failures.
                self.result_queue.put(("err", label, exc, None))

        threading.Thread(target=worker, daemon=True).start()

    def _process_results(self) -> None:
        while True:
            try:
                kind, label, payload, callback = self.result_queue.get_nowait()
            except queue.Empty:
                break

            if kind == "ok":
                if label != "设备监听":
                    self.status_var.set(f"完成：{label}")
                if callback:
                    callback(payload)
                elif payload is not None:
                    self._append_output(f"{payload}\n")
            else:
                if callback:
                    callback(payload)
                if label != "设备监听":
                    self.status_var.set(f"失败：{label}")
                    self._append_output(f"错误：{payload}\n")
                if label != "设备监听" and isinstance(payload, AdbPilotError):
                    messagebox.showerror("AdbPilot", str(payload))

        self.after(100, self._process_results)

    def _append_output(self, text: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        self.output.insert(tk.END, f"[{timestamp}] {text}")
        self.output.see(tk.END)

    def _on_device_selected(self, event: tk.Event[Any]) -> None:
        selection = self.device_table.selection()
        if selection:
            values = self.device_table.item(selection[0], "values")
            if values:
                serial = str(values[0])
                state = str(values[1])
                if state == "已断开":
                    if self.locked_serial_var.get().strip() != serial:
                        self.selected_serial_var.set("")
                    return
                self.selected_serial_var.set(serial)

    def _on_package_selected(self, event: tk.Event[Any]) -> None:
        selection = self.package_list.curselection()
        if selection:
            self._set_package_context(self.package_list.get(selection[0]))

    def _on_process_selected(self, event: tk.Event[Any]) -> None:
        selection = self.process_table.selection()
        if not selection:
            return
        values = self.process_table.item(selection[0], "values")
        if not values:
            return
        package = str(values[2] or values[3]).strip()
        if package:
            self._set_package_context(package)

    def show_version(self) -> None:
        self._run_async("检测 ADB 版本", lambda: self._get_client().version())

    def restart_server(self) -> None:
        self._run_async("重启 ADB 服务", lambda: self._get_client().restart_server() or "adb server 已重启")

    def refresh_devices(self) -> None:
        self.manual_refresh_in_progress = True

        def done(devices: list[Device]) -> None:
            self.manual_refresh_in_progress = False
            self._render_devices(devices, mark_disconnected=True, noisy=True)

        self._run_async("刷新设备", lambda: self._get_client().devices(), done)

    def _poll_devices(self) -> None:
        if self.device_poll_running:
            self.after(self.DEVICE_POLL_INTERVAL_MS, self._poll_devices)
            return

        self.device_poll_running = True

        def worker() -> None:
            try:
                devices = self._get_client().devices()
                self.result_queue.put(("ok", "设备监听", devices, self._render_polled_devices))
            except Exception as exc:  # noqa: BLE001 - listener should surface failures but keep retrying.
                self.result_queue.put(("err", "设备监听", exc, self._finish_device_poll))

        threading.Thread(target=worker, daemon=True).start()
        self.after(self.DEVICE_POLL_INTERVAL_MS, self._poll_devices)

    def _finish_device_poll(self, payload: Any = None) -> None:
        self.device_poll_running = False

    def _render_polled_devices(self, devices: list[Device]) -> None:
        self.device_poll_running = False
        self._render_devices(devices, mark_disconnected=True, noisy=False)

    def _render_devices(self, devices: list[Device], *, mark_disconnected: bool = True, noisy: bool = True) -> None:
        previous_connected = {
            serial
            for serial, row in self.device_rows.items()
            if row.get("state") != "已断开"
        }
        self.devices = devices
        current_serials = {device.serial for device in devices}
        for item in self.device_table.get_children():
            self.device_table.delete(item)
        next_rows: dict[str, dict[str, Any]] = {}

        for device in devices:
            details = device.details
            next_rows[device.serial] = {
                "state": device.state,
                "details": dict(details),
            }
            self.device_table.insert(
                "",
                tk.END,
                values=(
                    device.serial,
                    device.state,
                    details.get("product", ""),
                    details.get("model", ""),
                    details.get("device", ""),
                    details.get("transport_id", ""),
                ),
            )

        if mark_disconnected:
            for serial, row in self.device_rows.items():
                if serial in current_serials:
                    continue
                details = dict(row.get("details", {}))
                next_rows[serial] = {"state": "已断开", "details": details}
                self.device_table.insert(
                    "",
                    tk.END,
                    values=(
                        serial,
                        "已断开",
                        details.get("product", ""),
                        details.get("model", ""),
                        details.get("device", ""),
                        details.get("transport_id", ""),
                    ),
                    tags=("disconnected",),
                )

        self.device_table.tag_configure("disconnected", foreground="#9ca3af")
        self.device_rows = next_rows

        selected = self.selected_serial_var.get().strip()
        locked = self.locked_serial_var.get().strip()
        if locked:
            self.selected_serial_var.set(locked)
        elif selected and selected not in current_serials:
            self.selected_serial_var.set("")
            self.log_package_var.set("")
            self._append_output(f"设备已断开：{selected}\n")
        elif not selected and devices:
            ready = next((device for device in devices if device.is_ready), devices[0])
            self.selected_serial_var.set(ready.serial)

        disconnected = sorted(previous_connected - current_serials)
        connected = sorted(current_serials - previous_connected)
        for serial in connected:
            if previous_connected:
                self._append_output(f"设备已连接：{serial}\n")
        for serial in disconnected:
            self._append_output(f"设备已断开：{serial}\n")

        if noisy or connected or disconnected:
            self._append_output(f"检测到 {len(devices)} 台在线设备\n")
        self._update_lock_status()
        selected_for_logs = self.selected_serial_var.get().strip()
        if selected_for_logs in current_serials and not self.log_package_var.get().strip():
            self.use_foreground_package(default_only=True)
        self._render_floating_devices()

    def show_device_info(self) -> None:
        serial = self._selected_serial()
        self._run_async("读取设备详情", lambda: self._get_client().device_info(serial), self._display_mapping)

    def connect_device(self) -> None:
        address = self.connect_addr_var.get().strip()
        if not address:
            messagebox.showwarning("AdbPilot", "请输入 host:port 地址")
            return
        self._run_async(f"连接 {address}", lambda: self._get_client().connect(address))

    def disconnect_device(self) -> None:
        address = self.connect_addr_var.get().strip() or None
        self._run_async("断开无线设备", lambda: self._get_client().disconnect(address))

    def use_foreground_package(self, default_only: bool = False) -> None:
        if default_only and self.log_package_var.get().strip():
            return

        def apply(package: str) -> None:
            if default_only and self.log_package_var.get().strip():
                return
            self._set_package_context(package)
            self._append_output(f"当前前台应用：{package}\n")

        self._run_async(
            "获取前台应用",
            lambda: self._get_client().foreground_package(self._selected_serial()),
            apply,
        )

    def refresh_processes(self) -> None:
        self._run_async(
            "刷新运行进程",
            lambda: self._get_client().running_processes(self._selected_serial()),
            self._render_processes,
        )

    def _render_processes(self, processes: list[RunningProcess]) -> None:
        for item in self.process_table.get_children():
            self.process_table.delete(item)
        sorted_processes = sorted(processes, key=lambda process: (not bool(process.package), process.name))
        for process in sorted_processes:
            self.process_table.insert(
                "",
                tk.END,
                values=(process.pid, process.user, process.package, process.name),
            )
        self._append_output(f"列出 {len(processes)} 个运行进程\n")

    def install_apk(self) -> None:
        apk = self.apk_path_var.get().strip()
        if not apk:
            messagebox.showwarning("AdbPilot", "请选择 APK 文件")
            return
        self._run_async(
            "安装 APK",
            lambda: self._get_client().install(
                apk,
                self._selected_serial(),
                replace=self.install_replace_var.get(),
                downgrade=self.install_downgrade_var.get(),
                grant=self.install_grant_var.get(),
            ),
        )

    def uninstall_app(self) -> None:
        package = self._required_package()
        if not package:
            return
        if not messagebox.askyesno("AdbPilot", f"确认卸载 {package}？"):
            return
        self._run_async(f"卸载 {package}", lambda: self._get_client().uninstall(package, self._selected_serial()))

    def start_app(self) -> None:
        package = self._required_package()
        if package:
            self._run_async(f"启动 {package}", lambda: self._get_client().start_app(package, self._selected_serial()))

    def stop_app(self) -> None:
        package = self._required_package()
        if package:
            self._run_async(f"停止 {package}", lambda: self._get_client().stop_app(package, self._selected_serial()) or "已停止")

    def clear_app(self) -> None:
        package = self._required_package()
        if not package:
            return
        if not messagebox.askyesno("AdbPilot", f"确认清除 {package} 的应用数据？"):
            return
        self._run_async(f"清除 {package}", lambda: self._get_client().clear_app(package, self._selected_serial()))

    def list_apps(self) -> None:
        self._run_async(
            "列出应用",
            lambda: self._get_client().list_packages(
                self._selected_serial(),
                third_party=self.third_party_only_var.get(),
                filter_text=self.package_filter_var.get().strip() or None,
            ),
            self._render_packages,
        )

    def _render_packages(self, packages: list[str]) -> None:
        self.package_list.delete(0, tk.END)
        for package in packages:
            self.package_list.insert(tk.END, package)
        self._append_output(f"列出 {len(packages)} 个应用\n")

    def app_info(self) -> None:
        package = self._required_package()
        if package:
            self._run_async(f"应用详情 {package}", lambda: self._get_client().app_info(package, self._selected_serial()), self._display_mapping)

    def export_apk(self) -> None:
        package = self._required_package()
        if not package:
            return
        output_dir = filedialog.askdirectory(title="选择 APK 导出目录")
        if output_dir:
            self._run_async(
                f"导出 {package}",
                lambda: "\n".join(str(path) for path in self._get_client().export_apk(package, Path(output_dir), self._selected_serial())),
            )

    def push_file(self) -> None:
        local = self.local_path_var.get().strip()
        remote = self.remote_path_var.get().strip()
        if not local or not remote:
            messagebox.showwarning("AdbPilot", "请填写本机路径和设备路径")
            return
        self._run_async("上传文件", lambda: self._get_client().push(local, remote, self._selected_serial()))

    def pull_file(self) -> None:
        remote = self.remote_path_var.get().strip()
        local = self.local_path_var.get().strip()
        if not local or not remote:
            messagebox.showwarning("AdbPilot", "请填写本机路径和设备路径")
            return
        self._run_async("下载文件", lambda: self._get_client().pull(remote, local, self._selected_serial()))

    def screencap(self) -> None:
        output = Path(self.screencap_path_var.get().strip())
        self._run_async("截屏", lambda: self._get_client().screencap(output, self._selected_serial()))

    def bugreport(self) -> None:
        output = Path(self.bugreport_path_var.get().strip())
        self._run_async("导出 bugreport", lambda: self._get_client().bugreport(output, self._selected_serial()))

    def screenrecord(self) -> None:
        output = Path(self.record_path_var.get().strip())
        seconds = self._int_value(self.record_seconds_var, default=10)
        self._run_async("录屏", lambda: self._get_client().screenrecord(output, self._selected_serial(), seconds=seconds))

    def tap(self) -> None:
        x = self._int_value(self.tap_x_var)
        y = self._int_value(self.tap_y_var)
        self._run_async("点击", lambda: self._get_client().input_tap(x, y, self._selected_serial()) or "已点击")

    def swipe(self) -> None:
        values = [self._int_value(var) for var in self.swipe_vars]
        self._run_async(
            "滑动",
            lambda: self._get_client().input_swipe(
                values[0],
                values[1],
                values[2],
                values[3],
                self._selected_serial(),
                duration_ms=values[4],
            )
            or "已滑动",
        )

    def input_text(self) -> None:
        text = self.input_text_var.get()
        self._run_async("输入文本", lambda: self._get_client().input_text(text, self._selected_serial()) or "已输入")

    def keyevent(self, key: str) -> None:
        self._run_async(f"按键 {key}", lambda: self._get_client().input_keyevent(key, self._selected_serial()) or "已发送")

    def run_shell(self) -> None:
        command = self.shell_command_var.get().strip()
        if not command:
            return
        self._run_async("执行 shell", lambda: self._get_client().shell(command.split(), self._selected_serial()), self._render_shell_output)

    def _render_shell_output(self, text: str) -> None:
        self.shell_output.delete("1.0", tk.END)
        self.shell_output.insert(tk.END, text)
        self._append_output("Shell 命令已完成\n")

    def dump_logcat(self) -> None:
        self._run_async("读取 logcat", self._logcat_dump, self._render_log_output)

    def save_logcat(self) -> None:
        output = Path(self.log_output_path_var.get().strip())

        def save() -> str:
            text = self._logcat_dump()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(text, encoding="utf-8", errors="replace")
            return f"日志已保存：{output}"

        self._run_async("保存 logcat", save)

    def clear_logcat(self) -> None:
        def clear() -> str:
            client = self._get_client()
            serial = client.require_serial(self._selected_serial())
            return client.run(["logcat", "-c"], serial=serial).stdout or "已清空"

        self._run_async("清空 logcat", clear)

    def _logcat_dump(self) -> str:
        client = self._get_client()
        serial = client.require_serial(self._selected_serial())
        args = ["logcat", "-d"]
        package = self.log_package_var.get().strip()
        tag = self.log_tag_var.get().strip()
        level = self.log_level_var.get().strip()

        if package:
            pid = client.run(["shell", "pidof", package], serial=serial, check=False).stdout.strip().split()
            if pid:
                args.extend(["--pid", pid[0]])
        if tag and level:
            args.extend([f"{tag}:{level}", "*:S"])
        elif tag:
            args.extend([f"{tag}:V", "*:S"])
        elif level:
            args.append(f"*:{level}")
        return client.run(args, serial=serial, timeout=120).stdout

    def _render_log_output(self, text: str) -> None:
        self.log_output.delete("1.0", tk.END)
        self.log_output.insert(tk.END, text)
        self._append_output(f"读取日志 {len(text)} 字符\n")

    def _display_mapping(self, mapping: Any) -> None:
        if hasattr(mapping, "__dict__"):
            mapping = mapping.__dict__
        lines = [f"{key}: {value}" for key, value in dict(mapping).items()]
        self._append_output("\n".join(lines) + "\n")

    def _set_package_context(self, package: str) -> None:
        self.package_var.set(package)
        self.log_package_var.set(package)
        self._append_output(f"已切换包名：{package}\n")

    def _required_package(self) -> str | None:
        package = self.package_var.get().strip()
        if not package:
            messagebox.showwarning("AdbPilot", "请输入或选择包名")
            return None
        return package

    @staticmethod
    def _int_value(var: tk.StringVar, default: int | None = None) -> int:
        value = var.get().strip()
        if not value and default is not None:
            return default
        try:
            return int(value)
        except ValueError:
            if default is not None:
                return default
            raise ValueError(f"请输入整数：{value}") from None


def main() -> int:
    try:
        app = AdbPilotGui()
        app.mainloop()
        return 0
    except subprocess.TimeoutExpired as exc:
        messagebox.showerror("AdbPilot", f"命令超时：{exc}")
        return 2


def device_connection_type(serial: str) -> str:
    if ":" in serial:
        return "无线"
    return "USB"


def device_display_name(serial: str, details: dict[str, str]) -> str:
    model = details.get("model") or details.get("product") or details.get("device")
    if model:
        return model.replace("_", " ")
    return serial


if __name__ == "__main__":
    raise SystemExit(main())
