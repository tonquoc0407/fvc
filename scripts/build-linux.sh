#!/usr/bin/env bash
# Build the terminal dashboard as a standalone binary.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")/.."
python3 -m pip install --upgrade pyinstaller
python3 -m PyInstaller --noconfirm --clean --onefile --name fvc entry_tui.py
echo "Built dist/fvc"
