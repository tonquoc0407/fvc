<#
    Installs Finnvnoi API Check Usage Monitor as a tray app for the current user.

    Installs Python through winget if there is none, copies the source somewhere
    local, builds a private virtual environment, and creates a Start Menu
    shortcut. Nothing needs administrator rights and nothing touches a
    machine-wide Python.

    Usage:
        powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1
        powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1 -Autostart
#>
[CmdletBinding()]
param(
    [switch]$Autostart,
    [switch]$NoLaunch,
    [string]$PythonVersion = "3.13"
)

$ErrorActionPreference = "Stop"
$Root = Join-Path $env:LOCALAPPDATA "finnvnoi-api-check"
$AppDir = Join-Path $Root "app"
$VenvDir = Join-Path $Root "venv"
$Source = Split-Path -Parent $PSScriptRoot

function Step($text) { Write-Host "==> $text" -ForegroundColor Cyan }
function Note($text) { Write-Host "    $text" -ForegroundColor DarkGray }
function Fail($text) { Write-Host "!!  $text" -ForegroundColor Red; exit 1 }

# -- python ------------------------------------------------------------------
function Test-RealPython([string]$exe) {
    # The Microsoft Store alias answers to python.exe but only advertises itself.
    if (-not $exe) { return $false }
    try { $out = & $exe --version 2>&1 } catch { return $false }
    if ("$out" -notmatch "Python (\d+)\.(\d+)") { return $false }
    return ([int]$Matches[1] -gt 3) -or ([int]$Matches[1] -eq 3 -and [int]$Matches[2] -ge 9)
}

function Find-Python {
    foreach ($candidate in @("python3", "python")) {
        $found = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($found -and (Test-RealPython $found.Source)) { return $found.Source }
    }
    $roots = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Python"),
        "C:\Program Files\Python313", "C:\Program Files\Python312", "C:\Program Files\Python311"
    )
    foreach ($base in $roots) {
        if (-not (Test-Path $base)) { continue }
        $exes = Get-ChildItem -Path $base -Filter python.exe -Recurse -Depth 2 -ErrorAction SilentlyContinue |
                Sort-Object FullName -Descending
        foreach ($exe in $exes) { if (Test-RealPython $exe.FullName) { return $exe.FullName } }
    }
    return $null
}

Step "Looking for Python 3.9 or newer"
$python = Find-Python
if ($python) {
    Note "found $python  ($(& $python --version 2>&1))"
} else {
    Note "none found - the python.exe on PATH is the Microsoft Store placeholder"
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Fail "winget is unavailable. Install Python $PythonVersion from python.org, then run this again."
    }
    Step "Installing Python $PythonVersion for this user through winget"
    winget install --id "Python.Python.$PythonVersion" --exact --scope user --silent `
        --accept-package-agreements --accept-source-agreements | Out-Null
    $python = Find-Python
    if (-not $python) { Fail "Python installed but could not be located. Open a new terminal and run this again." }
    Note "installed $python"
}

# -- stage the source ---------------------------------------------------------
Step "Staging the application in $AppDir"
if (-not (Test-Path (Join-Path $Source "codex_usage\__init__.py"))) {
    Fail "Could not find codex_usage next to this script (looked in $Source)."
}
# A UNC source such as \\wsl.localhost\... reads fine but makes a poor home for
# a venv, so the app is always copied somewhere local.
New-Item -ItemType Directory -Force -Path $AppDir | Out-Null
# Copy-Item -Recurse nests the folder when the destination already exists, and a
# leftover build/ makes setuptools package the previous sources. Clear both.
foreach ($stale in @("codex_usage", "build", "codex_usage.egg-info")) {
    $path = Join-Path $AppDir $stale
    if (Test-Path $path) { Remove-Item $path -Recurse -Force }
}
Copy-Item -Path (Join-Path $Source "codex_usage") -Destination $AppDir -Recurse -Force
foreach ($file in @("pyproject.toml", "README.md", "entry_tray.py", "entry_tui.py")) {
    $path = Join-Path $Source $file
    if (Test-Path $path) { Copy-Item $path $AppDir -Force }
}
Note "copied from $Source"

# -- virtual environment ------------------------------------------------------
$venvPython = Join-Path $VenvDir "Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Step "Creating a private virtual environment"
    & $python -m venv $VenvDir
    if (-not (Test-Path $venvPython)) { Fail "Could not create the virtual environment at $VenvDir." }
} else {
    Step "Reusing the virtual environment at $VenvDir"
}

Step "Installing the app and its dependencies"
& $venvPython -m pip install --upgrade --quiet pip 2>&1 | Out-Null
# Installing the package itself, rather than just its dependencies, is what
# makes codex_usage importable regardless of the working directory.
# --force-reinstall because re-running after a source change keeps the same
# version string, which pip would otherwise treat as already satisfied.
& $venvPython -m pip install --quiet --force-reinstall --no-deps "$AppDir" 2>&1 | Out-Null
& $venvPython -m pip install --quiet "$AppDir[tray]" 2>&1 | Out-Null
& $venvPython -c "import codex_usage, pystray, PIL" 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Fail "pip could not install the app and its dependencies." }
Note "codex_usage, pystray and pillow ready"

# -- launchers ----------------------------------------------------------------
# The gui-script entry point already runs under pythonw, so no console appears.
$trayExe = Join-Path $VenvDir "Scripts\fvc-tray.exe"
$cliExe = Join-Path $VenvDir "Scripts\fvc.exe"
$launcher = Join-Path $Root "FinnvnoiApiCheck.cmd"
@"
@echo off
start "" "$trayExe" %*
"@ | Set-Content -Path $launcher -Encoding ASCII

$console = Join-Path $Root "FinnvnoiApiCheck-console.cmd"
@"
@echo off
REM Same app with a console attached, for reading errors.
"$cliExe" %*
pause
"@ | Set-Content -Path $console -Encoding ASCII

Step "Creating a Start Menu shortcut"
$startMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
$shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $startMenu "Finnvnoi API Check Usage.lnk"))
$shortcut.TargetPath = $trayExe
$shortcut.WorkingDirectory = $Root
$shortcut.Description = "Finnvnoi API Check API key usage monitor"
$shortcut.Save()
Note "Start Menu > Finnvnoi API Check Usage"

if ($Autostart) {
    Step "Enabling start with Windows"
    # The app writes the registry value, so the installer and the tray menu's
    # own "Start with Windows" toggle cannot drift into two different commands.
    & $venvPython -c "from codex_usage import autostart; raise SystemExit(0 if autostart.set_enabled(True) else 1)"
    if ($LASTEXITCODE -ne 0) { Fail "Could not write the Run entry." }
    Note "registered under HKCU\...\CurrentVersion\Run"
}

# -- report -------------------------------------------------------------------
$configured = $false
$configPath = Join-Path $env:APPDATA "finnvnoi-api-check\config.json"
if (Test-Path $configPath) {
    $configured = (((Get-Content $configPath -Raw | ConvertFrom-Json).keys | Measure-Object).Count -gt 0)
}

Write-Host ""
Step "Done"
Note "app       $AppDir"
Note "venv      $VenvDir"
Note "launcher  $launcher"
Write-Host ""
Write-Host "Start it from the Start Menu, or run:" -ForegroundColor Green
Write-Host "    & '$launcher'"
if (-not $configured) { Note "the app will ask for an API key on first launch" }
if (-not $NoLaunch) {
    Step "Launching"
    & $launcher
    Note "the icon should now be in the system tray"
}
