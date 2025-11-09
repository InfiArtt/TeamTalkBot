"""
Utility helpers for persisting bot data in a lightweight binary format.

The files use Python's pickle protocol so the contents are not easily readable
by text editors, yet the implementation stays dependency-free and backwards
compatible with the previous JSON-based storage.
"""

import json
import os
import pickle
from typing import Any, Optional


def _atomic_write(path: str, data: bytes) -> None:
    """Write ``data`` to ``path`` atomically to avoid torn files."""
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    tmp_path = os.path.join(directory, f".{os.path.basename(path)}.tmp")
    with open(tmp_path, "wb") as fh:
        fh.write(data)
    os.replace(tmp_path, path)


def load_dat(
    path: str, *, default: Any = None, legacy_json: Optional[str] = None
) -> Any:
    """Load pickled data, optionally migrating from a legacy JSON file."""
    if os.path.exists(path):
        try:
            with open(path, "rb") as fh:
                return pickle.load(fh)
        except Exception:
            pass

    if legacy_json and os.path.exists(legacy_json):
        try:
            with open(legacy_json, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            save_dat(path, data)
            os.remove(legacy_json)
            return data
        except Exception:
            pass

    return default


def save_dat(path: str, data: Any) -> None:
    """Persist ``data`` at ``path`` using pickle."""
    payload = pickle.dumps(data, protocol=pickle.HIGHEST_PROTOCOL)
    _atomic_write(path, payload)
