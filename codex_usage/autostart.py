"""Start at login, for the two platforms that have a desktop front end.

Windows: a value under ``HKCU\\...\\CurrentVersion\\Run``.
macOS:   a LaunchAgent plist in ``~/Library/LaunchAgents``, the same one
         ``scripts/install-macos.sh`` writes, so the installer and the menu
         toggle manage one file rather than two.

The command is built rather than borrowed from ``sys.argv``. A login launch
starts in ``C:\\Windows\\system32`` (or ``/``), so it has to name an interpreter
that can import ``codex_usage`` from wherever the package actually lives, and on
Windows it has to be ``pythonw.exe`` or every login flashes a console window.
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import List, Optional

from . import APP_NAME

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "FinnvnoiApiCheck"
AGENT_LABEL = "top.finnvnoi.fvc"

# Used when the package is not importable from a fixed location, so the login
# launch has to be told where it lives. repr() quotes the path, which keeps
# Windows backslashes and apostrophes in a user's name from breaking the line.
BOOTSTRAP = ("import sys;sys.path.insert(0,{root!r});"
             "from codex_usage.cli import main;sys.exit(main(['tray']))")


def supported() -> bool:
    return sys.platform.startswith("win") or sys.platform == "darwin"


def label() -> str:
    return "Start with Windows" if sys.platform.startswith("win") else "Start at login"


# ------------------------------------------------------------------ command
def package_root() -> str:
    """The directory that has to be on sys.path for ``import codex_usage``."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def is_installed(root: Optional[str] = None) -> bool:
    """True when ``python -m codex_usage`` resolves whatever the cwd is.

    That holds when the package sits in one of the interpreter's own library
    directories; running from a clone it does not, and the login command has to
    carry the path instead.
    """
    import sysconfig

    root = os.path.normcase(os.path.abspath(root or package_root()))
    candidates = [sysconfig.get_paths().get(name) for name in ("purelib", "platlib")]
    try:
        import site

        candidates += list(getattr(site, "getsitepackages", lambda: [])())
        candidates.append(getattr(site, "getusersitepackages", lambda: None)())
    except Exception:
        pass
    return any(directory and os.path.normcase(os.path.abspath(directory)) == root
               for directory in candidates)


def windowed_python(executable: str) -> str:
    """pythonw.exe next to python.exe: same interpreter, no console window."""
    directory, name = os.path.split(executable)
    stem, extension = os.path.splitext(name)
    if not stem.lower().startswith("python") or stem.lower().endswith("w"):
        return executable
    candidate = os.path.join(directory, stem + "w" + extension)
    return candidate if os.path.isfile(candidate) else executable


def launch_command(executable: Optional[str] = None, frozen: Optional[bool] = None,
                   root: Optional[str] = None, installed: Optional[bool] = None) -> List[str]:
    """The argv a login launch should run. Pure, so the tests can check it."""
    executable = executable or sys.executable
    if frozen is None:
        frozen = bool(getattr(sys, "frozen", False))
    if frozen:
        return [executable, "tray"]
    if sys.platform.startswith("win"):
        executable = windowed_python(executable)
    root = root or package_root()
    if installed is None:
        installed = is_installed(root)
    if installed:
        return [executable, "-m", "codex_usage", "tray"]
    return [executable, "-c", BOOTSTRAP.format(root=root)]


def command_line() -> str:
    """The same command as one string, which is what the registry stores."""
    return subprocess.list2cmdline(launch_command())


# ------------------------------------------------------------------ windows
def _windows_enabled() -> bool:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, RUN_VALUE)
        return True
    except OSError:
        return False


def _windows_set(enabled: bool) -> bool:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, command_line())
            else:
                try:
                    winreg.DeleteValue(key, RUN_VALUE)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False


# -------------------------------------------------------------------- macos
def agent_path() -> str:
    return os.path.expanduser(f"~/Library/LaunchAgents/{AGENT_LABEL}.plist")


def agent_plist(argv: Optional[List[str]] = None) -> bytes:
    """The LaunchAgent, built with plistlib so the paths need no escaping."""
    import plistlib

    return plistlib.dumps({
        "Label": AGENT_LABEL,
        "ProgramArguments": list(argv or launch_command()),
        "RunAtLoad": True,
        "KeepAlive": False,
        "ProcessType": "Interactive",
    })


def _launchctl(*arguments: str) -> None:
    """Best effort: an agent that is already loaded, or not, is not an error."""
    try:
        subprocess.run(["launchctl", *arguments], check=False,
                       capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass


def _macos_set(enabled: bool) -> bool:
    path = agent_path()
    if not enabled:
        _launchctl("unload", path)
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        except OSError:
            return False
        return True
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(agent_plist())
    except OSError:
        return False
    _launchctl("unload", path)
    _launchctl("load", path)
    return True


# ------------------------------------------------------------------- public
def enabled() -> bool:
    if sys.platform.startswith("win"):
        return _windows_enabled()
    if sys.platform == "darwin":
        return os.path.isfile(agent_path())
    return False


def set_enabled(value: bool) -> bool:
    """Returns whether the state on disk now matches. Never raises."""
    if sys.platform.startswith("win"):
        return _windows_set(value)
    if sys.platform == "darwin":
        return _macos_set(value)
    return False


def toggle() -> bool:
    set_enabled(not enabled())
    return enabled()


def describe() -> str:
    """One line for `fvc config`."""
    if not supported():
        return f"not available on this platform ({sys.platform})"
    where = RUN_KEY if sys.platform.startswith("win") else agent_path()
    return f"{'on' if enabled() else 'off'}   {where}   [{APP_NAME}]"
