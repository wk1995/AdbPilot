# 无线调试卡顿诊断技术方案

2026-11-11 · 设计稿 · [PRD](prd.md) · [可行性分析](feasibility.md)

## 技术选型

| 上游项目 | 许可证 | 采用方式 |
| --- | --- | --- |
| Android Dev Monitor | MIT | 参考会话打点、无线延迟检查和会话对比。AdbPilot 使用现有 Python CLI 实现采集和输出。 [功能说明](https://github.com/marko69420/android-dev-monitor#whats-new-in-v250) · [许可证](https://github.com/marko69420/android-dev-monitor/blob/main/LICENSE) |
| DroidGauge | MIT | 参考单调时钟采样、独立保存与比较。上游 `gfxinfo` 用累计总帧与累计卡顿帧算比例。AdbPilot 需要标注采样窗口，首版使用 `framestats` 的近期帧数据。 [采集循环](https://github.com/RuizheGu0/droidgauge/blob/a484a15f4390/src/droidgauge/capture.py#L18-L69) · [解析器](https://github.com/RuizheGu0/droidgauge/blob/a484a15f4390/src/droidgauge/parsers.py#L23-L26) · [许可证](https://github.com/RuizheGu0/droidgauge/blob/a484a15f4390/LICENSE) |
| Perfetto | Apache-2.0 | 使用 Android 自带的 System Tracing 录制，Perfetto UI 打开本地 trace。第二阶段可直接调用设备端 `perfetto`，按需使用本机 Trace Processor。 [Android 录制方式](https://perfetto.dev/docs/learning-more/android) · [Python API](https://perfetto.dev/docs/analysis/trace-processor-python) · [许可证](https://github.com/google/perfetto/blob/main/LICENSE) |
| scrcpy | Apache-2.0 | 作为独立的镜像负载复现。记录启用与否及视频设置，避免混入空闲基线。 [视频设置](https://github.com/Genymobile/scrcpy/blob/master/doc/video.md) · [许可证](https://github.com/Genymobile/scrcpy/blob/master/LICENSE) |

首版的运行依赖是项目已有的 Python 和 Android Platform-Tools。上述项目作为流程和实现参考，Perfetto UI 由用户按需打开。若后续分发第三方代码或二进制，实施时核对实际分发版本及对应的许可文件。

## 代码入口与会话（R1、R2、R5）

- 在 [`cli.py`](../../adbpilot/cli.py#L42) 增加 `lag record` 和 `lag compare`，由 `lag.py` 管理会话、归纳数据和离线比较。`compare` 在初始化 `AdbClient` 前分发，支持没有 ADB 的电脑。
- 复用 [`AdbClient.run`](../../adbpilot/client.py#L47) 和 [`parse_devices`](../../adbpilot/client.py#L1199)。为 `run` 和 `_build_command` 增加可选 `transport_id`，与 `serial` 互斥。通过 `adb devices -l` 的原始结果选目标。多条在线传输时用户传入精确的 `--serial`，仅一条时可省略。ADB 支持 `-t ID` 指向 server 内的传输。 [ADB 手册](https://android.googlesource.com/platform/packages/modules/adb/+/refs/heads/main/docs/user/adb.1.md)
- 传输类型优先读取 `usb` 等 ADB 元数据，识别无线 mDNS 标识和 IP 端点。模拟器及无法确认物理介质的远程 ADB 连接保留明确的 `emulator` 或 `unknown` 标记。首版真机流程只承诺验证 USB 和无线 ADB。
- 会话内每次设备端调用前，用一次主机侧设备列表检查已选 `serial`、`transport_id` 和 `device` 状态。出现明确的掉线、授权失效或 ID 变化即终止，已有样本写入部分报告。缺少 ID 的 ADB 返回兼容提示。连接列表与发命令之间存在竞态，遇到共享 ADB server 重启或设备重新授权，应废弃受影响的测量轮次。诊断只发只读命令，不主动配对、断开、重启或恢复连接。
- 默认 30 秒，可配置 10–120 秒。每 3 秒安排一个探针，`adb -t <ID> shell true` 单次超时 2 秒，主机侧列表检查也受独立的短超时和剩余会话预算约束。采样串行，错过的时刻跳过，避免慢链路上追赶补采。连续超时可持续记录到窗口结束，确认目标丢失立即停止。
- 探针用 `time.monotonic_ns()` 计时，从启动 ADB 子进程前到命令返回。计时包含进程启动、主机 ADB server、传输和设备 shell 执行。预检与设备列表查询的耗时放在探针计时之外。记录计划点、实际发起时间、耗时和状态。只从成功样本计算中位数和最大值，超时、失败、跳过单独计数。少于 5 个成功样本时显示样本不足，不输出差值。
- `--mark-on-enter` 只在交互终端启用。输入线程只入队时间点，采集线程仍是唯一的设备端命令调用者。提示和进度发到 stderr，结构化 stdout 保留给 JSON。Ctrl+C 停止调度，尽量写出部分报告，退出码 `130`。正常输出 `0`，参数、目标、I/O 错误或目标中断返回 `2`，并在能够写入时保留已取得的数据。

```bash
adbpilot lag record --serial 192.168.1.10:37001 --label wifi-idle --package com.example.app --mark-on-enter --out wifi-idle.json
adbpilot lag record --serial 192.168.1.10:37001 --label wifi-logcat --package com.example.app --out wifi-logcat.json
adbpilot lag compare wifi-idle.json wifi-logcat.json --same-device
```

## 手机端帧证据（R3）

`--package` 可选。校验包名后，仅在采样窗前后各执行一次 `dumpsys gfxinfo <包名> framestats`，每次限时 4 秒。把结束帧查询放在探针窗口之外。首版不清空系统帧统计。 [Android dumpsys](https://developer.android.com/tools/dumpsys#ui)

- 根据 `PROFILEDATA` 边界和列名解析各窗口，校验 `Flags`、`IntendedVsync`、`FrameCompleted` 与时间戳顺序。跳过标记异常或不完整的行。用同一窗口的时间戳去掉基线已有帧，取 `FrameCompleted - IntendedVsync` 作为 HWUI 近期帧完成耗时，统计帧数、中位数、最大值和最早至最晚样本跨度。显示的跨度仅描述已取到的样本。 [AOSP FrameInfo](https://android.googlesource.com/platform/frameworks/base/+/d559e782533e1b9071c9945c1b7c056872c4ff29/libs/hwui/FrameInfo.h)
- 近期缓冲区可能只剩会话尾部数据。窗口重建、基线丢失、数据无交集、输出截断或帧时钟异常时，将该窗口标记为部分覆盖或不可用。可展示可信的近期样本，禁用全会话掉帧率和跨口径差值。首版不使用固定 16.7 毫秒阈值，也不将 HWUI 完成耗时当作显示上屏时间。SurfaceView 等渲染路径的覆盖需单独检查。 [Android 渲染指标](https://developer.android.com/topic/performance/vitals/render) · [Perfetto FrameTimeline](https://perfetto.dev/docs/data-sources/frametimeline)
- 手机本地 trace 可覆盖无线调试关闭和开启未连接的基线。相同场景优先用相同数据源和录制长度，结束后再导出。Android 12+ 在支持的设备和图层上看 FrameTimeline，并结合 `sched` 和 `freq` 查看调度与 CPU 频率。Android 11 以可用系统轨道和应用 HWUI 数据为主。CLI 打点使用主机相对时间，trace 使用设备时钟。首版以场景和人工复现时刻比对，不直接叠加两边时间轴。 [真机 System Tracing](https://developer.android.com/topic/performance/tracing/on-device) · [Perfetto FrameTimeline](https://perfetto.dev/docs/data-sources/frametimeline)

## 本地报告与比较（R4、R5）

| 字段 | 内容 |
| --- | --- |
| `schema_version`, `session_id`, `status` | 格式版本、随机会话 ID、`complete` / `partial` / `failed`，附枚举终止原因 |
| `context` | 采集开始时间（UTC）、用户场景标签、主机平台、ADB 版本、Android SDK、传输类型、可选包名、计划和实际时长 |
| `adb` | 相对开始时间、命令耗时和结果类别的样本序列，成功与失败计数、中位数和最大值 |
| `frames` | 来源、状态、时间跨度、窗口与帧数、指标及不可用原因。设备时钟的原始绝对值和窗口标题不写入摘要 |
| `marks` | 用户打点的主机相对时间 |

JSON 采用 UTF-8。通过同目录临时文件写入，再原子发布，发布操作应拒绝覆盖已存在的目标。写入失败在 stderr 说明。保存前只从明确定义的结构字段生成输出。原始 ADB 命令、错误文本、序列号、地址、日志和凭据不进入文件。包名和场景标签属于用户主动提供的信息。

比较前校验 JSON 版本、数值和字段。只有用户通过 `--same-device` 确认同一台设备、两侧度量口径一致、采样完整且达到最小样本量，才计算 ADB 耗时差值。时长、采样间隔、主机平台、ADB 版本或 Android SDK 不匹配时，相关数据只并排展示并标注原因。传输类型与负载可作为有意改变的变量，报告显示两侧条件。HWUI 近期帧数据只并排展示，突出包名和帧覆盖差异。

## 第二阶段 Perfetto 接入

1. 在真机验证首版后，增加独立的 trace 命令。验证设备端 Perfetto 和所需数据源可用。通过 stdin 提交固定的 `TraceConfig`，让未 root 的 Android 11 也能尝试相同录制路径。拟采用默认 20 秒、上限 60 秒的设备端时限。按场景限制缓冲和文件大小，启用所需帧、调度和频率数据源。 [Android 配置与 SELinux](https://perfetto.dev/docs/learning-more/android) · [TraceConfig](https://perfetto.dev/docs/concepts/config)
2. 设备侧以唯一文件名写到 Perfetto trace 目录，录制结束且目标仍匹配后再拉取。取消、超时或断线由设备端时限兜底。拉取失败保留文件位置供手动恢复，成功后只清理本次创建的文件。原始 trace 可能含进程名及系统事件，单独保存，由用户决定是否分享。
3. 先由用户手动在 Perfetto UI 打开本地文件。需要自动汇总时，评估使用用户显式提供的本机 Trace Processor。用 FrameTimeline 对应的 expected / actual 数据及 `stats` 检查覆盖和丢包，识别解析版本与数据源缺失。此路径按需运行，无本机分析器时仍可导出。 [Perfetto UI](https://perfetto.dev/docs/visualization/perfetto-ui) · [数据丢失诊断](https://perfetto.dev/docs/concepts/buffers) · [Trace Processor API](https://perfetto.dev/docs/analysis/trace-processor-python)

## 实施和验证

| 顺序 | 验证点 | 通过条件 |
| --- | --- | --- |
| 1 | CLI 路由、目标选择、采样和报告 | 模拟多传输、离线、权限失败、超时、取消和 server 变化。无样本的统计是空值，时钟跳变不改变耗时，少样本和断线结果可读。验证 `compare` 可无 ADB 执行，报告不含原始设备标识。 |
| 2 | HWUI 解析 | 用 Android 11、12+ 和至少一个厂商真机样本覆盖正常帧、无帧、多窗口、缓冲覆盖和字段变化。跟手机本地 trace 人工核对覆盖，报告不把近期帧样本扩展成会话统计。 |
| 3 | 无线对照和采集开销 | Windows、macOS 真机运行，覆盖单无线、USB、双传输和中途断线。各相关场景至少 3 轮，先有无探针对照，再单独加 logcat、文件传输、投屏等负载。记录原始测量与环境，检查诊断本身是否引入可重复的额外卡顿。 |
| 4 | Perfetto 第二阶段 | 先在目标手机验证录制权限、设备端时限、拉取恢复及缺失数据源，再启用本地摘要和 GUI 入口。 |

参数均为待验证的初始值。实现前优先在实际出现卡顿的机型做 [A–E 组对照](feasibility.md#真机对照)，判断优先投入 ADB 延迟采集还是手机端 trace。若仅 AdbPilot GUI 组复现，再验证设备监听及多传输去重的查询次数，基于结果另行优化。
