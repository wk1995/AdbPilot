#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

ARCH="${1:-universal2}"
case "$ARCH" in
  arm64|x86_64|universal2) ;;
  *)
    echo "Usage: $0 [arm64|x86_64|universal2]" >&2
    exit 2
    ;;
esac

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "macOS app bundles must be built on macOS." >&2
  exit 2
fi

PYTHON_BIN="${PYTHON_BIN:-python3}"
VERSION="$(tr -d '[:space:]' < packaging/macos/version.txt)"
HELPER_SOURCE="adbpilot/macos_floating_helper.swift"
HELPER_BUILD_DIR="$ROOT_DIR/build/macos-helper"
HELPER_BINARY="$HELPER_BUILD_DIR/AdbPilotFloatingHelper"

printf '__version__ = "%s"\n' "$VERSION" > adbpilot/_packaged_version.py

"$PYTHON_BIN" -m pip install --upgrade pip
"$PYTHON_BIN" -m pip install -e ".[build]"

rm -rf build dist
mkdir -p "$HELPER_BUILD_DIR"

build_helper_arch() {
  local helper_arch="$1"
  local helper_output="$2"
  xcrun swiftc "$HELPER_SOURCE" -O -target "${helper_arch}-apple-macos11.0" -o "$helper_output"
}

case "$ARCH" in
  arm64|x86_64)
    build_helper_arch "$ARCH" "$HELPER_BINARY"
    ;;
  universal2)
    build_helper_arch arm64 "$HELPER_BUILD_DIR/AdbPilotFloatingHelper-arm64"
    build_helper_arch x86_64 "$HELPER_BUILD_DIR/AdbPilotFloatingHelper-x86_64"
    xcrun lipo -create \
      "$HELPER_BUILD_DIR/AdbPilotFloatingHelper-arm64" \
      "$HELPER_BUILD_DIR/AdbPilotFloatingHelper-x86_64" \
      -output "$HELPER_BINARY"
    ;;
esac

PYINSTALLER_ARGS=(
  --clean
  --noconfirm
  --windowed
  --name AdbPilot
  --osx-bundle-identifier com.adbpilot.desktop
  --target-arch "$ARCH"
  --add-binary "$HELPER_BINARY:."
  packaging/entrypoints/adbpilot_gui.py
)

if [[ -d platform-tools ]]; then
  PYINSTALLER_ARGS+=(--add-data "platform-tools:platform-tools")
elif [[ -d tools/platform-tools ]]; then
  PYINSTALLER_ARGS+=(--add-data "tools/platform-tools:platform-tools")
fi

"$PYTHON_BIN" -m PyInstaller "${PYINSTALLER_ARGS[@]}"

echo "Built dist/AdbPilot.app for $ARCH, version $VERSION"
echo "Run: open dist/AdbPilot.app"
