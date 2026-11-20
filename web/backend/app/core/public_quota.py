"""Single-process public AI quota guard; replace with Redis before multi-worker scaling."""
from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict
from datetime import datetime, timezone


class PublicAnalysisQuota:
    def __init__(self, daily_limit: int, *, max_tracked_clients: int = 20_000) -> None:
        self._daily_limit = max(0, daily_limit)
        self._max_tracked_clients = max_tracked_clients
        self._counts: OrderedDict[str, tuple[str, int]] = OrderedDict()
        self._lock = threading.Lock()

    @staticmethod
    def client_key(client_ip: str) -> str:
        return hashlib.sha256(client_ip.encode("utf-8", errors="ignore")).hexdigest()

    @staticmethod
    def _today() -> str:
        return datetime.now(timezone.utc).date().isoformat()

    def consume(self, client_ip: str) -> tuple[bool, int]:
        """Consume one daily analysis credit and return (allowed, remaining)."""
        key = self.client_key(client_ip)
        today = self._today()
        with self._lock:
            for stale_key, (day, _) in list(self._counts.items()):
                if day != today:
                    self._counts.pop(stale_key, None)

            day, used = self._counts.get(key, (today, 0))
            if day != today:
                used = 0
            if used >= self._daily_limit:
                self._counts.move_to_end(key)
                return False, 0

            used += 1
            self._counts[key] = (today, used)
            self._counts.move_to_end(key)
            while len(self._counts) > self._max_tracked_clients:
                self._counts.popitem(last=False)
            return True, max(0, self._daily_limit - used)

    def remaining(self, client_ip: str) -> int:
        key = self.client_key(client_ip)
        today = self._today()
        with self._lock:
            day, used = self._counts.get(key, (today, 0))
            if day != today:
                return self._daily_limit
            return max(0, self._daily_limit - used)
