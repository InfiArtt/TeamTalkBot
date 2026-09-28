"""Admin on/off switches for moderation features, kept across restarts.

Only switches that differ from config.json are stored, so config.json stays
the source of defaults and is never rewritten by the bot.
"""

import json
import os
from typing import Dict

TOGGLES_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "feature_toggles.json"
)


def read() -> Dict[str, bool]:
    """Load the stored switches (empty if the file is missing or unreadable)."""
    try:
        with open(TOGGLES_FILE, "r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, bool)}


def write(data: Dict[str, bool]) -> None:
    """Persist the switches atomically."""
    tmp_path = TOGGLES_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(dict(sorted(data.items())), fh, indent=2)
    os.replace(tmp_path, TOGGLES_FILE)
