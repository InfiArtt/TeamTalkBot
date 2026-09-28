"""Usernames and IP addresses that automatic moderation never punishes."""

import os
from typing import Iterable, List, Set


class AbuseWhitelist:
    """Maintain the whitelist file (one username or IP per line)."""

    def __init__(self) -> None:
        self._entries: Set[str] = set()
        self._file_path: str = ""

    def load_file(self, path: str) -> None:
        """Load entries from ``path``, creating the file when missing."""
        ap = os.path.abspath(path or "abuse_whitelist.txt")
        os.makedirs(os.path.dirname(ap), exist_ok=True)
        if not os.path.exists(ap):
            with open(ap, "w", encoding="utf-8") as file_handle:
                file_handle.write("")
        self._file_path = ap
        self._entries.clear()
        try:
            with open(ap, "r", encoding="utf-8", errors="ignore") as file_handle:
                for raw in file_handle:
                    parts = [self._normalize(part) for part in raw.split(",")]
                    self._entries.update(part for part in parts if part)
        except Exception:
            self._entries = set()

    def _normalize(self, entry: str) -> str:
        return str(entry or "").strip().lower()

    def covers(self, username: str, ip: str) -> bool:
        """Return True if the username or the IP address is whitelisted."""
        return bool(
            (username and self._normalize(username) in self._entries)
            or (ip and self._normalize(ip) in self._entries)
        )

    def add(self, entries: Iterable[str]) -> List[str]:
        """Add entries and return the ones that were new."""
        added = sorted({self._normalize(e) for e in entries if self._normalize(e)} - self._entries)
        if added:
            self._entries.update(added)
            self._save()
        return added

    def remove(self, entries: Iterable[str]) -> List[str]:
        """Remove entries and return the ones that were present."""
        removed = sorted({self._normalize(e) for e in entries} & self._entries)
        if removed:
            self._entries.difference_update(removed)
            self._save()
        return removed

    def list_entries(self) -> List[str]:
        return sorted(self._entries)

    def _save(self) -> None:
        if not self._file_path:
            return
        try:
            with open(self._file_path, "w", encoding="utf-8") as file_handle:
                file_handle.write("\n".join(sorted(self._entries)))
        except Exception:
            pass
