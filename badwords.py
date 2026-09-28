"""Bad word filter helpers and runtime editing utilities."""

import logging
import os
import re
from fnmatch import fnmatchcase
from typing import Callable, Dict, Iterable, Iterator, Set, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# Wildcards usable in entries: * = any characters within a word, ? = one character
WILDCARDS = "*?"
# Letters/digits a wildcard entry must contain, so entries like "*" or "a*"
# cannot match (and punish) nearly every message.
MIN_PATTERN_LETTERS = 3
_EDGE_SYMBOLS = re.compile(r"^[\W_]+|[\W_]+$")
# A letter typed 3+ times in a row marks a stretched word ("anjiiiing"); such
# words are also compared with repeated letters collapsed ("anjing"). Plain
# double letters are left alone so e.g. "cook" is not read as "cok".
_STRETCHED = re.compile(r"([^\W\d_])\1\1")
_REPEATED_LETTERS = re.compile(r"([^\W\d_])\1+")


def _collapse(word: str) -> str:
    """Reduce every run of a repeated letter to one letter."""
    return _REPEATED_LETTERS.sub(r"\1", word)


def _squashed(words: Sequence[str]) -> List[Optional[str]]:
    """Collapsed form of each stretched word, None for the others."""
    return [_collapse(w) if _STRETCHED.search(w) else None for w in words]


class BadWordsFilter:
    """Maintain the active badword list and provide mutation helpers."""

    def __init__(self) -> None:
        self._words: Set[str] = set()
        self._file_path: str = ""
        # Lookup structures derived from _words by _rebuild_index(); each keeps
        # the original entry so matches can be reported (see matching_entries).
        # Phrases/patterns hold (parts, collapsed parts, entry).
        self._single: Dict[str, List[str]] = {}
        self._single_collapsed: Dict[str, List[str]] = {}
        self._phrases: List[Tuple[Tuple[str, ...], Tuple[str, ...], str]] = []
        self._patterns: List[Tuple[Tuple[str, ...], Tuple[str, ...], str]] = []

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
        self._rebuild_index()

    def _normalize_word(self, word: str) -> str:
        """Return a lowercase trimmed representation of ``word``."""
        return str(word or "").strip().lower()

    def pattern_error(self, word: str) -> str:
        """Return why ``word`` cannot be used as a wildcard entry, or ''."""
        normalized = self._normalize_word(word)
        if not any(ch in normalized for ch in WILDCARDS):
            return ""
        letters = sum(1 for ch in normalized if ch.isalnum())
        if letters < MIN_PATTERN_LETTERS:
            return (
                f"'{normalized}' is too broad: wildcard entries need at least "
                f"{MIN_PATTERN_LETTERS} letters or digits besides * and ?."
            )
        return ""

    def _rebuild_index(self) -> None:
        """Sort entries into single words, phrases and wildcard patterns."""
        single: Dict[str, List[str]] = {}
        single_collapsed: Dict[str, List[str]] = {}
        phrases: List[Tuple[Tuple[str, ...], Tuple[str, ...], str]] = []
        patterns: List[Tuple[Tuple[str, ...], Tuple[str, ...], str]] = []
        for word in self._words:
            if any(ch in word for ch in WILDCARDS):
                error = self.pattern_error(word)
                if error:
                    logger.warning("Ignoring badword entry %s", error)
                    continue
                # "[" would start a character class in fnmatch; keep it literal
                parts = tuple(part.replace("[", "[[]") for part in word.split())
                patterns.append((parts, tuple(map(_collapse, parts)), word))
                continue
            tokens = self._tokenize(word)
            if len(tokens) == 1:
                single.setdefault(tokens[0], []).append(word)
                single_collapsed.setdefault(_collapse(tokens[0]), []).append(word)
            elif tokens:
                phrases.append((tuple(tokens), tuple(map(_collapse, tokens)), word))
        self._single, self._single_collapsed = single, single_collapsed
        self._phrases, self._patterns = phrases, patterns

    def add_words(self, words: Iterable[str]) -> List[str]:
        """Add new words to the set and return the ones that were newly added.

        Wildcard entries rejected by :meth:`pattern_error` are skipped.
        """
        cleaned = {
            self._normalize_word(word)
            for word in words
            if self._normalize_word(word) and not self.pattern_error(word)
        }
        if not cleaned:
            return []
        added = sorted(cleaned - self._words)
        if not added:
            return []
        self._words.update(added)
        self._rebuild_index()
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
        self._rebuild_index()
        self._save()
        return removed

    def list_words(self) -> List[str]:
        """Return the current badword list in sorted order."""
        return sorted(self._words)

    def _tokenize(self, text: str) -> List[str]:
        """Split ``text`` into lowercase word tokens."""
        processed = str(text or "").lower().replace("_", " ")
        return [token for token in re.split(r"\W+", processed) if token]

    def contains(self, text: str) -> bool:
        """Return True if ``text`` contains any configured badword."""
        return any(True for _ in self._iter_matches(text))

    def matching_entries(self, text: str) -> List[str]:
        """Return every entry that ``text`` would be flagged by, sorted."""
        return sorted(set(self._iter_matches(text)))

    def _iter_matches(self, text: str) -> Iterator[str]:
        """Yield the entries matching ``text`` (possibly with repeats)."""
        if not text:
            return
        tokens = self._tokenize(text)
        squashed = _squashed(tokens)
        for token, collapsed in zip(tokens, squashed):
            yield from self._single.get(token, ())
            if collapsed is not None:
                yield from self._single_collapsed.get(collapsed, ())
        # Multi-word entries (e.g. "orang gila") never equal a single token;
        # match them as consecutive tokens instead.
        for parts, collapsed_parts, entry in self._phrases:
            if self._matches_run(tokens, squashed, parts, collapsed_parts, str.__eq__):
                yield entry
        if self._patterns:
            # Whitespace-separated words keep their inner symbols, so a censored
            # "an*ing" typed in chat is caught too, not only "anjing".
            lowered = str(text).lower().replace("_", " ")
            raw_words = [w for w in (_EDGE_SYMBOLS.sub("", w) for w in lowered.split()) if w]
            raw_squashed = _squashed(raw_words)
            for parts, collapsed_parts, entry in self._patterns:
                if self._matches_run(
                    tokens, squashed, parts, collapsed_parts, fnmatchcase
                ) or self._matches_run(
                    raw_words, raw_squashed, parts, collapsed_parts, fnmatchcase
                ):
                    yield entry

    @staticmethod
    def _matches_run(
        words: Sequence[str],
        squashed: Sequence[Optional[str]],
        parts: Tuple[str, ...],
        collapsed_parts: Tuple[str, ...],
        match: Callable[[str, str], bool],
    ) -> bool:
        """Return True if consecutive ``words`` match ``parts``.

        A stretched word (``squashed`` entry not None) may instead match in its
        collapsed form against the collapsed part.
        """
        size = len(parts)
        for start in range(len(words) - size + 1):
            if all(
                match(words[start + i], parts[i])
                or (
                    squashed[start + i] is not None
                    and match(squashed[start + i], collapsed_parts[i])
                )
                for i in range(size)
            ):
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
