"""Desktop controls for the shared lag recording workflow."""

from __future__ import annotations

import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, ttk
from typing import TYPE_CHECKING, Any

from . import lag, lag_incident, lag_report
from .client import AdbClient
from .models import Device

if TYPE_CHECKING:
    from .gui import AdbPilotGui


class LagDiagnosticsTab(ttk.Frame):
    def __init__(self, tabs: ttk.Notebook, app: AdbPilotGui) -> None:
        super().__init__(tabs, padding=16, style="Surface.TFrame")
        self.app = app
        self.control: lag.RecordingControl | None = None
        self.incident: lag_incident.Incident | None = None
        self.recovering = False
        self.mark_count = 0
        self.status = tk.StringVar(self, value=f"在设备中心选择在线设备。每次采集 {lag.DEFAULT_DURATION} 秒。")
        self.warning = tk.StringVar(self)
        self.mark_status = tk.StringVar(self, value="卡顿标记 0 次")
        self.report_path = tk.StringVar(self)
        self.snapshot_path = tk.StringVar(self)
        self.recovery_path = tk.StringVar(self)
        self.usb_choice = tk.StringVar(self)
        self.wifi_choice = tk.StringVar(self)
        self.scene = tk.StringVar(self)
        self.usb_options: dict[str, Device] = {}
        self.wifi_options: dict[str, Device] = {}

        self.columnconfigure(1, weight=1)
        actions = ttk.Frame(self)
        actions.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        self.start_button = ttk.Button(actions, text="开始采集", command=self.start_recording, style="Primary.TButton")
        self.mark_button = ttk.Button(actions, text="标记卡顿", command=self.mark, state=tk.DISABLED)
        self.stop_button = ttk.Button(actions, text="停止采集", command=self.stop_recording, state=tk.DISABLED)
        for button in (self.start_button, self.mark_button, self.stop_button):
            button.pack(side=tk.LEFT, padx=(0, 8))
        ttk.Label(actions, textvariable=self.mark_status, style="Muted.TLabel").pack(side=tk.LEFT, padx=(4, 0))
        ttk.Label(self, textvariable=self.status, wraplength=620).grid(row=1, column=0, columnspan=2, sticky="w")
        ttk.Label(self, textvariable=self.warning, wraplength=620, style="Warning.TLabel").grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(2, 12),
        )

        for row, name, path in ((3, "采集报告", self.report_path), (4, "超时快照", self.snapshot_path),
                                (5, "USB 补查报告", self.recovery_path)):
            ttk.Label(self, text=name).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=4)
            ttk.Entry(self, textvariable=path, state="readonly").grid(row=row, column=1, sticky="ew", pady=4)

        dual = ttk.Labelframe(self, text="USB + Wi-Fi 联合采集", padding=10)
        dual.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        dual.columnconfigure(1, weight=1)
        ttk.Label(dual, text="USB").grid(row=0, column=0, sticky="w", padx=(0, 10), pady=4)
        self.usb_menu = ttk.Combobox(dual, textvariable=self.usb_choice, state="readonly", postcommand=self.refresh_transports)
        self.usb_menu.grid(row=0, column=1, sticky="ew", pady=4)
        ttk.Label(dual, text="Wi-Fi").grid(row=1, column=0, sticky="w", padx=(0, 10), pady=4)
        self.wifi_menu = ttk.Combobox(dual, textvariable=self.wifi_choice, state="readonly", postcommand=self.refresh_transports)
        self.wifi_menu.grid(row=1, column=1, sticky="ew", pady=4)
        ttk.Label(dual, text="场景备注（可选）").grid(row=2, column=0, sticky="w", padx=(0, 10), pady=4)
        ttk.Entry(dual, textvariable=self.scene).grid(row=2, column=1, sticky="ew", pady=4)
        dual_actions = ttk.Frame(dual)
        dual_actions.grid(row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self.dual_button = ttk.Button(dual_actions, text="开始联合采集", command=self.start_incident, style="Primary.TButton")
        self.dual_button.pack(side=tk.LEFT, padx=(0, 8))
        self.recovery_button = ttk.Button(dual_actions, text="恢复后通过 USB 补查", command=self.recover_incident, state=tk.DISABLED)
        self.recovery_button.pack(side=tk.LEFT, padx=(0, 8))
        ttk.Label(dual_actions, text="最长 5 分钟", style="Muted.TLabel").pack(side=tk.LEFT)

    def refresh_transports(self) -> None:
        usb, wifi = {}, {}
        for device in self.app.device_transports:
            if not device.is_ready:
                continue
            kind = lag._transport_type(device)
            label = f"{device.details.get('model') or '设备'} · {device.serial}"
            if kind == "usb":
                usb[label] = device
            elif kind in {"wifi", "tcpip"}:
                wifi[label] = device
        self.usb_options, self.wifi_options = usb, wifi
        for menu, choice, options in ((self.usb_menu, self.usb_choice, usb), (self.wifi_menu, self.wifi_choice, wifi)):
            menu.configure(values=list(options))
            if choice.get() not in options:
                choice.set(next(iter(options)) if len(options) == 1 else "")

    def _begin(self, message: str) -> lag.RecordingControl:
        control = lag.RecordingControl()
        self.control = control
        self.incident = None
        self.mark_count = 0
        self.mark_status.set("卡顿标记 0 次")
        self.warning.set("")
        self.report_path.set("")
        self.snapshot_path.set("")
        self.recovery_path.set("")
        self.status.set(message)
        self.start_button.state(["disabled"])
        self.dual_button.state(["disabled"])
        self.recovery_button.state(["disabled"])
        self.stop_button.state(["!disabled"])
        return control

    def start_recording(self) -> None:
        if self.control is not None or self.recovering:
            return
        serial = self.app._selected_serial()
        device = next((device for device in self.app.device_transports if device.serial == serial and device.is_ready), None)
        if device is None:
            self.status.set("请先在设备中心选择在线设备。")
            return
        identifier = device.details.get("transport_id")
        if not identifier:
            self.status.set("当前 ADB 未提供传输 ID。请更新 Android Platform-Tools。")
            return

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
        destination = filedialog.asksaveasfilename(
            parent=self, title="保存卡顿诊断报告", defaultextension=".json",
            initialfile=f"adbpilot-lag-{stamp}.json", filetypes=[("JSON 报告", "*.json")],
        )
        if not destination:
            return

        output = Path(destination).expanduser().absolute()
        control = self._begin(f"正在确认设备 {serial}…")

        def publish(event: str, detail: str) -> None:
            self.app.result_queue.put(("progress", "卡顿诊断", (event, detail), show_event))

        def show_event(payload: tuple[str, str]) -> None:
            event, detail = payload
            if event == "started" and not control.cancelled.is_set():
                self.status.set(f"正在采集 {serial}（{lag.DEFAULT_DURATION} 秒）。出现卡顿时点击「标记卡顿」。")
                self.mark_button.state(["!disabled"])
            elif event == "unresponsive":
                self.warning.set(f"{lag_report.UNRESPONSIVE_MESSAGE} 后续每 {lag.BACKOFF_INTERVAL} 秒最多探测一次。")
            elif event == "checkpoint_saved":
                self.snapshot_path.set(detail)
            elif event == "checkpoint_failed":
                self.warning.set(f"{self.warning.get()} 快照保存失败。{detail}")

        def done(report: dict[str, Any]) -> None:
            self.report_path.set(str(output))
            self.mark_status.set(f"卡顿标记 {len(report['marks'])} 次")
            reason = report["stop_reason"]
            if reason:
                self.status.set(f"{lag.STOP_MESSAGES[reason]} 已保存现有样本。")
            else:
                self.status.set("采集结束，报告已保存。")
            self._reset_controls()

        def failed(error: Exception | None) -> None:
            self.status.set(f"诊断失败。{error}" if error is not None else "诊断准备已取消。")
            self._reset_controls()

        def ready(client: AdbClient) -> None:
            if control.cancelled.is_set():
                failed(None)
                return
            self.app._run_async(
                "卡顿诊断", lambda: lag.record_to_path(
                    client, output, serial=device.serial, expected_transport_id=identifier,
                    control=control, on_event=publish,
                ), done, failed, daemon=False,
            )

        self.app._prepare_client_async(ready, failed, label="准备卡顿诊断")

    def start_incident(self) -> None:
        if self.control is not None or self.recovering:
            return
        self.refresh_transports()
        usb = self.usb_options.get(self.usb_choice.get())
        wifi = self.wifi_options.get(self.wifi_choice.get())
        if usb is None or wifi is None:
            self.status.set("请为联合采集分别选择 USB 和 Wi-Fi 连接。")
            return
        destination = filedialog.askdirectory(parent=self, title="选择联合诊断报告目录", mustexist=True)
        if not destination:
            return
        directory = Path(destination).expanduser().absolute()
        label = self.scene.get().strip() or None
        control = self._begin("正在核对两条连接是否属于同一台手机…")

        def show_event(payload: tuple[str, str]) -> None:
            event, detail = payload
            if event == "started" and not control.cancelled.is_set():
                self.status.set("正在轮流探测 USB 和 Wi-Fi。出现卡顿时点击「标记卡顿」。")
                self.mark_button.state(["!disabled"])
            elif event == "timeline_saved":
                self.report_path.set(detail)
            elif event == "checkpoint_saved":
                self.snapshot_path.set(detail)
            elif event == "unresponsive":
                scope = {"both": "USB 和 Wi-Fi", "usb": "USB", "wifi": "Wi-Fi"}[detail]
                self.warning.set(f"{scope} 探针超时。已保存现场，后续每 {lag.BACKOFF_INTERVAL} 秒最多探测一次。")

        def publish(event: str, detail: str) -> None:
            self.app.result_queue.put(("progress", "联合卡顿诊断", (event, detail), show_event))

        def done(incident: lag_incident.Incident) -> None:
            self.incident = incident
            self.status.set("联合采集已保存。手机恢复后，选择它的 USB 连接并点击补查。")
            self._reset_controls()

        def failed(error: Exception | None) -> None:
            self.status.set(f"联合诊断未完成。{error}" if error else "联合诊断准备已取消。")
            self._reset_controls()

        def ready(client: AdbClient) -> None:
            if control.cancelled.is_set():
                failed(None)
                return
            self.app._run_async(
                "联合卡顿诊断", lambda: lag_incident.record_incident(
                    client, directory, usb=usb, wifi=wifi, label=label, control=control, on_event=publish,
                ), done, failed, daemon=False,
            )

        self.app._prepare_client_async(ready, failed, label="准备联合卡顿诊断")

    def recover_incident(self) -> None:
        incident = self.incident
        if self.control is not None or self.recovering or incident is None:
            return
        self.refresh_transports()
        usb = self.usb_options.get(self.usb_choice.get())
        if usb is None:
            self.status.set("请选择手机恢复后的在线 USB 连接。")
            return
        self.recovering = True
        self.start_button.state(["disabled"])
        self.dual_button.state(["disabled"])
        self.recovery_button.state(["disabled"])
        self.status.set("正在核对手机身份并读取重启和异常记录…")

        def done(result: tuple[Path, dict[str, Any]]) -> None:
            output, report = result
            self.recovery_path.set(str(output))
            cycle = {"new_boot": "确认出现新的启动周期", "same_boot": "启动周期未变化", "unknown": "启动周期无法确认"}
            reasons = report["boot_reasons"]
            reason = reasons["sys.boot.reason"] or reasons["ro.boot.bootreason"] or "不可用"
            records = (f"匹配系统条目 {len(report['dropbox_entries'])} 条" if report["checks"]["dropbox"] == "ok"
                       else "系统异常记录不可用")
            self.status.set(f"USB 补查已保存。{cycle[report['boot_cycle']]}，启动原因 {reason}，{records}。")
            self.recovering = False
            self._reset_controls()

        def failed(error: Exception | None) -> None:
            self.status.set(f"USB 补查未完成。{error}" if error else "USB 补查已取消。")
            self.recovering = False
            self._reset_controls()

        self.app._prepare_client_async(
            lambda client: self.app._run_async(
                "USB 补查", lambda: lag_incident.collect_recovery(client, incident, usb), done, failed, daemon=False,
            ), failed, label="准备 USB 补查",
        )

    def mark(self) -> None:
        if self.control is not None and self.control.mark():
            self.mark_count += 1
            self.mark_status.set(f"卡顿标记 {self.mark_count} 次")

    def stop_recording(self) -> None:
        if self.control is not None:
            self.control.cancelled.set()
            self.mark_button.state(["disabled"])
            self.stop_button.state(["disabled"])
            self.status.set("正在停止并保存已采集的数据…")

    def _reset_controls(self) -> None:
        self.control = None
        self.start_button.state(["!disabled"])
        self.dual_button.state(["!disabled"])
        self.recovery_button.state(["!disabled" if self.incident is not None else "disabled"])
        self.mark_button.state(["disabled"])
        self.stop_button.state(["disabled"])
