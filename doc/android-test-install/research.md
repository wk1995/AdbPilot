# AndroidTest APK 无人值守安装调研

调研日期 2026-11-11。项目基线为远端默认分支 `master` 的 `78b02ab`。本文记录官方文档、开源实现和公开的真机案例，AdbPilot 尚未在目标真机上复测。产品需求见 [PRD](prd.md)。

## 问题

在已连接的测试真机上首次安装 `androidTest` APK 时，用户遇到手机端的安装确认弹窗。插桩测试依赖主 APK 和独立的测试 APK。安装阶段等待人工点击，会让后续 `am instrument` 无法按计划执行。当前尚未取得目标机型、系统版本和弹窗原文，具体弹窗来源待确认。 [Android 命令行测试指南](https://developer.android.com/studio/test/command-line)

## Android 官方支持的行为

| 情况 | 官方行为及对本需求的影响 |
| --- | --- |
| 首次连接出现 RSA 指纹提示 | 这是对电脑的 ADB 调试授权。设备完成授权后才可执行后续命令，应在测试设备初始化时处理。 [ADB 调试授权](https://developer.android.com/tools/adb#Enabling) |
| 测试 APK 标记为 `testOnly` | 手动安装时使用 `adb install -t`。缺少该参数时可能返回 `INSTALL_FAILED_TEST_ONLY`。`-r` 用于覆盖安装并保留数据，`-g` 用于授予可授予的运行时权限。 [ADB 安装参数](https://developer.android.com/tools/adb#pm) · [AOSP 错误定义](https://android.googlesource.com/platform/frameworks/base/+/ce3ca40b4c21cf74bcf05afc14013a6ec0875dca/core/java/android/content/pm/PackageManager.java) |
| AOSP 默认的 ADB 安装流程 | Shell 具有 `INSTALL_PACKAGES`，AOSP 安装会话通常无需用户介入。厂商可对 USB 安装另加限制，Android 官方公开的 `adb install` 参数中没有通用的弹窗自动确认开关。 [AOSP Shell 权限](https://android.googlesource.com/platform/frameworks/base/+/refs/heads/main/packages/Shell/AndroidManifest.xml#184) · [AOSP 会话确认逻辑](https://android.googlesource.com/platform/frameworks/base/+/refs/heads/main/services/core/java/com/android/server/pm/PackageInstallerSession.java#1157) · [小米 USB 安装说明](https://privacy.mi.com/security-pad/en_US/) |
| 由 Android 应用发起安装 | 普通安装器使用 `USER_ACTION_NOT_REQUIRED` 仍受安装器身份、更新和目标 API 等条件限制。全托管设备的设备所有者可通过 `PackageInstaller` 无需交互地安装私有 APK，适用于已完成企业设备注册的环境。 [SessionParams API](https://developer.android.com/reference/android/content/pm/PackageInstaller.SessionParams#setRequireUserAction(int)) · [Android Enterprise 专用设备指南](https://developer.android.com/work/dpc/dedicated-devices/cookbook#install_packages) |

## GitHub 方案对照

| 方案 | 公开实现或记录 | 适用条件 |
| --- | --- | --- |
| `openatx/adbutils` 自动点击 | `--install-confirm` 在安装前启动 UiAutomator2 watcher，安装结束后清理。安装默认传 `-r -t`。CLI 传入 `uninstall=True`，解析到包名时会在安装前卸载。 [CLI 源码](https://github.com/openatx/adbutils/blob/master/adbutils/__main__.py) · [安装源码](https://github.com/openatx/adbutils/blob/master/adbutils/install.py) | 可借鉴监听的启动和清理时机。AdbPilot 应匹配已识别的安装窗口和按钮，并保留已有安装数据。上游按通用按钮文案匹配，直接用于任意机型有误点风险。 |
| `openatx/uiautomator2` 启动方式 | 所查版本通过设备端 JAR 启动服务，安装前可以独立于目标 `androidTest` APK 启动。 [项目说明](https://github.com/openatx/uiautomator2#how-it-works) · [设备端启动源码](https://github.com/openatx/uiautomator2/blob/master/uiautomator2/core.py) | 需要 ADB 已授权、屏幕可交互，且目标 ROM 允许读取和点击该安装窗口。能否实际操作弹窗需在目标设备验证。 |
| 真机初始化时首次安装，测试中覆盖更新 | `android-dev-cockpit` 在 Redmi Note 10（MIUI 14、Android 13）记录了首次安装受限、手动安装后 `adb install -r` 成功的案例。Appium 的旧 issue 另有 Redmi Note 4 上测试辅助 APK 多次提示的报告。 [Redmi Note 10 记录](https://github.com/AppSonar/android-dev-cockpit/blob/main/README.md#special-case-xiaomi--redmi--poco-miui-hyperos) · [Appium issue #10761](https://github.com/appium/appium/issues/10761) | 固定测试机、稳定包名和兼容签名，且该机型允许覆盖更新。公开案例未证明所有机型或测试 APK 都只提示一次。 |
| CI 使用干净模拟器 | Android 的 `nowinandroid` 仓库有 Gradle Managed Devices 的测试任务，`android-emulator-runner` 提供 GitHub Actions 启动 AVD 并运行插桩测试的示例。 [Now in Android 测试配置说明](https://github.com/android/nowinandroid/blob/main/AGENTS.md#instrumented-tests) · [Android Emulator Runner](https://github.com/ReactiveCircus/android-emulator-runner#usage--examples) | 适用于不依赖指定真机硬件或厂商 ROM 的回归测试。 |

## AdbPilot 基线与结论

- 桌面 GUI、`adbpilot install` 和 `adbpilot-ai install` 共用 `AdbClient.install()`。当前入口提供覆盖安装、降级和授予运行时权限选项，共用方法未传 `-t`，也没有安装弹窗处理。 [安装方法](../../adbpilot/client.py#L258) · [GUI 入口](../../adbpilot/gui.py#L2350) · [CLI 入口](../../adbpilot/cli.py#L84) · [AI CLI](../../adbpilot/ai_cli.py#L183)
- 支持 `-t` 可以独立交付。真机确认能力以明确启用、已授权的测试设备和已验证的安装器窗口为条件。先用目标设备证明「首次安装时弹窗可被检测且可点击」，再决定是否将该 ROM 列为支持对象。
- 真机复测记录至少包括型号、系统版本与构建号、连接方式、Platform Tools 版本、安装窗口包名和按钮资源 ID、首次安装与覆盖安装的实际结果，以及 ADB 返回值。对照用例覆盖普通 APK、`testOnly` APK、系统未弹窗、厂商安装确认和无法识别的弹窗。
