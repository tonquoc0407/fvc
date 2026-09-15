#!/usr/bin/env bash
# Installs Finnvnoi API Check Usage Monitor as a menu bar app for the current user.
#
# Copies the source into Application Support, builds a private virtual
# environment, installs rumps, and writes a launcher. Nothing needs sudo and
# nothing touches the system Python.
#
#   ./scripts/install-macos.sh              install and start
#   ./scripts/install-macos.sh --autostart  also start at login
#   ./scripts/install-macos.sh --no-launch  install only

set -euo pipefail

AUTOSTART=0
LAUNCH=1
for arg in "$@"; do
    case "$arg" in
        --autostart) AUTOSTART=1 ;;
        --no-launch) LAUNCH=0 ;;
        -h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done

SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="$HOME/Library/Application Support/finnvnoi-api-check"
APP_DIR="$ROOT/app"
VENV="$ROOT/venv"
LAUNCHER="$ROOT/fvc"
AGENT="$HOME/Library/LaunchAgents/top.finnvnoi.finnvnoi-api-check.plist"

step() { printf '\033[36m==> %s\033[0m\n' "$1"; }
note() { printf '\033[90m    %s\033[0m\n' "$1"; }
fail() { printf '\033[31m!!  %s\033[0m\n' "$1" >&2; exit 1; }

# -- python -------------------------------------------------------------------
step "Looking for Python 3.9 or newer"
PYTHON=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3.9 python3; do
    path="$(command -v "$candidate" 2>/dev/null || true)"
    [ -n "$path" ] || continue
    if "$path" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
        PYTHON="$path"
        break
    fi
done
if [ -z "$PYTHON" ]; then
    fail "No suitable Python found. Install one with:
        xcode-select --install        # Apple command line tools
        brew install python@3.13      # or Homebrew
    then run this again."
fi
note "found $PYTHON  ($("$PYTHON" --version 2>&1))"

# -- stage the source ---------------------------------------------------------
step "Staging the application in $APP_DIR"
[ -f "$SOURCE/codex_usage/__init__.py" ] || fail "Could not find codex_usage next to this script (looked in $SOURCE)."
mkdir -p "$APP_DIR"
# A leftover build/ makes setuptools package the previous sources.
rm -rf "$APP_DIR/codex_usage" "$APP_DIR/build" "$APP_DIR/codex_usage.egg-info"
cp -R "$SOURCE/codex_usage" "$APP_DIR/"
for file in pyproject.toml README.md entry_tray.py entry_tui.py; do
    [ -f "$SOURCE/$file" ] && cp "$SOURCE/$file" "$APP_DIR/"
done
find "$APP_DIR" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
note "copied from $SOURCE"

# -- virtual environment ------------------------------------------------------
if [ -x "$VENV/bin/python" ]; then
    step "Reusing the virtual environment at $VENV"
else
    step "Creating a private virtual environment"
    "$PYTHON" -m venv "$VENV"
fi

step "Installing the app and rumps"
"$VENV/bin/python" -m pip install --upgrade --quiet pip
# Installing the package itself, not just its dependencies, is what makes
# codex_usage importable regardless of the working directory.
# --force-reinstall because re-running after a source change keeps the same
# version string, which pip would otherwise treat as already satisfied.
"$VENV/bin/python" -m pip install --quiet --force-reinstall --no-deps "$APP_DIR"
"$VENV/bin/python" -m pip install --quiet "$APP_DIR[macos]"
"$VENV/bin/python" -c 'import codex_usage, rumps' || fail "pip could not install the app and rumps."
note "codex_usage and rumps ready"

# -- launcher -----------------------------------------------------------------
step "Writing the launcher"
cat > "$LAUNCHER" <<LAUNCH
#!/bin/sh
exec "$VENV/bin/fvc" "\${@:-tray}"
LAUNCH
chmod +x "$LAUNCHER"
note "$LAUNCHER"

if [ "$AUTOSTART" = "1" ]; then
    step "Enabling start at login"
    # The app writes the agent, so the installer and the menu bar's own
    # "Start at login" toggle cannot drift into two different plists.
    "$VENV/bin/python" -c 'from codex_usage import autostart; raise SystemExit(0 if autostart.set_enabled(True) else 1)' \
        || fail "Could not write $AGENT."
    note "launchctl agent top.finnvnoi.finnvnoi-api-check"
fi

# -- report -------------------------------------------------------------------
CONFIG="$HOME/Library/Application Support/finnvnoi-api-check/config.json"
echo
step "Done"
note "app       $APP_DIR"
note "venv      $VENV"
note "launcher  $LAUNCHER"
echo
if [ "$LAUNCH" = "1" ]; then
    step "Launching"
    "$LAUNCHER" >/dev/null 2>&1 &
    note "the app asks for an API key on first launch"
else
    printf '\033[32mStart it with:\033[0m\n'
    echo "    \"$LAUNCHER\""
fi
