"""Small on-disk JSON cache.

Morning runs happen once a day against sources that are rate-limited or
scraped, so refetching the same history repeatedly during a session is both
slow and rude. Cache entries carry their own expiry.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any


class Cache:
    def __init__(self, root: Path, default_ttl_hours: float = 12.0) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.default_ttl = default_ttl_hours * 3600
        self.enabled = True

    def _path(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in key)[:48]
        return self.root / f"{safe}.{digest}.json"

    def get(self, key: str, ttl_hours: float | None = None) -> Any | None:
        if not self.enabled:
            return None
        path = self._path(key)
        if not path.exists():
            return None
        ttl = (ttl_hours * 3600) if ttl_hours is not None else self.default_ttl
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        if time.time() - payload.get("_cached_at", 0) > ttl:
            return None
        return payload.get("data")

    def set(self, key: str, data: Any) -> None:
        if not self.enabled:
            return
        try:
            self._path(key).write_text(
                json.dumps({"_cached_at": time.time(), "data": data}),
                encoding="utf-8",
            )
        except (OSError, TypeError):
            # A cache write failure must never break a morning run.
            pass

    def clear(self) -> int:
        removed = 0
        for f in self.root.glob("*.json"):
            try:
                f.unlink()
                removed += 1
            except OSError:
                pass
        return removed
