"""Bad word filter helpers and runtime editing utilities."""

import os
import re
from typing import Iterable, Set, List


class BadWordsFilter:
    """Maintain the active badword list and provide mutation helpers."""

    def __init__(self) -> None:
        self._words: Set[str] = set()
        self._file_path: str = ""

    @property
    def file_path(self) -> str:
        """Return the absolute path of the active badword file."""
        return self._file_path

    def load_file(self, path: str) -> None:
        """Load badwords from ``path`` and ensure the file exists on disk."""
        ap = os.path.abspath(path or "badwords/words.txt")
        os.makedirs(os.path.dirname(ap), exist_ok=True)
        if not os.path.exists(ap):
            with open(ap, "w", encoding="utf-8") as file_handle:
                file_handle.write("")
        self._file_path = ap
        self._words.clear()
        try:
            with open(ap, "r", encoding="utf-8", errors="ignore") as file_handle:
                for raw in file_handle:
                    parts = [self._normalize_word(word) for word in raw.split(",")]
                    self._words.update([part for part in parts if part])
        except Exception:
            self._words = set()

    def _normalize_word(self, word: str) -> str:
        """Return a lowercase trimmed representation of ``word``."""
        return str(word or "").strip().lower()

    def add_words(self, words: Iterable[str]) -> List[str]:
        """Add new words to the set and return the ones that were newly added."""
        cleaned = {
            self._normalize_word(word) for word in words if self._normalize_word(word)
        }
        if not cleaned:
            return []
        added = sorted(cleaned - self._words)
        if not added:
            return []
        self._words.update(added)
        self._save()
        return added

    def remove_words(self, words: Iterable[str]) -> List[str]:
        """Remove words from the set and return the ones actually removed."""
        cleaned = {
            self._normalize_word(word) for word in words if self._normalize_word(word)
        }
        removed = sorted([word for word in cleaned if word in self._words])
        if not removed:
            return []
        for word in removed:
            self._words.discard(word)
        self._save()
        return removed

    def list_words(self) -> List[str]:
        """Return the current badword list in sorted order."""
        return sorted(self._words)

    def contains(self, text: str) -> bool:
        """Return True if ``text`` contains any configured badword."""
        if not text:
            return False
        processed = str(text).lower().replace("_", " ")
        tokens = [token for token in re.split(r"\W+", processed) if token]
        for token in tokens:
            if token in self._words:
                return True
        return False

    def _save(self) -> None:
        """Persist the current word list to disk."""
        if not self._file_path:
            return
        try:
            with open(self._file_path, "w", encoding="utf-8") as file_handle:
                file_handle.write("\n".join(sorted(self._words)))
        except Exception:
            pass
