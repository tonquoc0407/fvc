param([string]$Version = "1.0.0")
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$null = Set-Location $Root
$Out = Join-Path $Root "dist"
$Payload = Join-Path $Out "windows-payload"
New-Item -ItemType Directory -Force $Payload | Out-Null
python -m pip install --upgrade pyinstaller pystray pillow
python -m PyInstaller --noconfirm --clean --onefile --noconsole --name FinnvnoiApiCheck `
  --hidden-import pystray._win32 --collect-submodules pystray --collect-submodules PIL `
  (Join-Path $Root "entry_tray.py")
Copy-Item (Join-Path $Out "FinnvnoiApiCheck.exe") $Payload -Force
if (-not (Get-Command wix -ErrorAction SilentlyContinue)) { throw "WiX v4 is required: dotnet tool install --global wix" }
wix build (Join-Path $Root "packaging\windows\FinnvnoiApiCheck.wxs") `
  -d Payload="$Payload" -arch x64 -o (Join-Path $Out "FinnvnoiApiCheck-$Version.msi")
Remove-Item $Payload -Recurse -Force
Write-Host "Built $Out\FinnvnoiApiCheck-$Version.msi"
