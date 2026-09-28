"""Persistent storage for active temporary bans so they survive restarts."""

import os
from typing import Any, Dict

from data_store import load_dat, save_dat


TEMP_BANS_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "temp_bans.dat"
)


def read() -> Dict[str, Dict[str, Any]]:
    """Load the active temp bans keyed by ``"<mode>:<label>"``."""
    data = load_dat(TEMP_BANS_FILE, default=None)
    result: Dict[str, Dict[str, Any]] = {}
    if isinstance(data, dict):
        for key, entry in data.items():
            if not isinstance(key, str) or not isinstance(entry, dict):
                continue
            try:
                entry["until"] = float(entry.get("until", 0))
            except Exception:
                continue
            if entry.get("mode") in ("IPADDR", "USERNAME") and entry.get("label"):
                result[key] = entry
    return result


def write(data: Dict[str, Dict[str, Any]]) -> None:
    """Persist the active temp bans."""
    save_dat(TEMP_BANS_FILE, dict(data))
