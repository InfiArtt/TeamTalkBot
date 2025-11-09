"""Helpers for persisting channel ownership metadata."""

import os
import time
from typing import Any, Dict, Optional, List, Tuple

from data_store import load_dat, save_dat

CACHE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "channel_owners.dat"
)
LEGACY_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "channel_owners.json"
)


def _normalize_path(path: str) -> str:
    """Normalize channel paths to a canonical form."""
    return (path or "").replace("\\", "/").strip("/")


def _normalize_record(value: Any) -> Dict[str, Any]:
    """Upgrade legacy string records into full metadata dictionaries."""
    now = time.time()
    if isinstance(value, dict):
        owner = str(value.get("owner", ""))
        created = _safe_timestamp(value.get("created_at"), default=now)
        last_active = _safe_timestamp(value.get("last_active_at"), default=created)
    else:
        owner = str(value or "")
        created = now
        last_active = created
    return {
        "owner": owner,
        "created_at": created,
        "last_active_at": last_active,
    }


def _safe_timestamp(value: Any, default: float) -> float:
    """Return a positive float timestamp."""
    try:
        timestamp = float(value)
    except Exception:
        timestamp = default
    if timestamp <= 0:
        timestamp = default
    return timestamp


def _load() -> Dict[str, Dict[str, Any]]:
    """Load the persisted mapping into memory."""
    data = load_dat(CACHE_FILE, default=None, legacy_json=LEGACY_FILE)
    result: Dict[str, Dict[str, Any]] = {}
    if isinstance(data, dict):
        for key, value in data.items():
            norm = _normalize_path(key)
            result[norm] = _normalize_record(value)
    return result


def _save(data: Dict[str, Dict[str, Any]]) -> None:
    """Persist ownership metadata back to disk."""
    serialized = {}
    for key, record in data.items():
        norm = _normalize_path(key)
        serialized[norm] = {
            "owner": str(record.get("owner", "")),
            "created_at": float(record.get("created_at", time.time())),
            "last_active_at": float(record.get("last_active_at", time.time())),
        }
    save_dat(CACHE_FILE, serialized)


def set_owner(
    path: str,
    username: str,
    *,
    created_at: Optional[float] = None,
    last_active_at: Optional[float] = None
) -> None:
    """Record ownership for ``path`` along with timestamps."""
    data = _load()
    key = _normalize_path(path)
    now = time.time()
    record = data.get(key, {})
    created = created_at if created_at is not None else record.get("created_at", now)
    last_active = (
        last_active_at
        if last_active_at is not None
        else record.get("last_active_at", now)
    )
    data[key] = {
        "owner": str(username or ""),
        "created_at": _safe_timestamp(created, now),
        "last_active_at": _safe_timestamp(last_active, now),
    }
    _save(data)


def touch_channel(path: str, when: Optional[float] = None) -> None:
    """Update ``last_active_at`` for ``path``."""
    data = _load()
    key = _normalize_path(path)
    if key not in data:
        return
    record = data[key]
    now = time.time() if when is None else when
    record["last_active_at"] = _safe_timestamp(now, now)
    data[key] = record
    _save(data)


def get_owner(path: str) -> Optional[str]:
    """Return the owner username for ``path`` if it exists."""
    info = get_channel_info(path)
    return info.get("owner") if info else None


def get_channel_info(path: str) -> Optional[Dict[str, Any]]:
    """Return the full record for ``path``."""
    data = _load()
    record = data.get(_normalize_path(path))
    if not record:
        return None
    return {
        "owner": str(record.get("owner", "")),
        "created_at": float(record.get("created_at", 0) or 0),
        "last_active_at": float(record.get("last_active_at", 0) or 0),
    }


def delete_owner(path: str) -> None:
    """Remove ownership tracking for ``path``."""
    data = _load()
    key = _normalize_path(path)
    if key in data:
        data.pop(key, None)
        _save(data)


def list_channels_by_owner(owner: str) -> List[str]:
    """List all channel paths owned by ``owner``."""
    owner_key = str(owner or "").strip().lower()
    if not owner_key:
        return []
    data = _load()
    result = []
    for path, record in data.items():
        try:
            if str(record.get("owner", "")).strip().lower() == owner_key:
                result.append(path)
        except Exception:
            continue
    return result


def count_channels_by_owner(owner: str) -> int:
    """Return the number of channels owned by ``owner``."""
    return len(list_channels_by_owner(owner))


def list_all_records() -> List[Tuple[str, Dict[str, Any]]]:
    """Return ``(path, record)`` tuples for every tracked channel."""
    data = _load()
    items: List[Tuple[str, Dict[str, Any]]] = []
    for path, record in data.items():
        items.append(
            (
                path,
                {
                    "owner": str(record.get("owner", "")),
                    "created_at": float(record.get("created_at", 0) or 0),
                    "last_active_at": float(record.get("last_active_at", 0) or 0),
                },
            )
        )
    return items


# Note: list_owners removed as unused to keep module minimal
