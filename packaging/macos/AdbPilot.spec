# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

ROOT = Path.cwd()
ENTRY = ROOT / "packaging" / "entrypoints" / "adbpilot_gui.py"
ICON = ROOT / "packaging" / "macos" / "AdbPilot.icns"
VERSION = (ROOT / "packaging" / "macos" / "version.txt").read_text(encoding="utf-8").strip()


datas = []
for candidate in [
    ROOT / "platform-tools",
    ROOT / "tools" / "platform-tools",
]:
    if candidate.exists():
        datas.append((str(candidate), "platform-tools"))

binaries = []
helper = ROOT / "build" / "macos-helper" / "AdbPilotFloatingHelper"
if helper.exists():
    binaries.append((str(helper), "."))


a = Analysis(
    [str(ENTRY)],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=["tkinter"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AdbPilot",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="AdbPilot",
)

app = BUNDLE(
    coll,
    name="AdbPilot.app",
    icon=str(ICON) if ICON.exists() else None,
    bundle_identifier="com.adbpilot.desktop",
    info_plist={
        "CFBundleName": "AdbPilot",
        "CFBundleDisplayName": "AdbPilot",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "11.0",
    },
)
