"""Desktop controls for the shared lag recording workflow."""

from __future__ import annotations

import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, ttk
from typing import TYPE_CHECKING, Any

from . import lag, lag_report
from .client import AdbClient

if TYPE_CHECKING:
    from .gui import AdbPilotGui


class LagDiagnosticsTab(ttk.Frame):
    def __init__(self, tabs: ttk.Notebook, app: AdbPilotGui) -> None:
        super().__init__(tabs, padding=16, style="Surface.TFrame")
        self.app = app
        self.control: lag.RecordingControl | None = None
        self.mark_count = 0
        self.status = tk.StringVar(self, value=f"在设备中心选择在线设备。每次采集 {lag.DEFAULT_DURATION} 秒。")
        self.warning = tk.StringVar(self)
        self.mark_status = tk.StringVar(self, value="卡顿标记 0 次")
        self.report_path = tk.StringVar(self)
        self.snapshot_path = tk.StringVar(self)

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

        for row, name, path in ((3, "最终报告", self.report_path), (4, "超时快照", self.snapshot_path)):
            ttk.Label(self, text=name).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=4)
            ttk.Entry(self, textvariable=path, state="readonly").grid(row=row, column=1, sticky="ew", pady=4)

    def start_recording(self) -> None:
        if self.control is not None:
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
        control = lag.RecordingControl()
        self.control = control
        self.mark_count = 0
        self.mark_status.set("卡顿标记 0 次")
        self.warning.set("")
        self.report_path.set("")
        self.snapshot_path.set("")
        self.status.set(f"正在确认设备 {serial}…")
        self.start_button.state(["disabled"])
        self.stop_button.state(["!disabled"])

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
        self.mark_button.state(["disabled"])
        self.stop_button.state(["disabled"])
