"""PyInstaller entry point for the terminal build."""

import sys

from codex_usage.cli import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["tui"]))
