# Windows Packaging

Build AdbPilot as a Windows GUI executable with PyInstaller.

```powershell
powershell -ExecutionPolicy Bypass -File packaging/windows/build_windows.ps1
```

Single-file executable:

```powershell
powershell -ExecutionPolicy Bypass -File packaging/windows/build_windows.ps1 -Mode onefile
```

The generated executable uses GUI/windowed mode, so it does not show a Python console window.

The current Windows app version is `0.0.1`. File version metadata is defined in:

```text
packaging/windows/file_version_info.txt
```
