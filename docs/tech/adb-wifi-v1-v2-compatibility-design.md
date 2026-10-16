# AdbPilot ADB Wi-Fi 1.0 / 2.0 兼容技术方案

- 状态：Draft
- 日期：2026-08-20
- 修订：2026-10-16，按当前 ADB 用户文档校正能力检测；同日按当前代码把配对、扫码和连接从「待实现」改为「已有，需补齐」
- 目标分支：`codex/feat-adb-wifi-v1-v2-compat`
- 关联 PRD：[AdbPilot ADB Wi-Fi 1.0 / 2.0 兼容 PRD](../product/adb-wifi-v1-v2-compatibility-prd.md)

## 1. 方案摘要

本方案在现有 `PlatformAdapter → AdbClient → GUI/CLI/JSON CLI` 分层上增加无线能力探测、安全配对和配对后连接编排。AdbPilot 不实现 ADB Wi-Fi 协议栈，而是复用用户选择的 Android SDK Platform-Tools：

- 经典 TCP/IP 继续调用 `adb connect` / `adb disconnect`。
- ADB Wi-Fi 1.0 调用不带配对码参数的 `adb pair`。手机从 Android 11 起，电视和 Wear OS 从 Android 13 起；Android 11 Wear 除外。配对后发现 `_adb-tls-connect` 即自动连接，这不是 2.0 才有的行为。
- ADB Wi-Fi 2.0 同时取决于主机 `adb server-status` 和设备 `mdns_service_version`。AdbPilot 只在两者都满足时标记 2.0，用户流程仍是配对码。
- 配对码仅以 AdbPilot 内存字符串存在，通过子进程标准输入发送，不出现在 AdbPilot 发起的命令参数、结果对象或日志中。adb 发给本机 Server 的内部指令不在此保证范围内。

## 2. 设计依据

Android 官方资料给出的关键约束如下：

1. 手机 Android 11、电视和 Wear OS Android 13 起支持这套无线调试。Android 11 Wear 不适用。Android 10 及更低版本仍走 USB 初始化后的 `adb tcpip 5555`。
2. AOSP 架构说明：主机发现 `_adb-tls-connect` 后，只要该主机此前已配对，TLS 认证成功即自动连接。因此自动连接属于 Wi-Fi 1.0。
3. Android 17 与 Platform-Tools 37.0.0 引入 ADB Wi-Fi 2.0：更换 mDNS 实现，并在设备连上已勾选「始终允许」的可信网络时自动连接。设备是否支持，看 `adb mdns track-services --proto-text` 的 `mdns_service_version` 是否不低于 `2.0`，不看 `ro.build.version.sdk`。
4. `adb pair HOST[:PORT] [PAIRING CODE]` 两者都合法。省略配对码时，adb 打印 `Enter pairing code:` 并从标准输入读取；传入参数时直接使用。AdbPilot 只使用前一种。
5. 现行排障不使用 `adb mdns check`。该命令只报告本机 mDNS 组件名称和版本。主机状态读 `adb server-status` 的 `version`、`mdns_enabled`、`mdns_backend`；网络与设备读 `adb mdns track-services --proto-text`。
6. 配对端口与连接端口是不同的临时端口，不能默认复用。无线设备序列号通常是 `adb-<序列号后缀>._adb-tls-connect._tcp`。
7. 30.0.0 只是无线配对的初步支持。34.0.5 才把「配对失败仍返回成功退出码」改为失败退出码 1。37.0.0 默认后端改为 libadbmdns，openscreen 仍作备用；37.0.1 删除 openscreen，`ADB_MDNS_OPENSCREEN` 失效。30.0.1 起 mDNS 自动连接默认关闭，可由 `ADB_MDNS_AUTO_CONNECT` 重新打开。

参考：

- [ADB 用户文档：无线调试与排障](https://developer.android.com/tools/adb)
- [Android SDK Platform-Tools 发布说明](https://developer.android.com/tools/releases/platform-tools)
- [Android API 级别](https://developer.android.com/about/versions/api-levels)
- [ADB Wi-Fi 2.0 官方介绍](https://developer.android.com/blog/posts/introducing-fast-and-reliable-wireless-debugging-with-android-debug-bridge-adb-wi-fi-2-0)
- [AOSP：ADB Wi-Fi 架构](https://android.googlesource.com/platform/packages/modules/adb/+/HEAD/docs/dev/adb_wifi.md)
- [AOSP：ADB 命令手册](https://android.googlesource.com/platform/packages/modules/adb/+/refs/heads/main/docs/user/adb.1.md)

## 3. 现状与问题

### 3.1 当前实现

- `adbpilot/client.py` 已有 `connect()`、`disconnect()`、`pair(address, pairing_code)`、`pair_by_qr()`、`mdns_services()`、`connect_paired_by_mdns()`、`devices()` 和 `version()`。
- `pair()` 目前执行 `adb pair <地址> <配对码>`，配对码在命令参数中。
- `pair_by_qr()` 生成临时服务名和配对信息，等待 `_adb-tls-pairing` 或直接出现的 `_adb-tls-connect`，再连接新设备。
- `connect_paired_by_mdns()` 等待 `_adb-tls-connect`；找不到时提示改填无线调试主页上的连接地址。
- `adbpilot/gui.py` 的「无线调试」已有配对地址、配对码、扫码配对、连接已配对、连接地址、连接和断开。操作走后台线程。
- `adbpilot/cli.py` 与 `adbpilot/ai_cli.py` 已暴露 `connect` / `disconnect`，尚未暴露能力检查和配对。
- 测试以 Python `unittest` 为主。

### 3.2 当前缺口

- 没有 `wifi_capabilities()`，不读取 `adb server-status`，也不读取 `mdns_service_version`。界面不能区分主机条件和设备代际。
- 配对地址和连接地址已经分栏，但没有在调用 adb 前拦截空地址、非法端口和控制字符。
- 六位配对码和扫码临时配对信息都会进入 adb 命令参数，不符合本方案的脱敏约束。
- 错误仍主要是通用 `AdbCommandError` 或一段提示文本，没有本方案的错误类别。
- GUI、CLI、JSON CLI 没有共用的配对结果对象。CLI 和 JSON CLI 还不能做能力检查和配对。

## 4. 目标与非目标

### 4.1 技术目标

- 在不新增第三方运行时依赖的前提下实现能力检测、配对、连接和结果验证。
- 所有无线业务逻辑只实现一次，由 GUI、CLI 和 JSON CLI 复用。
- 对旧 adb、Wi-Fi 1.0、Wi-Fi 2.0 和经典 TCP/IP 安全降级。1.0 的自动连接必须保留，不能降级成「只有 2.0 才会自动出现」。
- 建立配对码不落盘、不入参数、不入日志的可测试保证。
- 所有 GUI 操作异步、有界、可恢复，不阻塞 Tk 主线程。

### 4.2 技术非目标

- 不自行实现 TLS 配对、mDNS responder/browser 或 Wi-Fi 2.0 协议。
- 不在 MVP 使用 `host:track-mdns-services` 等内部/低层 ADB Server 服务。
- 不重写现有扫码配对，不解析 Android Studio 私有状态，不操作 ADB 私钥文件。现有二维码流程只补齐脱敏、取消和失败分类。
- 不实现 ADB Server mDNS 自动重启；该能力进入后续独立设计。
- 不自动执行设备侧 `adb tcpip`。

## 5. 总体架构

```mermaid
flowchart TD
    GUI[AdbPilot GUI] --> SVC[AdbClient]
    CLI[adbpilot CLI] --> SVC
    AI[adbpilot-ai JSON CLI] --> SVC

    SVC --> VALIDATE[输入校验与脱敏]
    SVC --> CAP[无线能力检测]
    SVC --> ORCH[配对/连接编排]

    CAP --> ADB[选定的 adb 二进制]
    ORCH --> ADB
    ADB --> SERVER[ADB Server]
    SERVER --> LEGACY[经典 TCP/IP 设备]
    SERVER --> V1[ADB Wi-Fi 1.0 设备]
    SERVER --> V2[ADB Wi-Fi 2.0 设备]

    SVC --> RESULT[结构化结果/受控错误]
    RESULT --> GUI
    RESULT --> CLI
    RESULT --> AI
```

核心原则：界面层不解析 adb 文本；`AdbClient` 不判断产品展示形式；Platform-Tools 负责协议，AdbPilot 负责流程与安全边界。

## 6. 数据模型

在 `adbpilot/models.py` 增加不可变数据类型：

```python
@dataclass(frozen=True)
class AdbWifiCapabilities:
    adb_path: str
    adb_version: str
    platform_tools_revision: str = ""
    supports_connect: bool = True
    supports_pair: bool = False
    mdns_enabled: str = "unknown"
    mdns_backend: str = "unknown"
    network_mdns_status: str = "unknown"
    wifi2_host_compatible: bool = False
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class WirelessPairingResult:
    status: str
    paired: bool
    connected: bool
    pairing_address: str
    connection_address: str = ""
    device_serial: str = ""
    next_action: str = ""
    capabilities: AdbWifiCapabilities | None = None
```

`status` 使用受控值：

| 状态 | 含义 |
| --- | --- |
| `paired_and_connected` | 配对成功且设备已上线 |
| `paired_waiting_for_connect` | 配对成功，但需要用户提供连接地址 |
| `paired_connected_explicitly` | 配对成功，并通过显式连接地址上线 |
| `unsupported` | 当前 adb 不支持安全配对 |
| `ambiguous` | 旧 adb 返回结果无法可靠判断，需要用户复核或升级 |

失败类结果原则上使用异常，而不是构造 `paired=False` 的成功返回，确保 CLI 退出码和 JSON `ok` 一致。

## 7. 能力检测设计

### 7.1 新增接口

```python
class AdbClient:
    def wifi_capabilities(self) -> AdbWifiCapabilities:
        ...
```

### 7.2 检测步骤

1. 调用 `adb version`，保存原始版本文本并解析 Platform-Tools revision，例如 `37.0.1`。禁止用 `1.0.41` 代替该 revision。
2. 调用 `adb server-status`，读取 `version`、`mdns_enabled`、`mdns_backend`。`adb help` 里有 `pair` 只说明命令存在，不能证明配对稳定。
3. `supports_pair=True` 仅当 revision 不低于 34.0.5。30.0.0–34.0.4 记入 `warnings`，产品内不执行配对。
4. `wifi2_host_compatible=True` 必须同时满足：revision 不低于 37.0.0、`supports_pair=True`、`mdns_enabled=true`。37.0.0 还要求 `mdns_backend=LIBADBMDNS`；37.0.1 起 openscreen 已删除，不再检查该环境变量。任一条件不满足都保留手输地址，并给出对应警告。
5. 网络是否能发现设备不由 `adb mdns check` 判断。该命令只返回本机 mDNS 组件信息。需要设备在场时，有界读取 `adb mdns track-services --proto-text`：出现带地址和端口的 `_adb-tls-connect` 才把 `network_mdns_status` 标为 `visible`；空输出标为 `not_visible`。无设备或用户未开启无线调试时保持 `unknown`，不得报成网络故障。
6. `supports_connect` 只要 adb 基础调用可用即为真。30.0.1–34.x 若自动连接没有发生，警告中说明默认关闭，可由 `ADB_MDNS_AUTO_CONNECT` 打开；AdbPilot 不改用户环境变量。

### 7.3 设备侧代际展示

配对前只知道主机能力。设备代际以官方排障命令为准，不读取 `ro.build.version.sdk` 代替：

1. 设备已开启无线调试。
2. 有界执行 `adb mdns track-services --proto-text`。
3. 同一条 TLS 服务里 `mdns_service_version` 不低于 `2.0`，才标记「设备支持 Wi-Fi 2.0」。
4. 有 TLS 服务但没有该字段，标记「安全无线调试（Wi-Fi 1.0）」。
5. 没有 TLS 服务时不猜测代际。手机最低 Android 11、电视和 Wear OS 最低 Android 13，只用于操作说明；Android 11 Wear 提示不适用。

`build_version_sdk_full` 可以随服务元数据展示，但不能覆盖 `mdns_service_version`。厂商系统版本达到 17 而服务字段缺失时，仍按 1.0 处理。

### 7.4 版本解析

新增纯函数：

```python
def parse_platform_tools_revision(output: str) -> tuple[int, int, int] | None:
    ...
```

兼容以下形式：

```text
Android Debug Bridge version 1.0.41
Version 37.0.1-xxxxxxxx
Installed as /path/to/adb
```

禁止用 `1.0.41` 判断 Platform-Tools 版本；它是 ADB 协议/程序版本标识，不等同于 `37.0.1` revision。

## 8. 输入与子进程安全

### 8.1 地址校验

新增：

```python
def normalize_adb_address(value: str, *, require_port: bool = True) -> str:
    ...
```

规则：

- 去除首尾空白。
- 拒绝空字符串、NUL、换行和其他 ASCII 控制字符。
- 接受 `IPv4:port`、`hostname:port` 和标准 `[IPv6]:port`。`track-services` 会同时给出 IPv4、IPv6 和 `.local` 主机名，展示时保留原值。MVP 的通过标准仍只覆盖 IPv4 真机。
- 端口必须为 1–65535 的十进制整数。
- 配对地址必须显式包含端口；经典 `connect` 仍可在兼容层允许 adb 默认端口，但 GUI 首期要求显式端口以减少歧义。

### 8.2 配对码校验

```python
def normalize_pairing_code(value: str) -> str:
    ...
```

- 只接受 6 位 ASCII 数字。
- 不写入配置、结果、命令记录或日志。
- 进入 `AdbClient` 后只在配对调用作用域内存在，调用结束即释放引用；Python 无法保证物理内存立即清零，文档不作该承诺。

### 8.3 安全标准输入调用

扩展 `AdbClient.run()`，或增加私有 `_run_with_input()`：

```python
def _run_with_input(
    self,
    args: Iterable[str],
    *,
    input_text: str,
    timeout: int,
    check: bool = True,
    redact: Iterable[str] = (),
) -> CommandResult:
    ...
```

配对实际执行形式：

```python
result = self._run_with_input(
    ["pair", pairing_address],
    input_text=f"{pairing_code}\n",
    timeout=60,
    check=False,
    redact=(pairing_code,),
)
```

关键约束：

- `pairing_code` 不得加入 `args`。官方帮助允许 `adb pair host code`，产品明确不使用这种形式。
- `subprocess.run(..., input=input_text, shell=False)`。
- 创建 `CommandResult` 或异常前，对 AdbPilot 捕获的 stdout/stderr 做精确 secret 替换；替换后的文本才能进入错误格式化。
- AdbPilot 调试日志只记录 `[adb, pair, <redacted-address>]`、退出码和受控状态，不记录标准输入。
- 不设置 `ADB_TRACE`，不打开 `adb server-status` 的 `log_absolute_path`。adb 发给本机 Server 的 `host:pair:<code>:<address>` 内部指令不由本产品擦除。
- 单元测试必须读取 mock 调用参数，证明 code 不在命令数组、AdbPilot 异常字符串和 AdbPilot 日志中。

## 9. 配对与连接编排

### 9.1 接口

```python
class AdbClient:
    def pair(
        self,
        pairing_address: str,
        pairing_code: str,
        *,
        connect_address: str | None = None,
        wait_for_device_seconds: float = 10.0,
    ) -> WirelessPairingResult:
        ...
```

### 9.2 状态机

```mermaid
stateDiagram-v2
    [*] --> Validating
    Validating --> Unsupported: adb 不支持 pair
    Validating --> Pairing: 参数与能力有效
    Pairing --> Failed: 明确失败/超时
    Pairing --> Ambiguous: 旧 adb 无可靠成功证据
    Pairing --> Waiting: 明确配对成功
    Waiting --> Online: 设备自动上线
    Waiting --> Connecting: 未上线且提供连接地址
    Waiting --> NeedAddress: 未上线且无连接地址
    Connecting --> Online: connect 成功且设备可见
    Connecting --> Failed: connect 失败/超时
    Online --> [*]
    NeedAddress --> [*]
    Ambiguous --> [*]
```

### 9.3 详细流程

1. 校验配对地址、配对码和可选连接地址。
2. 获取能力；不支持 `pair` 时抛出 `AdbWifiCapabilityError`，附升级建议。
3. 通过 stdin 执行 `adb pair <pairing_address>`，超时 60 秒。
4. 结果判断：
   - revision 低于 34.0.5：不进入本流程。
   - 非零退出码：失败。34.0.5 起，官方把原先失败仍返回成功的情况改为退出码 1。
   - 输出包含明确成功语义：配对成功。
   - 零退出码但无明确成功语义：返回 `ambiguous`，不声称成功。
5. 配对成功后，每 1 秒执行一次 `adb devices -l`，最多 10 秒。只把新增的 `._adb-tls-connect._tcp` 设备当作自动上线证据。已有 USB 设备、经典 `ip:port` 设备都不能算。Wi-Fi 1.0 就会自动连接；30.0.1 至 34.x 默认关闭该行为，10 秒内没有新 TLS 设备时走连接地址回退，不报成 2.0 缺失。
6. 若发现新增在线设备，返回 `paired_and_connected`。
7. 若没有新增设备但提供了连接地址，执行现有 `connect()`；再次刷新设备列表并返回 `paired_connected_explicitly` 或错误。
8. 若没有连接地址，返回 `paired_waiting_for_connect`，`next_action` 指引用户填写手机无线调试主页的连接地址。

### 9.4 旧 platform-tools 兼容

Platform-Tools 34.0.5 修复了无线配对失败仍返回成功退出码的问题。因此：

- 37.0.1+：推荐版本。openscreen 已删除，按退出码、成功语义和 `mdns_enabled` 判断。
- 37.0.0：可配对。只有 `mdns_backend=LIBADBMDNS` 且 `mdns_enabled=true` 才标记 Wi-Fi 2.0 主机条件；否则提示 `ADB_MDNS_OPENSCREEN=0` 或 `ADB_MDNS=1`，由用户确认后自行重启 adb。
- 34.0.5–36.x：可使用安全配对，不标记 Wi-Fi 2.0 主机条件。自动连接可能受 `ADB_MDNS_AUTO_CONNECT` 默认关闭影响。
- 30.0.0–34.0.4：不提供产品内配对。30.0.0 是初步支持，34.0.5 之前失败退出码不可靠。
- <30.0.0：不支持安全配对，只保留经典 `connect`。

不为旧版本维护自定义 TLS 实现，也不自动下载或替换用户 adb。

## 10. 错误模型

在 `adbpilot/errors.py` 增加：

```python
class WirelessAddressError(AdbPilotError): ...
class PairingCodeError(AdbPilotError): ...
class AdbWifiCapabilityError(AdbPilotError): ...
class AdbPairingError(AdbCommandError): ...
class AdbConnectionError(AdbCommandError): ...
```

`AdbPairingError` 保存退出码和已脱敏 stdout/stderr。错误归一化建议：

| 错误类别 | 识别证据 | 用户建议 |
| --- | --- | --- |
| `adb_missing` | 无法解析 adb 路径 | 安装或选择 Platform-Tools |
| `pair_unsupported` | revision <34.0.5，或无 `pair` | 升级 Platform-Tools；30.0.0–34.0.4 不在产品内配对；经典连接仍可用 |
| `mdns_disabled` | `server-status` 中 `mdns_enabled=false` | 提示 `ADB_MDNS=1` 后由用户重启 adb；仍可手输地址 |
| `mdns_backend_obsolete` | 37.0.0 且后端不是 `LIBADBMDNS` | 提示 `ADB_MDNS_OPENSCREEN=0` 后由用户重启 adb。37.0.1 不再给出该变量 |
| `network_mdns_not_visible` | 无线调试已开，但 `track-services` 为空 | 确认同一网络。企业 Wi-Fi 可能阻止连接；仍可手输连接地址 |
| `invalid_address` | 本地校验失败 | 使用手机当前页面显示的 `IP:端口` |
| `invalid_code` | 非 6 位数字 | 重新打开配对码页面并输入当前代码 |
| `pair_rejected` | adb 明确认证失败 | 代码可能错误或过期，生成新代码重试 |
| `pair_unreachable` | 超时/拒绝连接 | 保持配对页面开启，检查同网与端口变化 |
| `paired_not_connected` | 已配对但 10 秒无新增设备 | 输入无线调试主页的连接地址 |
| `connect_failed` | `adb connect` 失败 | 检查连接端口，不要使用配对端口 |

文本匹配只用于归类和提示，不应成为唯一成功证据；保留默认 `unknown` 分支。

## 11. GUI 设计

### 11.1 布局

保留现有侧栏「无线调试」区域。配对地址、配对码、扫码配对、连接已配对、连接地址、连接和断开都已经存在，本方案不重做这些入口：

- 顶部增加简短能力状态，例如“经典连接可用 · 安全配对可用 · 主机具备 Wi-Fi 2.0 条件”。最后一项必须同时满足版本、`mdns_enabled` 和后端要求，不能只因为版本不低于 37 就显示。
- 连接地址、连接、断开保留，标题改为“经典/已配对设备连接”，避免和配对地址混用。
- 现有配对栏继续承担六位码流程：配对地址必填，配对码掩码显示。配对成功但设备没有自动出现时，把焦点放到连接地址，不要求重新输入配对码。
- 现有扫码配对保留。电脑继续出示二维码，手机扫描；补齐临时配对信息脱敏、取消和失败分类，不改扫码方向。
- 提供“检查能力”入口，不在应用启动时强制执行额外 adb 命令。

### 11.2 交互状态

- 点击配对后立即禁用提交按钮、清空配对码输入框并进入执行中状态。
- 复用 `_run_async()` 和 `result_queue`；网络/adb 调用不得在主线程执行。
- 成功后关闭对话框、刷新设备列表、选择新增设备并显示结构化结果。
- `paired_waiting_for_connect` 时保留对话框，只聚焦连接地址字段，不要求用户重新输入配对码。
- 失败时恢复提交按钮；错误弹窗和输出区均使用脱敏后的用户文案。
- 窗口关闭不强杀已启动的 adb 子进程；首期允许后台调用在超时内结束，并丢弃已销毁窗口的回调。后续可加入取消令牌。

## 12. CLI 设计

### 12.1 普通 CLI

```text
adbpilot wifi-capabilities [--json]
adbpilot pair <pairing-address> [--connect <connection-address>]
```

- `pair` 使用 `getpass.getpass()` 获取配对码；即使终端不支持隐藏输入，也不把 code 放入 argv。
- 不提供 `--code` 或位置 code 参数，避免 shell history 与进程列表泄露。
- 成功打印简短结果与下一步；失败沿用退出码 2。
- `wifi-capabilities --json` 仅为人类 CLI 的可选机读输出；自动化优先使用 `adbpilot-ai`。

### 12.2 JSON CLI

新增 schema：

```json
{
  "wifi-capabilities": {
    "description": "Inspect adb wireless capabilities",
    "params": {}
  },
  "pair": {
    "description": "Pair a wireless adb device",
    "params": {
      "address": "host:port string",
      "pairing_code": "required sensitive 6-digit string",
      "connect_address": "optional host:port string"
    },
    "sensitive_params": ["pairing_code"]
  }
}
```

推荐调用：

```bash
printf '%s' '<request-json>' | adbpilot-ai run -
```

文档示例必须使用占位码，不得包含真实配对数据。响应使用现有 `{ok, operation, data/error}` 包装，`write_error()` 前再次执行敏感值检查，保证 code 不回显。

## 13. 并发、超时与一致性

- `AdbClient` 保持无共享可变配对状态；一次 `pair()` 的基线设备列表、code 和结果均为局部变量。
- GUI 增加 `pairing_in_progress`，阻止重复点击；CLI 进程天然串行。
- `adb pair` 与 `adb connect` 各 60 秒超时，设备上线轮询 10 秒；能力检查单项最多 10 秒。
- 如果 GUI 设备自动监听与配对轮询并发，允许两者都调用 `adb devices`，但配对完成后的 UI 更新必须通过主线程队列。
- 后续若引入自动 mDNS 恢复，需要再增加 ADB Server 级互斥，不能在配对期间执行 `kill-server`。

## 14. 可观测性与隐私

### 14.1 可记录

- adb 路径的脱敏展示、Platform-Tools revision、能力布尔值。
- 操作类型、耗时、退出码、受控错误类别。
- 是否配对、是否自动上线、是否使用显式连接回退。

### 14.2 禁止记录

- 六位配对码或二维码密码。
- 标准输入原文。
- 完整设备 GUID、完整序列号、真实 Wi-Fi SSID。
- Android Studio 二维码内容。

### 14.3 脱敏边界

- 异常构造前脱敏，而不是仅在 GUI 展示时脱敏。
- JSON CLI 不输出调试堆栈。
- 测试失败信息也不得直接打印 Fake code；测试使用固定占位值并断言其不出现。

## 15. 测试方案

### 15.1 单元测试

`tests/test_client.py`：

- 解析 30.x、34.0.5、36.x、37.0.0、37.0.1 及未知格式版本。
- 证明不会把 `1.0.41` 误判为 Platform-Tools revision。
- 能力检测覆盖：无 `pair`、revision 30.0.0–34.0.4、34.0.5–36.x、37.0.0 后端不是 `LIBADBMDNS`、37.0.0 且 `mdns_enabled=false`、37.0.1 且 mDNS 已开启。不得把 `adb mdns check` 的组件版本当成网络可用。
- 地址合法/非法、端口边界、控制字符与配对码格式。
- 配对码仅进入 `subprocess.run(input=...)`，不在 args、`CommandResult`、异常或格式化日志中。
- 明确成功、明确失败、旧 adb 零退出码但无成功语义、超时等分支。
- 配对前后只把新增的 `._adb-tls-connect._tcp` 设备当作自动上线，不把原有 USB 设备或经典 `ip:port` 设备算进去。
- 提供/未提供连接地址的两条回退路径。

`tests/test_ai_cli.py`：

- schema 包含 `wifi-capabilities`、`pair` 与 `sensitive_params`。
- `pair` 参数类型、必填字段与六位码校验。
- 成功和失败 JSON 都不回显 code。
- 既有 operation schema 与响应包装保持兼容。

`tests/test_gui_helpers.py`：

- 能力状态摘要文案。
- 配对结果到对话框状态的映射。
- 配对码提交后立即清空。
- 重复提交被阻止；后台完成后恢复按钮状态。

`tests/test_cli.py`（新增）：

- parser 暴露新命令。
- `getpass` 获取 code 且不会传入输出。
- `--connect` 可选参数映射正确。
- 能力与配对错误退出码为 2。

### 15.2 自动化回归

```bash
python -m unittest discover -s tests -v
```

- Python 3.9、3.12 至少各运行一次。
- Windows、macOS 运行完整测试；Linux 运行 CLI/客户端测试与打包前静态检查。
- `git diff --check` 必须通过。

### 15.3 真机验收矩阵

| 主机 | 设备 | adb | 验收重点 |
| --- | --- | --- | --- |
| Windows | 已开启经典 TCP/IP 的设备 | 最新稳定版 | 连接、断开、回归 |
| macOS | 已开启经典 TCP/IP 的设备 | 最新稳定版 | 连接、断开、回归 |
| Windows | 手机 Android 11–16 | 37.0.1+ | 1.0 配对、同一网络再次自动连接、手动连接回退 |
| macOS | 手机 Android 11–16 | 34.0.5–36.x | 稳定配对、自动连接默认关闭时的显式连接回退 |
| Windows | 电视或 Wear OS，Android 13–16 | 37.0.1+ | 最低系统门槛与 1.0 配对；Android 11 Wear 只验证提示不适用 |
| Windows / macOS | `mdns_service_version` 不低于 `2.0` | 37.0.1+，以及一台 `mdns_enabled=false` 的对照 | 2.0 字段识别、可信网络重连；mDNS 关闭时不得标记主机就绪 |

每个组合连续执行 20 次。必须额外覆盖错误码、过期码、错误端口、网络切换、配对后不自动上线和多个既有设备场景。

没有能返回 `mdns_service_version: "2.0"` 的设备时，MVP 可完成代码合入，但不得宣布 Wi-Fi 2.0 真机验收通过；发布说明必须标记该项为待验证。仅有 Android 17 系统版本、没有该字段，不算通过。

## 16. 发布与回滚

- 不变更现有命令语义，只新增命令与 GUI 入口，属于向后兼容变更。
- 不自动迁移配置；现有 `~/.adbpilot/gui.json` 保持可读。
- 新功能失败时，用户仍可使用现有 `connect` / `disconnect`。
- 若发布后出现配对安全或稳定性问题，可隐藏 GUI“安全配对”入口并停止暴露新增命令；经典连接无需回滚。
- 发布说明必须列出推荐 Platform-Tools 37.0.1+、产品内配对最低 34.0.5、配对地址/连接地址差异、1.0 已支持同一网络自动连接，以及尚未完成的真机矩阵项。

## 17. 实施顺序

1. 增加版本、地址、配对码与输出解析纯函数及单元测试。
2. 增加数据模型、错误类型和安全 stdin 子进程封装。
3. 实现 `wifi_capabilities()`、`pair()` 与配对后设备差异验证。
4. 接入普通 CLI 与 `tests/test_cli.py`。
5. 接入 JSON CLI schema/operation 与脱敏测试。
6. 在现有 GUI 的配对、扫码和连接上接入能力状态、异步结果和脱敏，不新增另一套配对入口。
7. 运行完整自动化测试与 Windows/macOS 真机矩阵。
8. 更新 README、版本说明和产品优化清单状态。

## 18. 方案取舍

| 方案 | 结论 | 原因 |
| --- | --- | --- |
| AdbPilot 自行实现 TLS/mDNS/Wi-Fi 2.0 | 不采用 | 重复 AOSP 协议、安全风险高、跨版本维护成本不可控 |
| 把配对码作为 `adb pair host code` 参数 | 不采用 | 会暴露在进程列表、shell history 或调试日志中 |
| 仅按 Android SDK 或主机 adb 版本判断 2.0 | 不采用 | 官方以 `mdns_service_version` 判断设备，以 `adb server-status` 判断主机；系统版本和 adb 版本都不能单独替代 |
| adb 版本过低时禁用全部无线功能 | 不采用 | 经典 `adb connect` 仍可能正常工作 |
| 重写现有扫码配对，或同时做自动 mDNS 发现 | 不采用 | 扫码已在 GUI 中可用；重写它或同时做持续发现会扩大范围。本方案只补齐脱敏、取消和失败分类 |

## 19. 后续技术优化登记

所有未覆盖项以 PRD 的 `Product Optimization Backlog` 为产品源清单。技术侧已知后续主题：

- 基于公开 `adb mdns services` 或 adb 37+ 跟踪能力的设备发现。
- mDNS 状态卡死的旁路证据、冷却与无在线设备保护。
- IPv6-only 网络真机验收。地址解析已接受 `[IPv6]:port`，`track-services` 的 IPv6 与 `.local` 主机名可展示，但首期不承诺仅 IPv6 网络。
- 扫码配对的取消、超时，以及临时服务名和配对信息不入日志。二维码生成本身已有，不另做一套。
- USB `adb tcpip` 的设备选择、端口冲突和明确授权交互。
- 配对取消、子进程终止和 GUI 生命周期一致性。
- 多 adb 二进制与已运行 ADB Server 版本不一致的诊断。

这些主题不在 MVP 隐式实现；进入开发前必须补充独立验收标准或技术设计。
