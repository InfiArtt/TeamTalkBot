"""Persistent storage helpers for registration throttling."""

import os
from typing import Dict

from data_store import load_dat, save_dat


THROTTLE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "registration_throttle.dat"
)
LEGACY_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "registration_throttle.json"
)


def _load() -> Dict[str, Dict[str, float]]:
    data = load_dat(THROTTLE_FILE, default=None, legacy_json=LEGACY_FILE)
    if isinstance(data, dict):
        result: Dict[str, Dict[str, float]] = {}
        for ip, payload in data.items():
            if not isinstance(ip, str) or not isinstance(payload, dict):
                continue
            try:
                count = int(payload.get("count", 0))
                expiry = float(payload.get("expiry", 0))
            except Exception:
                continue
            if expiry > 0:
                result[ip] = {"count": max(0, count), "expiry": float(expiry)}
        return result
    return {}


def _save(data: Dict[str, Dict[str, float]]) -> None:
    serializable = {}
    for ip, payload in data.items():
        if not isinstance(ip, str):
            continue
        if not isinstance(payload, dict):
            continue
        try:
            count = int(payload.get("count", 0))
            expiry = float(payload.get("expiry", 0))
        except Exception:
            continue
        serializable[ip] = {"count": max(0, count), "expiry": expiry}
    save_dat(THROTTLE_FILE, serializable)


def read() -> Dict[str, Dict[str, float]]:
    """Load the current throttle state."""
    return _load()


def write(data: Dict[str, Dict[str, float]]) -> None:
    """Persist the throttle state."""
    _save(data)
