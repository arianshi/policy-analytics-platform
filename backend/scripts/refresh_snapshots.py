"""Re-download the real source datasets into ``data/snapshots`` using the platform's own adapters.

Usage: ``python scripts/refresh_snapshots.py [dataset_key ...]``. Data.gov.sg rate-limits (HTTP 429), so
requests are paced and retried with backoff. Provenance is written to ``data/snapshots/manifest.json``.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import Settings
from app.datasources.base import DataSourceError
from app.datasources.catalog import CATALOG
from app.datasources.registry import DataRegistry


async def main(keys: list[str]) -> int:
    settings = Settings(http_retries=6, http_backoff_s=4.0, http_timeout_s=60.0)
    registry = DataRegistry(settings)
    failures = 0
    for spec in CATALOG:
        if spec.source == "internal" or (keys and spec.key not in keys):
            continue
        try:
            payload, url = await registry.fetch_live_payload(spec)
            registry._parse(spec, payload)  # validate before overwriting a good snapshot
            registry.write_snapshot(spec, payload, url)
            print(f"ok    {spec.key:32s} {len(payload):>9,d} bytes")
        except DataSourceError as exc:
            failures += 1
            print(f"FAIL  {spec.key:32s} {exc}", file=sys.stderr)
        await asyncio.sleep(4.0)  # be polite to a rate-limited API
    return failures


if __name__ == "__main__":
    sys.exit(1 if asyncio.run(main(sys.argv[1:])) else 0)
