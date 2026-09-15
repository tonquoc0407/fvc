"""On-disk storage for settings and API keys, written with mode 0600."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from typing import Dict, List, Optional, Tuple

from . import APP_NAME, DEFAULT_BASE_URL, DEFAULT_POLL_INTERVAL

ENV_KEY = "FINNVNOI_API_KEY"
ENV_BASE_URL = "FINNVNOI_API_BASE_URL"
ENV_INTERVAL = "FINNVNOI_API_POLL_INTERVAL"


def config_dir() -> str:
    override = os.environ.get("FINNVNOI_API_CONFIG_DIR")
    if override:
        return os.path.abspath(os.path.expanduser(override))
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or os.path.expanduser("~\\AppData\\Roaming")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, APP_NAME)


def config_path() -> str:
    return os.path.join(config_dir(), "config.json")


def _default() -> Dict:
    return {
        "version": 1,
        "base_url": DEFAULT_BASE_URL,
        "poll_interval": DEFAULT_POLL_INTERVAL,
        "active": None,
        "keys": [],
    }


def load() -> Dict:
    cfg = _default()
    try:
        with open(config_path(), "r", encoding="utf-8") as handle:
            stored = json.load(handle)
        if isinstance(stored, dict):
            cfg.update(stored)
    except (OSError, ValueError):
        pass

    cfg["base_url"] = (os.environ.get(ENV_BASE_URL) or cfg.get("base_url") or DEFAULT_BASE_URL).rstrip("/")
    try:
        interval = float(os.environ.get(ENV_INTERVAL) or cfg.get("poll_interval") or DEFAULT_POLL_INTERVAL)
    except (TypeError, ValueError):
        interval = DEFAULT_POLL_INTERVAL
    cfg["poll_interval"] = max(2.0, interval)

    keys = []
    for entry in cfg.get("keys") or []:
        if isinstance(entry, dict) and entry.get("key"):
            keys.append({"label": str(entry.get("label") or "key"), "key": str(entry["key"])})
        elif isinstance(entry, str) and entry:
            keys.append({"label": f"key{len(keys) + 1}", "key": entry})
    cfg["keys"] = keys
    return cfg


def save(cfg: Dict) -> str:
    """Write atomically with mode 0600. Returns the config path."""
    directory = config_dir()
    os.makedirs(directory, exist_ok=True)
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass

    payload = {
        "version": 1,
        "base_url": cfg.get("base_url", DEFAULT_BASE_URL),
        "poll_interval": cfg.get("poll_interval", DEFAULT_POLL_INTERVAL),
        "active": cfg.get("active"),
        "keys": cfg.get("keys", []),
    }
    target = config_path()
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".config-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass
    return target


def add_key(label: str, key: str) -> Dict:
    cfg = load()
    label = (label or "key").strip() or "key"
    key = key.strip()
    entries = [e for e in cfg["keys"] if e["label"] != label]
    entries.append({"label": label, "key": key})
    cfg["keys"] = entries
    if not cfg.get("active"):
        cfg["active"] = label
    save(cfg)
    return cfg


def remove_key(label: str) -> bool:
    cfg = load()
    before = len(cfg["keys"])
    cfg["keys"] = [e for e in cfg["keys"] if e["label"] != label]
    if cfg.get("active") == label:
        cfg["active"] = cfg["keys"][0]["label"] if cfg["keys"] else None
    save(cfg)
    return len(cfg["keys"]) != before


def set_active(label: str) -> bool:
    cfg = load()
    if not any(e["label"] == label for e in cfg["keys"]):
        return False
    cfg["active"] = label
    save(cfg)
    return True


def resolve_keys(cli_key: Optional[str] = None) -> List[Tuple[str, str]]:
    """Precedence: --key, then the environment variable, then stored keys."""
    if cli_key:
        return [("cli", cli_key.strip())]
    env_key = os.environ.get(ENV_KEY, "").strip()
    entries = [(e["label"], e["key"]) for e in load()["keys"]]
    if env_key and not any(k == env_key for _, k in entries):
        entries.insert(0, ("env", env_key))
    return entries


def active_index(entries: List[Tuple[str, str]], cfg: Optional[Dict] = None) -> int:
    cfg = cfg or load()
    active = cfg.get("active")
    for i, (label, _) in enumerate(entries):
        if label == active:
            return i
    return 0
