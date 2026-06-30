# macOS Packaging

AdbPilot can be packaged as a macOS `.app` with PyInstaller.

Build on macOS, not Windows. Apple Silicon, Intel, and universal builds require different Python/runtime choices:

- `arm64`: build on Apple Silicon with an arm64 Python.
- `x86_64`: build on Intel macOS, or on Apple Silicon from an x86_64/Rosetta Python.
- `universal2`: build from a universal2 Python distribution.

## Build

```bash
python3 -m venv .venv
source .venv/bin/activate
bash packaging/macos/build_macos.sh universal2
```

Architecture-specific builds:

```bash
bash packaging/macos/build_macos.sh arm64
bash packaging/macos/build_macos.sh x86_64
```

The output is:

```text
dist/AdbPilot.app
```

The current macOS app version is `0.0.3`. The bundle version is defined in:

```text
packaging/macos/version.txt
```

## ADB

The app first checks user-configured paths and common macOS Android SDK locations:

- `ADBPILOT_ADB`
- `ANDROID_HOME/platform-tools/adb`
- `ANDROID_SDK_ROOT/platform-tools/adb`
- `/opt/homebrew/bin/adb`
- `/usr/local/bin/adb`
- `~/Library/Android/sdk/platform-tools/adb`

If you want to bundle `adb`, put macOS platform-tools in one of these folders before building:

```text
platform-tools/adb
tools/platform-tools/adb
```

For split architecture bundles, these paths are also checked inside the `.app` resources:

```text
platform-tools/darwin-arm64/adb
platform-tools/darwin-x86_64/adb
platform-tools/arm64/adb
platform-tools/x86_64/adb
```

## Signing

Unsigned local builds may need:

```bash
xattr -dr com.apple.quarantine dist/AdbPilot.app
```

For distribution outside your own machine, sign and notarize with your Apple Developer identity.
