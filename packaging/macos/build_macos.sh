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

"$PYTHON_BIN" -m pip install --upgrade pip
"$PYTHON_BIN" -m pip install -e ".[build]"

rm -rf build dist

PYINSTALLER_ARGS=(
  --clean
  --noconfirm
  --windowed
  --name AdbPilot
  --osx-bundle-identifier com.adbpilot.desktop
  --target-arch "$ARCH"
  packaging/entrypoints/adbpilot_gui.py
)

if [[ -d platform-tools ]]; then
  PYINSTALLER_ARGS+=(--add-data "platform-tools:platform-tools")
elif [[ -d tools/platform-tools ]]; then
  PYINSTALLER_ARGS+=(--add-data "tools/platform-tools:platform-tools")
fi

"$PYTHON_BIN" -m PyInstaller "${PYINSTALLER_ARGS[@]}"

echo "Built dist/AdbPilot.app for $ARCH"
echo "Run: open dist/AdbPilot.app"
