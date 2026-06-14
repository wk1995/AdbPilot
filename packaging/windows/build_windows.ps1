param(
    [ValidateSet("onedir", "onefile")]
    [string]$Mode = "onedir"
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $Root

python -m pip install --upgrade pip
python -m pip install -e ".[build]"

Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue

$args = @(
    "--clean",
    "--noconfirm",
    "--windowed",
    "--name", "AdbPilot",
    "--version-file", "packaging\windows\file_version_info.txt",
    "packaging\entrypoints\adbpilot_gui.py"
)

if ($Mode -eq "onefile") {
    $args = @("--onefile") + $args
}

if (Test-Path "platform-tools") {
    $args += @("--add-data", "platform-tools;platform-tools")
}
elseif (Test-Path "tools\platform-tools") {
    $args += @("--add-data", "tools\platform-tools;platform-tools")
}

python -m PyInstaller @args
Write-Host "Built dist\AdbPilot for Windows, version 0.0.3"
