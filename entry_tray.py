"""PyInstaller entry point for the Windows tray build."""

import sys

from codex_usage.cli import main

if __name__ == "__main__":
    argv = sys.argv[1:] or ["tray"]
    sys.exit(main(argv))
