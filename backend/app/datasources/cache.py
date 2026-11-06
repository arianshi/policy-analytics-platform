"""Tiny on-disk cache for raw source payloads (so reruns don't hammer rate-limited APIs)."""

from __future__ import annotations

import json
import time
from pathlib import Path


class PayloadCache:
    def __init__(self, directory: Path, ttl_hours: float):
        self.dir = directory
        self.ttl_s = ttl_hours * 3600

    def _paths(self, key: str) -> tuple[Path, Path]:
        safe = key.replace("/", "_")
        return self.dir / f"{safe}.bin", self.dir / f"{safe}.json"

    def get_fresh(self, key: str) -> tuple[bytes, dict] | None:
        body, meta = self._paths(key)
        try:
            if not body.exists() or not meta.exists():
                return None
            info = json.loads(meta.read_text())
            if time.time() - float(info.get("stored_at", 0)) > self.ttl_s:
                return None
            return body.read_bytes(), info
        except (OSError, ValueError):
            return None

    def put(self, key: str, payload: bytes, info: dict) -> None:
        body, meta = self._paths(key)
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            body.write_bytes(payload)
            meta.write_text(json.dumps({**info, "stored_at": time.time()}))
        except OSError:
            pass  # cache is best-effort
