# Finnvnoi API Check

Check Finnvnoi API key usage from a terminal or desktop tray/menu bar.

## Install

macOS:

```bash
bash scripts/install-macos.sh
```

Windows:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1
```

From source:

```bash
python3 -m pip install -e ".[tray,macos]"
fvc key add work
fvc tui
```

## Commands

```bash
fvc tui
fvc tray
fvc once
fvc key add work
fvc key list
```

On the first `tui` or `tray` launch, the app asks for the API key and saves it
as `default`. The Linux package also installs a desktop launcher for the TUI.

Configuration is stored per user in `fvc/config.json`. Environment variables:

```bash
FINNVNOI_API_KEY
FINNVNOI_API_BASE_URL
FINNVNOI_API_POLL_INTERVAL
FINNVNOI_API_CONFIG_DIR
```

## Install packages

Build on the target operating system:

```bash
scripts/build-linux-deb.sh       # dist/fvc_1.0.0_all.deb
scripts/build-macos-pkg.sh       # dist/FinnvnoiApiCheck-1.0.0.pkg (macOS)
```

```powershell
scripts\build-windows-msi.ps1   # dist\FinnvnoiApiCheck-1.0.0.msi (Windows; WiX v4 required)
```

GitHub Actions builds all three packages automatically on pushes and pull requests.
Push a tag such as `v1.0.0` to build and publish a GitHub Release:

```bash
git tag v1.0.0
git push origin v1.0.0
```
