"""Compatibility helpers for converting between Python strings and TeamTalk TTCHAR arrays."""

import ctypes
import sys
from typing import Any, Optional, Tuple, Union


TTChar = Union[str, bytes]
_FieldTarget = Tuple[Any, str]


def to_tt_char(value: Any) -> TTChar:
    """
    Convert arbitrary value to TeamTalk-compatible string/buffer.
    Windows (UTF-16) expects str, other platforms expect UTF-8 bytes.
    """
    if value is None:
        value = ""

    if isinstance(value, (bytes, bytearray)):
        data = bytes(value)
        if sys.platform == "win32":
            try:
                return data.decode("utf-8")
            except Exception:
                return data.decode("utf-8", errors="ignore")
        return data

    text = str(value)
    if sys.platform == "win32":
        return text
    return text.encode("utf-8")


def _max_chars_for_field(obj: Any, attr: str) -> Optional[int]:
    cls = getattr(obj, "__class__", None)
    fields = getattr(cls, "_fields_", None)
    if not fields:
        return None
    for name, ftype in fields:
        if name != attr:
            continue
        if isinstance(ftype, type) and issubclass(ftype, ctypes.Array):
            base = getattr(ftype, "_type_", None)
            try:
                total = ctypes.sizeof(ftype)
            except Exception:
                total = None
            try:
                elem = ctypes.sizeof(base) if base is not None else None
            except Exception:
                elem = None
            if total is not None:
                if elem and elem > 0:
                    return max(0, (total // elem) - 1)
                return max(0, total - 1)
        break
    return None


def _truncate(data: TTChar, max_len: Optional[int]) -> TTChar:
    if max_len is None or max_len < 0:
        return data
    return data[:max_len]


def assign_tt_char_array(target, value: Any) -> None:
    """
    Write a Python value into a TeamTalk TTCHAR array field safely.
    """
    data = to_tt_char(value)

    if isinstance(target, tuple) and len(target) == 2:
        obj, attr = target  # type: ignore[misc]
        if not attr or obj is None:
            return
        max_len = _max_chars_for_field(obj, attr)
        truncated = _truncate(data, max_len)
        try:
            setattr(obj, attr, truncated)
        except Exception:
            # Fallback: attempt without truncation (will raise if invalid)
            setattr(obj, attr, data)
        return

    buffer = target
    try:
        max_len = len(buffer) - 1
    except TypeError:
        max_len = None

    if isinstance(data, str):
        truncated = data if max_len is None else data[:max_len]
    else:
        truncated = data if max_len is None else data[:max_len]

    try:
        buffer.value = truncated
    except AttributeError:
        raise TypeError(
            "assign_tt_char_array requires a TTCHAR array or (obj, attr)"
        ) from None


def from_tt_char(value: Any) -> str:
    """
    Convert a TeamTalk TTCHAR or related ctypes field to a Python str.
    Normalizes both UTF-16 (Windows) and UTF-8 (Unix) representations.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (bytes, bytearray, memoryview)):
        try:
            return bytes(value).decode("utf-8")
        except Exception:
            return bytes(value).decode("utf-8", errors="ignore")
    if hasattr(value, "value"):
        try:
            inner = value.value
        except Exception:
            inner = None
        if inner not in (None, value):
            return from_tt_char(inner)
    try:
        return str(value)
    except Exception:
        return ""
