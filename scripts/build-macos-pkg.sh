#!/usr/bin/env bash
# Build a signed-ready macOS application package. Run this on macOS.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="${VERSION:-1.0.2}"
OUT="$ROOT/dist"

command -v pkgbuild >/dev/null || { echo "pkgbuild is required (install Xcode command-line tools)." >&2; exit 1; }
cd "$ROOT"
python3 -m pip install --upgrade pyinstaller rumps
python3 -m PyInstaller --noconfirm --clean --windowed --name "Finnvnoi API Check" \
  --collect-all rumps entry_tray.py
pkgbuild --component "$OUT/Finnvnoi API Check.app" \
  --install-location /Applications \
  --identifier top.finnvnoi.api-check \
  --version "$VERSION" \
  "$OUT/FinnvnoiApiCheck-$VERSION.pkg"
echo "Built $OUT/FinnvnoiApiCheck-$VERSION.pkg"
