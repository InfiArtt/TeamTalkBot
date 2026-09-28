"""Simple abuse-tracking utility used for rate-limiting user actions."""

import time
from typing import Dict, List, Set, Tuple


class AbuseTracker:
    """Track abuse events per key (typically IP/User) and escalate in stages."""

    def __init__(
        self,
        login_count: int,
        join_count: int,
        badword_count: int,
        window_sec: int,
        message_count: int = 7,
        message_window_sec: int = 10,
    ) -> None:
        self.default_window = float(window_sec)
        self._windows: Dict[str, float] = {
            "message": float(message_window_sec),
        }
        self._thresholds: Dict[str, int] = {
            "login": max(1, int(login_count)),
            "join": max(1, int(join_count)),
            "badword": max(1, int(badword_count)),
            "message": max(1, int(message_count)),
        }
        self._events: Dict[str, Dict[str, List[float]]] = {
            "login": {},
            "join": {},
            "badword": {},
            "message": {},
        }
        self._stages: Dict[str, Dict[str, int]] = {
            "login": {},
            "join": {},
            "badword": {},
            "message": {},
        }
        self._stage_ts: Dict[str, Dict[str, float]] = {
            "login": {},
            "join": {},
            "badword": {},
            "message": {},
        }

    def _get_window(self, kind: str) -> float:
        return self._windows.get(kind, self.default_window)


    def _clean(self, kind: str) -> None:
        now = time.time()
        window = self._get_window(kind)
        store = self._events.get(kind, {})
        drop_keys = []
        for key, timestamps in store.items():
            while timestamps and (now - timestamps[0]) > window:
                timestamps.pop(0)
            if not timestamps:
                drop_keys.append(key)
        for key in drop_keys:
            store.pop(key, None)

        stage_ts_store = self._stage_ts.get(kind, {})
        stage_store = self._stages.get(kind, {})
        drop_stages = []
        for key, ts in stage_ts_store.items():
            if (now - ts) > window:
                drop_stages.append(key)
        for key in drop_stages:
            stage_ts_store.pop(key, None)
            stage_store.pop(key, None)

    def record(self, kind: str, key: str) -> int:
        """Register an event for the given abuse kind and return the new stage (1-3) if escalated."""
        if not key:
            return 0
        if kind not in self._events:
            self._events[kind] = {}
        if kind not in self._stages:
            self._stages[kind] = {}
        if kind not in self._stage_ts:
            self._stage_ts[kind] = {}
        store = self._events[kind]
        stage_store = self._stages[kind]
        stage_ts_store = self._stage_ts[kind]
        timestamps = store.setdefault(key, [])
        now = time.time()
        window = self._get_window(kind)
        prev_stage = stage_store.get(key, 0)
        last_stage_ts = stage_ts_store.get(key)
        if prev_stage and last_stage_ts and (now - last_stage_ts) > window:
            stage_store.pop(key, None)
            stage_ts_store.pop(key, None)
            prev_stage = 0
        timestamps.append(now)
        self._clean(kind)
        threshold = self._thresholds.get(kind, 1)
        if len(store.get(key, [])) < threshold:
            return 0
        new_stage = min(prev_stage + 1, 3)
        store[key] = []
        stage_store[key] = new_stage
        stage_ts_store[key] = now
        if new_stage > prev_stage:
            return new_stage
        return 0

    def reset(self, kind: str, key: str) -> None:
        """Reset counters and stage for a key, typically after cooldown/unban."""
        self._events.get(kind, {}).pop(key, None)
        self._stages.get(kind, {}).pop(key, None)
        self._stage_ts.get(kind, {}).pop(key, None)

    def get_stage(self, kind: str, key: str) -> int:
        return self._stages.get(kind, {}).get(key, 0)

    def active_stages(self) -> List[Tuple[str, str, int, float]]:
        """Return ``(kind, key, stage, seconds until it clears)`` for escalated keys."""
        now = time.time()
        result = []
        for kind in list(self._stages):
            self._clean(kind)
            window = self._get_window(kind)
            stage_ts = self._stage_ts.get(kind, {})
            for key, stage in self._stages[kind].items():
                left = window - (now - stage_ts.get(key, now))
                result.append((kind, key, stage, max(0.0, left)))
        return sorted(result, key=lambda item: (item[0], item[1]))

    def tracked_keys(self) -> Set[Tuple[str, str]]:
        """Return every ``(kind, key)`` that still has events or a stage."""
        keys: Set[Tuple[str, str]] = set()
        for kind in list(self._events):
            self._clean(kind)
            keys.update((kind, key) for key in self._events[kind])
            keys.update((kind, key) for key in self._stages.get(kind, {}))
        return keys
