# Build a single .exe that lives in the system tray, with no console window.
# Needs Python 3.9+ and: pip install pyinstaller pystray pillow
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

python -m pip install --upgrade pyinstaller pystray pillow

python -m PyInstaller `
    --noconfirm --clean `
    --onefile --noconsole `
    --name FinnvnoiApiCheck `
    --hidden-import pystray._win32 `
    --collect-submodules pystray `
    --collect-submodules PIL `
    entry_tray.py

Write-Host ""
Write-Host "Built dist\FinnvnoiApiCheck.exe" -ForegroundColor Green
Write-Host "Run it and the icon appears in the system tray."
