# Android 无线调试一直显示“正在配对设备”

## 现象

Android 设备曾通过 Android Studio 的 Wi-Fi 无线调试正常连接。电脑离开当前网络并重新接入后，原连接不再可用；重新扫描二维码时，手机一直显示“正在配对设备”，Android Studio 也没有明确报错。

本次问题发生在 macOS、Android Studio 2025.3.4 和 Android platform-tools 37.0.0 环境。该版本信息只用于复现背景，尚不能说明问题仅发生在这些版本。

## 结论

这次故障不是手机 IP 变化直接导致的。根因是网络中断后 ADB Server 的 mDNS 发现状态没有正确刷新；反复扫描同一二维码又留下了未释放的配对实例，使手机发布的服务名出现 `(2)` 后缀。Android Studio 因此能停留在等待状态，却没有完成配对。

重启 ADB Server、直接完成一次配对，再重新打开手机“无线调试”开关后，设备使用已有配对凭据自动恢复连接。

## 判定依据

排查时获得了以下证据：

1. 电脑和手机位于同一网段，手机 IP 可以正常 `ping` 通。
2. 手机显示的配对 TCP 端口可以建立连接，排除了普通网络不可达、客户端隔离和本机防火墙阻断。
3. macOS Bonjour 能发现手机发布的 `_adb-tls-pairing._tcp` 服务，但 `adb mdns services` 返回空列表，说明故障位于 ADB 的 mDNS 发现层。
4. 执行 `adb kill-server` 和 `adb start-server` 后，ADB 立即发现了同一手机的配对服务。
5. Android Studio 生成的二维码服务名不带后缀，手机实际广播的服务名带有 `(2)`，说明旧配对实例发生了名称冲突。
6. 使用当前配对地址直接执行 `adb pair` 成功；再次打开手机无线调试开关后，设备自动上线。

排查日志或文档中不要记录二维码密码、六位配对码、设备 GUID、完整设备序列号或真实 Wi-Fi 信息。

## 处理步骤

先确认 ADB 与 mDNS 状态：

```bash
adb version
adb devices -l
adb mdns check
adb mdns services
```

如果手机与电脑网络可达，但 ADB 长时间发现不到配对服务，重启 ADB Server：

```bash
adb kill-server
adb start-server
adb mdns services
```

然后关闭并重新打开手机的“开发者选项 → 无线调试”。如果 Android Studio 扫码仍停留在“正在配对设备”，改用手机的“使用配对码配对设备”，并使用该页面临时显示的配对地址：

```bash
adb pair <手机IP>:<配对端口>
```

按提示输入六位配对码。配对成功后，重新打开无线调试开关；正常情况下 ADB 会通过已保存的凭据自动连接。如果没有自动连接，再使用无线调试主页显示的连接地址：

```bash
adb connect <手机IP>:<连接端口>
adb devices -l
```

配对端口和连接端口通常不同，并且在无线调试开关重启后可能变化，必须以手机当前页面显示的值为准。

## 如何区分其他原因

| 检查结果 | 更可能的原因 | 后续动作 |
| --- | --- | --- |
| 手机 IP 无法访问 | 不同网络、访客网络或 AP/客户端隔离 | 先恢复同一局域网的双向访问 |
| 手机 IP 可访问，但配对端口不通 | 配对页面已关闭、端口已变化或无线调试未开启 | 重新打开配对页面并使用当前端口 |
| 系统 Bonjour 能发现服务，`adb mdns services` 为空 | ADB Server 的 mDNS 状态异常 | 重启 ADB Server |
| ADB 能发现带 `(2)` 等后缀的二维码服务，但 Android Studio 仍等待 | 旧二维码配对实例未释放或服务名冲突 | 退出旧配对页面，重开无线调试并生成新二维码，或改用配对码 |
| `adb pair` 成功但设备未出现在列表 | 尚未发布连接服务，或连接端口已改变 | 重开无线调试，必要时对主页端口执行 `adb connect` |

## 复发时的最短恢复路径

```bash
adb kill-server
adb start-server
```

随后关闭再打开手机无线调试。若此前已成功配对，通常不需要删除配对记录或重新扫码。
