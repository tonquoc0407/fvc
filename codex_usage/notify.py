"""Desktop notifications."""

from __future__ import annotations

import shutil
import subprocess
import sys
from typing import Callable, Optional


def _run(command: list) -> bool:
    try:
        subprocess.run(command, check=True, capture_output=True, timeout=8)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def _macos(title: str, message: str) -> bool:
    if not shutil.which("osascript"):
        return False
    # Quote by escaping, since the text carries user-visible key labels.
    safe = lambda t: t.replace("\\", "\\\\").replace('"', '\\"')
    script = f'display notification "{safe(message)}" with title "{safe(title)}"'
    return _run(["osascript", "-e", script])


def _linux(title: str, message: str, level: str) -> bool:
    if not shutil.which("notify-send"):
        return False
    urgency = "critical" if level == "critical" else "normal"
    return _run(["notify-send", "-u", urgency, "-a", "finnvnoi-api-check", title, message])


def _windows_powershell(title: str, message: str) -> bool:
    """Fallback when there is no tray icon to hang a balloon off."""
    safe = lambda t: t.replace("'", "''")
    script = (
        "[reflection.assembly]::LoadWithPartialName('System.Windows.Forms')>$null;"
        "$n=New-Object System.Windows.Forms.NotifyIcon;"
        "$n.Icon=[System.Drawing.SystemIcons]::Information;$n.Visible=$true;"
        f"$n.ShowBalloonTip(8000,'{safe(title)}','{safe(message)}',"
        "[System.Windows.Forms.ToolTipIcon]::Warning);Start-Sleep -s 9;$n.Dispose()"
    )
    return _run(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script])


class Notifier:
    """Sends a notification, remembering nothing: dedupe belongs to AlertWatcher."""

    def __init__(self, balloon: Optional[Callable[[str, str], None]] = None, enabled: bool = True):
        # `balloon` is normally pystray's Icon.notify, which needs no subprocess.
        self.balloon = balloon
        self.enabled = enabled
        self.sent = 0

    def send(self, title: str, message: str, level: str = "warning") -> bool:
        if not self.enabled:
            return False
        if self.balloon is not None:
            try:
                self.balloon(message, title)
                self.sent += 1
                return True
            except Exception:
                pass  # fall through to the platform tool

        if sys.platform == "darwin":
            delivered = _macos(title, message)
        elif sys.platform.startswith("win"):
            delivered = _windows_powershell(title, message)
        else:
            delivered = _linux(title, message, level)
        self.sent += delivered
        return delivered

    def send_alert(self, alert) -> bool:
        return self.send(alert.title, alert.body, alert.level)
