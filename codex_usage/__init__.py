"""Finnvnoi API usage checker."""

__version__ = "1.0.2"
APP_NAME = "finnvnoi-api-check"
DEFAULT_BASE_URL = "https://codex.finnvnoi.top"
DEFAULT_POLL_INTERVAL = 10.0

# One list per choice, shared by every front end, so the terminal's `w` key, the
# tray submenu and the --window flag always offer the same four windows.
WINDOW_CHOICES = ("1d", "7d", "30d", "lt")
INTERVAL_CHOICES = (5, 10, 30, 60)
