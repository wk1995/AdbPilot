# AdbPilot

AdbPilot 是一个围绕 Android ADB 能力构建的跨平台命令行工具，目标是在 Windows、macOS、Linux 和 Android 无线调试场景中提供统一、稳定、易用的设备调试和管理能力。

## 项目定位

项目主要解决 Android 设备连接、应用安装调试、文件传输、日志采集、Shell 命令执行、截屏和无线调试连接等问题。当前版本优先提供 CLI 能力，后续可以扩展为桌面工具、移动端工具或上层业务系统中的 ADB 能力模块。

## 主要功能

- ADB 环境检测：检测 ADB 路径、版本和 server 状态。
- 设备管理：识别 USB 和无线设备，展示设备状态和基础信息。
- 应用管理：安装、卸载、启动、停止、清除数据、列表、详情和导出 APK。
- 文件传输：支持 push、pull、目录传输。
- 日志诊断：实时 logcat、按包名/tag/级别过滤、日志保存和 bugreport 导出。
- Shell 执行：执行单条 adb shell 命令。
- 屏幕操作：截屏、录屏、点击、滑动、输入和按键事件。
- 无线调试：连接和断开 `host:port` 形式的无线 ADB 设备。

## 平台支持

| 平台 | 支持重点 |
| --- | --- |
| Windows | ADB 驱动、路径兼容、环境变量、USB 授权 |
| macOS | Apple Silicon/Intel、执行权限、Homebrew 路径 |
| Linux | udev 规则、设备权限、发行版差异 |
| Android | 无线调试、配对连接、权限受限场景 |

## MVP 目标

当前已完成第一版 MVP：

1. ADB 路径检测和版本显示。
2. 设备列表和设备状态识别。
3. APK 安装、卸载、启动、停止、清除数据、应用列表、应用详情、APK 导出。
4. 文件上传和下载。
5. logcat 实时查看、过滤、保存和 bugreport 导出。
6. 截屏、录屏和输入控制。
7. 基础 Shell 命令执行。
8. 无线调试连接和断开。

## 安装

需要 Python 3.9+ 和 Android platform-tools。

```bash
python -m pip install -e .
```

如果 `adb` 不在系统 `PATH` 中，可以通过环境变量或命令参数指定：

```bash
set ADBPILOT_ADB=C:\Android\platform-tools\adb.exe
adbpilot --adb-path C:\Android\platform-tools\adb.exe devices
```

macOS/Linux：

```bash
export ADBPILOT_ADB=/Users/me/Library/Android/sdk/platform-tools/adb
adbpilot devices
```

## 使用示例

查看 ADB 版本：

```bash
adbpilot version
```

列出设备：

```bash
adbpilot devices
adbpilot devices --json
```

查看设备信息：

```bash
adbpilot info
adbpilot info -s emulator-5554
```

连接无线调试设备：

```bash
adbpilot connect 192.168.1.10:5555
adbpilot disconnect 192.168.1.10:5555
```

安装和卸载应用：

```bash
adbpilot install app-debug.apk
adbpilot install app-debug.apk --grant --downgrade
adbpilot uninstall com.example.app
```

管理应用：

```bash
adbpilot apps
adbpilot apps --third-party --filter example
adbpilot app-info com.example.app
adbpilot export-apk com.example.app ./apks
adbpilot start com.example.app
adbpilot stop com.example.app
adbpilot clear com.example.app
```

上传和下载文件：

```bash
adbpilot push ./demo.txt /sdcard/Download/demo.txt
adbpilot pull /sdcard/Download/demo.txt ./demo.txt
```

保存截图：

```bash
adbpilot screencap ./screenshots/home.png
adbpilot screenrecord ./screenshots/demo.mp4 --seconds 10
```

输入控制：

```bash
adbpilot tap 300 800
adbpilot swipe 500 1500 500 300 --duration-ms 400
adbpilot text hello
adbpilot key HOME
```

执行 shell 命令：

```bash
adbpilot shell -- getprop ro.product.model
```

查看并保存日志：

```bash
adbpilot logcat
adbpilot logcat --package com.example.app -o logs/app.log
adbpilot logcat --tag ActivityManager --level I
adbpilot bugreport ./reports/bugreport.zip
```

多设备场景下必须显式指定设备：

```bash
adbpilot install -s emulator-5554 app-debug.apk
```

## 推荐架构

```text
UI / CLI / API
    |
TaskRunner
    |
AdbClient
    |
PlatformAdapter
    |
adb executable
```

- `AdbClient`：统一封装 ADB 命令执行、超时、输出解析和错误处理。
- `Device`：表示单台 Android 设备，包含序列号、状态和设备属性。
- `PlatformAdapter`：处理不同操作系统下的路径、权限和执行差异。
- `TaskRunner`：处理批量任务、并发执行、失败重试和结果报告。

## 开发原则

- 所有 ADB 命令必须显式指定目标设备，避免多设备误操作。
- 不直接在业务层拼接复杂命令，统一通过封装层调用。
- 对命令超时、异常退出、stderr 和设备离线状态进行明确处理。
- 兼容中文路径、空格路径和特殊字符路径。
- 日志和报告要便于导出、复现和问题追踪。

## 文档

详细需求和阶段规划见 [plant.md](./plant.md)。

## 开发和测试

运行单元测试：

```bash
python -m unittest discover -s tests
```

当前测试覆盖 ADB 输出解析和平台路径检测。涉及真实设备的命令需要连接 Android 设备后手动验证。
