from __future__ import annotations

import threading
import time
from typing import Any


class TTLCache:
    def __init__(self):
        self._lock = threading.RLock()
        self._store: dict[str, tuple[float, Any]] = {}

    def get(self, key: str):
        with self._lock:
            value = self._store.get(key)
            if value is None:
                return None
            expires_at, payload = value
            if expires_at < time.time():
                self._store.pop(key, None)
                return None
            return payload

    def set(self, key: str, payload: Any, ttl_sec: int):
        with self._lock:
            self._store[key] = (time.time() + max(1, int(ttl_sec)), payload)

    def delete(self, key: str):
        with self._lock:
            self._store.pop(key, None)

    def delete_prefix(self, prefix: str):
        with self._lock:
            for key in list(self._store.keys()):
                if key.startswith(prefix):
                    self._store.pop(key, None)

    def clear(self):
        with self._lock:
            self._store.clear()
