# AdbPilot ADB Wi-Fi 1.0 / 2.0 兼容 PRD

- 状态：Draft
- 日期：2026-08-20
- 目标版本：MVP（版本号由发布流程决定）
- 目标分支：`codex/feat-adb-wifi-v1-v2-compat`
- 需求来源：兼容截图所示 ADB Wi-Fi 1.0 设备提示，并支持 ADB Wi-Fi 2.0
- 关联技术方案：[ADB Wi-Fi 1.0 / 2.0 兼容技术方案](../tech/adb-wifi-v1-v2-compatibility-design.md)

## 术语与范围校正

为避免把不同代际的无线 ADB 混为一谈，本需求统一使用以下术语：

| 产品术语 | 定义 | 首期支持 |
| --- | --- | --- |
| 经典 TCP/IP | 设备已通过 `adb tcpip` 或厂商能力开启 TCP 监听后，使用 `adb connect host:port` 连接；常见默认端口为 5555 | 是 |
| ADB Wi-Fi 1.0 | Android 11–16 引入/使用的安全无线调试配对流程，使用 `adb pair`、TLS 与 mDNS | 是 |
| ADB Wi-Fi 2.0 | Android 17 与 adb 37.0.0 引入的新一代无线调试能力，重点改善可信网络上的发现与自动连接体验 | 是，通过 adb 37+ 透明兼容；AdbPilot 首期不自行实现底层协议 |

Android 官方说明 Android 11 及以上支持无线调试，Android 17 搭配 adb 37.0.0 引入 ADB Wi-Fi 2.0。经典 `adb connect` 是兼容入口，不在文档中冒充 ADB Wi-Fi 1.0。

参考资料：

- [Android：通过 Wi-Fi 连接设备](https://developer.android.com/studio/run/device#wireless)
- [AOSP：ADB Wi-Fi 架构](https://android.googlesource.com/platform/packages/modules/adb/+/HEAD/docs/dev/adb_wifi.md)
- [AOSP：ADB 命令手册](https://android.googlesource.com/platform/packages/modules/adb/+/refs/heads/main/docs/user/adb.1.md)

## 1. Executive Summary

### Problem Statement

AdbPilot 当前只暴露 `adb connect host:port`，无法在产品内完成 Android 11+ 的安全配对，也无法识别本机 adb 对 ADB Wi-Fi 1.0 / 2.0 的支持等级。用户遇到旧设备能力受限、配对端口与连接端口混淆、platform-tools 版本过低或 mDNS 不可用时，只能依靠外部工具和经验排查。

### Proposed Solution

在 `AdbClient` 增加无线能力检测、安全配对和配对后连接编排，统一供桌面 GUI、普通 CLI 和 `adbpilot-ai` JSON CLI 使用。产品始终委托本机 Android SDK Platform-Tools 实现底层协议：支持经典 TCP/IP；对 Android 11–16 使用安全配对流程；对 Android 17+ 在检测到 adb 37+ 时透明使用 ADB Wi-Fi 2.0 能力，并在能力不足时提供可执行的降级或升级建议。

### Success Criteria

- 在 Windows 与 macOS 验收矩阵中，经典 TCP/IP、ADB Wi-Fi 1.0 和 ADB Wi-Fi 2.0 每个组合连续执行 20 次，配对/连接成功率均不低于 95%；失败必须返回明确原因，不允许界面无响应。
- 对当前已经支持的 `connect`、`disconnect` 行为和既有自动化测试保持 100% 通过，不引入无线连接回归。
- GUI、CLI、JSON CLI 均可完成能力检查、配对和连接；三端对同一底层错误的错误类别一致率达到 100%。
- 配对码不得出现在进程命令行、AdbPilot 日志、GUI 输出、JSON 成功/失败响应或异常消息中；自动化安全测试通过率为 100%。
- 用户触发 GUI 配对或连接后 200 ms 内进入“执行中”状态，网络命令在 60 秒内成功、失败或超时返回，Tk 主线程不得阻塞。

## 2. User Experience & Functionality

### User Personas

- Android 开发者：需要在 Android 11–17+ 真机之间切换，不希望记忆不同无线调试命令。
- 测试工程师：需要通过 CLI 或 JSON CLI 稳定地配对、连接设备并获得可机读结果。
- 售后与现场支持人员：面对不同 Android 版本、厂商 ROM 和 platform-tools 时，需要明确的操作指引与错误提示。

### User Flow

```text
进入“无线调试”
  ├─ 检查能力
  │    ├─ 仅经典 TCP/IP → 展示连接入口与升级建议
  │    ├─ 支持安全配对 → 展示配对码入口
  │    └─ adb 37+ → 标记“可兼容 Wi-Fi 2.0”
  ├─ 经典 TCP/IP
  │    └─ 输入连接地址 → 连接 → 刷新设备列表
  └─ 安全无线调试（Wi-Fi 1.0 / 2.0）
       └─ 输入配对地址和六位配对码 → 配对
            ├─ 设备自动上线 → 完成
            └─ 未自动上线 → 输入无线调试主页连接地址 → 连接 → 刷新设备列表
```

配对地址和连接地址可能不同，且端口可能变化。界面与文案必须分别命名，不使用一个含糊的“设备地址”字段承载两者。

### User Stories

#### Story 1：识别当前无线调试能力

As a 开发者, I want to 在操作前看到本机 adb 的无线能力等级 so that 我可以选择有效流程，而不是在失败后猜测原因。

Acceptance Criteria:

- 显示 adb 版本、经典 TCP/IP 支持状态、安全配对支持状态、mDNS 可用状态和 Wi-Fi 2.0 主机兼容状态。
- adb 不存在、版本低于安全配对最低要求、`adb pair` 不可用或 mDNS 检查失败时，返回不同的受控状态和处理建议。
- 未连接设备时不得声称已确认手机是 Wi-Fi 1.0 或 2.0；只能说明“主机具备对应兼容能力”。设备上线并读取 SDK 后才展示设备侧判断。
- 能力检查失败不得禁用仍可工作的经典 `adb connect`。

#### Story 2：连接经典 TCP/IP 设备

As a 使用旧设备或厂商无线 ADB 的用户, I want to 输入连接地址直接连接 so that 既有无线调试流程继续可用。

Acceptance Criteria:

- 接受 IPv4、主机名及对应端口形式的连接地址；首期 IPv6 见非目标。
- 地址为空、端口缺失/非法或包含控制字符时，在调用 adb 前阻止执行并给出字段级错误。
- 调用现有 `adb connect` / `adb disconnect`，成功后刷新设备列表并显示连接类型为 Wi-Fi。
- 不自动执行 `adb tcpip 5555`，并提示用户设备必须已经开放 TCP/IP 调试端口。

#### Story 3：用六位配对码完成 Wi-Fi 1.0 / 2.0 配对

As an Android 11+ 用户, I want to 在 AdbPilot 中输入配对地址和六位配对码 so that 无需切换到外部终端即可安全配对。

Acceptance Criteria:

- GUI 明确引导用户在手机“开发者选项 → 无线调试 → 使用配对码配对设备”中获取配对地址和六位数字配对码。
- 配对地址与连接地址使用独立字段；配对码仅接受 6 位数字，提交后立即从可见输入框清除。
- AdbPilot 不把配对码拼入 adb 命令参数，而是通过标准输入交给 `adb pair`。
- 以 adb 退出码、输出语义和配对后设备可见性综合判断结果，兼容旧 platform-tools 曾存在的错误退出码行为。
- 配对成功后最多等待 10 秒检测设备自动上线；未上线时提示用户填写无线调试主页上的连接地址，不误用配对端口执行 `adb connect`。
- Wi-Fi 1.0 设备能力受限时仍可使用配对码流程；Wi-Fi 2.0 设备在 adb 37+ 上由 platform-tools 处理协议差异和可信网络自动连接。

#### Story 4：通过 CLI 与 JSON CLI 自动化无线连接

As a 测试工程师, I want to 使用稳定的命令和 JSON 契约执行能力检查、配对和连接 so that 自动化脚本无需解析 GUI 文本。

Acceptance Criteria:

- 普通 CLI 增加 `wifi-capabilities` 与 `pair <pairing-address>`；`pair` 默认安全提示输入配对码，不提供会进入 shell history 的位置参数。
- JSON CLI 增加 `wifi-capabilities` 与 `pair` 操作；`pair` 接收 `address`、`pairing_code` 和可选 `connect_address`。
- JSON CLI schema 明确标记 `pairing_code` 为敏感字段，文档推荐使用 `adbpilot-ai run -` 从标准输入传入完整请求。
- JSON 响应返回受控字段，例如 `status`、`paired`、`connected`、`device_serial`、`capabilities` 和 `next_action`，不得回显配对码。
- 三端复用同一个 `AdbClient` 实现，不复制命令拼接、结果判断或错误映射逻辑。

#### Story 5：获得可执行的失败指引

As a 现场支持人员, I want to 看到与失败原因对应的下一步 so that 我能快速恢复连接。

Acceptance Criteria:

- 至少区分：adb 缺失、adb 版本过低、配对能力不可用、地址非法、配对码非法、配对码错误/过期、配对服务不可达、配对成功但设备未上线、连接被拒绝、mDNS 不可用、命令超时。
- 每类错误包含一条不超过 120 个中文字符的用户建议，并保留底层退出码供诊断，但不包含敏感输入。
- 厂商 ROM 未实现或限制最新 API 时，提示“继续使用当前可用的配对码/经典连接流程，或升级系统与 platform-tools”，不把硬件限制错误归因于 AdbPilot。
- 错误文案不得建议关闭防火墙、删除密钥或重置全部 ADB 授权作为默认第一步；高影响操作只列在后续排障中。

### Non-Goals

- 首期不生成或展示配对二维码，也不控制手机摄像头扫描二维码。
- 首期不提供 mDNS 设备自动发现列表、持续跟踪或 ADB Server 自动恢复。
- 首期不自动执行 `adb tcpip`，不通过 USB 一键开启经典 TCP/IP 模式。
- 首期不管理、导出或删除 `~/.android/adbkey*` 等 ADB 主机密钥。
- 首期不实现跨网段、互联网中继、VPN 穿透或远程 ADB 暴露。
- 首期不承诺 IPv6-only 网络兼容；按当前 adb mDNS 命令可见能力以 IPv4/主机名为主。
- 首期不修改 Android Studio、设备系统或厂商 ROM，也不绕过 Android 的配对与授权机制。

## 3. AI System Requirements (If Applicable)

### Applicability

不适用。本功能不包含模型推理、生成式 AI、向量检索或 AI 决策。`adbpilot-ai` 只是面向自动化调用方的确定性 JSON CLI，不构成 AI 系统。

### Tool Requirements

- 不需要外部 AI 工具或 API。
- 运行时仅依赖用户配置或系统找到的 Android SDK Platform-Tools `adb`。

### Evaluation Strategy

- 不进行 AI 质量评估。
- JSON CLI 通过 schema、契约、敏感字段脱敏和跨端一致性自动化测试验证。

## 4. Technical Specifications

### Architecture Overview

```text
GUI / CLI / adbpilot-ai
        │  结构化请求
        ▼
AdbClient
  ├─ wifi_capabilities()
  ├─ pair(address, pairing_code, connect_address?)
  ├─ connect(address)
  └─ disconnect(address?)
        │  参数校验、超时、脱敏、结果归一化
        ▼
用户选择的 adb / ADB Server
        │
        ├─ 经典 TCP/IP
        ├─ ADB Wi-Fi 1.0
        └─ ADB Wi-Fi 2.0（adb 37+ 与 Android 17+）
```

- 所有界面层只消费结构化能力与结果对象。
- AdbPilot 不实现 TLS、mDNS 或 Wi-Fi 2.0 协议栈，避免复制 AOSP 逻辑和引入密钥处理风险。
- 详细类、数据流、错误模型与测试设计见关联技术方案。

### Integration Points

- `adbpilot/client.py`：能力检测、安全标准输入调用、配对/连接编排。
- `adbpilot/models.py`：无线能力、配对结果与受控状态数据模型。
- `adbpilot/errors.py`：无线连接专用输入、能力和配对错误类型。
- `adbpilot/gui.py`：无线调试分区、异步状态、成功后设备刷新。
- `adbpilot/cli.py`：`wifi-capabilities`、`pair` 命令。
- `adbpilot/ai_cli.py`：同名 JSON 操作与 schema。
- 不新增数据库、网络服务、账户系统或应用级认证；设备授权由 adb 管理。

### Compatibility Matrix

| 场景 | 设备要求 | 主机 adb | 首期产品行为 |
| --- | --- | --- | --- |
| 经典 TCP/IP | 已开放 ADB TCP 端口 | 能执行 `connect` | 直接连接/断开 |
| ADB Wi-Fi 1.0 | Android 11–16 或报告 v1.0 的兼容设备 | 最低 30.0.0，推荐最新 | 配对码配对；必要时手动输入连接地址 |
| ADB Wi-Fi 2.0 | Android 17+ 且设备实现对应 API | 37.0.0+，推荐 37.0.1+ | 配对码配对；复用 adb 的可信网络自动连接能力 |
| 新设备 + 旧 adb | Android 17+ | 低于 37.0.0 | 不冒充 2.0；允许已探测到的旧流程并提示升级 |
| 旧设备 + 新 adb | Android 11–16 | 37.0.0+ | 依赖 platform-tools 向后兼容，继续使用 1.0 配对 |

### Security & Privacy

- 配对码属于短期敏感凭据：仅驻留内存，提交后清空 GUI 字段，不写配置、不写日志、不进入 adb 进程参数。
- 地址、设备序列号和 mDNS 实例可用于本地诊断，但默认日志应脱敏；文档、截图和错误上报不记录真实配对码、完整设备 GUID、完整序列号或 Wi-Fi 名称。
- 不修改 ADB 私钥权限，不复制密钥，不提供绕过授权的能力。
- 所有输入以参数数组传递给子进程，不使用 shell 拼接；拒绝换行、NUL 和控制字符。
- `adbpilot-ai` 调用方负责保护其请求载体；产品文档明确推荐标准输入，避免 JSON 出现在 shell history 或进程列表。

### Non-Functional Requirements

- Python 3.9+，不新增第三方运行时依赖。
- 配对、连接和能力检查必须在 GUI 后台线程执行；UI 状态变更继续通过现有结果队列回到主线程。
- 默认配对/连接超时 60 秒，配对后自动上线等待最多 10 秒；所有等待必须有界。
- 同一 GUI 会话同一时间最多执行一个配对操作，重复点击必须被禁用或合并。
- 新增逻辑必须在无真机环境下可通过 mock 完成单元测试；Windows/macOS 真机矩阵作为发布前验收门禁。

## 5. Risks & Roadmap

### Phased Rollout

#### MVP

- 能力检测与结构化结果。
- 经典 TCP/IP 连接保留并改善地址校验。
- 六位配对码流程，覆盖 GUI、CLI、JSON CLI。
- 配对后自动上线检测与手动连接地址回退。
- Windows/macOS 真机验收；Linux CLI 自动化兼容测试。

#### v1.1

- 展示 `adb mdns services` 发现的配对/连接服务，减少手输地址。
- 增加无线诊断报告、mDNS 故障识别和安全的一次性恢复建议。
- 对已配对设备提供更明确的自动重连状态与端口变化提示。
- 补齐 IPv6/IPv6-only 网络调研和兼容验证。

#### v2.0

- 评估并实现二维码生成/配对流程。
- 使用 adb 37+ 的 mDNS 持续跟踪能力构建设备发现入口。
- 评估 USB 一键执行 `adb tcpip` 的安全交互。
- 建立可配置的无线设备别名、可信网络提示和历史连接管理。

### Technical Risks

| 风险 | 影响 | 首期控制措施 |
| --- | --- | --- |
| “Wi-Fi 1.0/2.0”术语与经典 TCP/IP 混淆 | 错误承诺兼容范围 | 在产品和文档中使用统一术语；设备未上线前只声明主机能力 |
| platform-tools 旧版本配对失败时退出码不可靠 | 误报成功 | 结合退出码、输出语义和设备上线验证；推荐 37.0.1+ |
| 配对端口与连接端口不同且会变化 | 用户连接失败 | UI 分字段、分步骤；不自动复用配对端口 |
| mDNS 被防火墙、AP 隔离或 VPN 影响 | 自动上线失败 | 配对后允许显式连接地址回退，提供分层提示 |
| 厂商 ROM 或硬件未实现最新 API | 2.0 能力不可用 | 安全降级到可用流程，避免把协议能力写死在 AdbPilot |
| 配对码经 CLI/JSON 泄露 | 凭据暴露 | 标准输入传给 adb；CLI 安全提示输入；JSON 推荐从 stdin 读取；全链路脱敏测试 |
| 多个 adb 二进制/Server 版本不一致 | 能力判断偏差 | 能力结果同时展示二进制路径与版本；调用始终复用同一 `AdbClient.adb_path` |

### Product Optimization Backlog

以下项目是当前尚未完整考虑或明确不纳入首期的需求，必须保留在产品待办中，不视为永久放弃：

| 优先级 | 待优化项 | 启动条件/需要补充的信息 |
| --- | --- | --- |
| P1 | mDNS 自动发现配对地址与连接地址 | MVP 真机数据表明手输地址是主要失败来源；确认 Windows/macOS mDNS 表现 |
| P1 | ADB Server mDNS 异常自动诊断/恢复 | 收集“系统可发现、adb 不可发现”的复现数据；确保不会中断已有设备 |
| P1 | IPv6 与 IPv6-only 网络 | AOSP 命令输出与各平台支持明确后建立测试矩阵 |
| P2 | 二维码配对 | 明确 AdbPilot 生成二维码、手机扫描和密钥生命周期的安全设计 |
| P2 | USB 一键开启 `adb tcpip` | 明确是否允许改变设备 adbd 监听状态及默认端口策略 |
| P2 | 已配对设备/可信网络管理 | 确认 adb 是否提供稳定、跨平台、可公开依赖的查询接口 |
| P2 | 多设备并发配对与批量连接 | 获得真实批量测试场景、并发上限和误操作防护要求 |
| P2 | 厂商 ROM 专项兼容 | 收集品牌、系统版本和失败日志，形成设备白/灰名单策略 |
| P3 | 跨网段、VPN 与远程中继 | 完成安全评审；默认不开放 ADB 到非可信网络 |
| P3 | 配对成功率和失败原因匿名遥测 | 明确隐私政策、用户授权、数据保留和脱敏要求后再设计 |

### Open Product Questions

- 是否需要把 adb 37+ 作为未来安装包内置依赖，而不是继续仅使用用户本机 adb？
- 是否需要为企业设备提供管理员预配对、MDM 或无人值守流程？
- 是否需要在 GUI 中展示“Wi-Fi 1.0 / 2.0”徽标，还是只展示用户可执行的能力状态？
- 是否需要将真实设备型号纳入公开兼容列表？若需要，必须先定义数据来源与维护责任。

这些问题不阻塞 MVP，进入产品后续评审。
