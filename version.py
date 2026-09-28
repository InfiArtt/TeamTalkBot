"""Utilities for reporting bot, OS, and TeamTalk SDK version details."""

from __future__ import annotations

import os
import platform
from typing import Dict

from TeamTalkPy.TeamTalk5 import getVersion

__version__ = "1.1.1"


def _sdk_version() -> str:
    """Read the TeamTalk SDK version from the native library."""
    try:
        raw = getVersion()
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "ignore")
        return raw.strip() or "Unknown"
    except Exception:
        return "Unknown"


def _bindings_path() -> str:
    """Return the absolute path to the Python binding entry file."""
    return os.path.abspath(
        os.path.join(os.path.dirname(__file__), "TeamTalkPy", "TeamTalk5.py")
    )


def _library_path() -> str:
    """Return the absolute path to the TeamTalk native library."""
    lib_file = "TeamTalk_DLL/TeamTalk5.dll" if os.name == "nt" else "libTeamTalk5.so"
    return os.path.abspath(os.path.join(os.path.dirname(__file__), lib_file))


def collect_version_info() -> Dict[str, str]:
    """Gather bot, OS, and SDK version details for outbound reporting."""
    return {
        "bot_version": __version__,
        "os_name": platform.system(),
        "os_version": platform.release(),
        "os_build": platform.version(),
        "platform": platform.platform(),
        "sdk_version": _sdk_version(),
        "bindings_path": _bindings_path(),
        "library_path": _library_path(),
    }
