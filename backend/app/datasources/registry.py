"""Acquire raw datasets: live API -> disk cache -> bundled snapshot, with explicit provenance.

Live failures are *raised* (not silently masked) so that the Data Extraction Agent can reason about
them and choose to fall back to a snapshot; the snapshot is then flagged ``degraded`` in provenance.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
from datetime import UTC, datetime
from typing import Any

import httpx
import pandas as pd

from ..config import Settings
from .base import DatasetSpec, DataSourceError, Provenance, RawDataset, SchemaError, utcnow_iso
from .cache import PayloadCache
from .catalog import SPEC_BY_KEY
from .faults import FaultPlan
from .http import Sleep, get_with_retry

log = logging.getLogger(__name__)

SINGSTAT_FIRST_YEAR = 2000


class DataRegistry:
    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None, sleep: Sleep = asyncio.sleep):
        self.s = settings
        self.cache = PayloadCache(settings.cache_dir, settings.cache_ttl_hours)
        self._transport = transport
        self._sleep = sleep

    # ------------------------------------------------------------------ public
    @staticmethod
    def _drift(spec: DatasetSpec, df: pd.DataFrame) -> pd.DataFrame:
        """Fault injection: simulate upstream API drift by removing a column the adapter depends on."""
        return df.drop(columns=[spec.required_columns[0]], errors="ignore")

    def spec(self, key: str) -> DatasetSpec:
        try:
            return SPEC_BY_KEY[key]
        except KeyError:
            raise DataSourceError(f"unknown dataset '{key}'", code="not_found") from None

    def has_snapshot(self, key: str) -> bool:
        spec = self.spec(key)
        return spec.source != "internal" and self._snapshot_path(spec).exists()

    async def acquire(self, key: str, *, faults: FaultPlan | None = None) -> RawDataset:
        """Fetch ``key`` from its live source (or cache). Raises :class:`DataSourceError` on failure."""
        spec = self.spec(key)
        faults = faults or FaultPlan()
        if spec.source == "internal":
            if faults.source_fault("internal"):
                raise DataSourceError("internal: simulated outage of the internal database (fault injection)", code="unavailable", source="internal")
            return self._load_internal(spec)
        fault = faults.source_fault(spec.source)
        if fault in ("unavailable", "rate_limited", "timeout"):
            raise DataSourceError(f"{spec.source}: simulated {fault.replace('_', ' ')} (fault injection)",
                                  code=fault, source=spec.source, retryable=fault != "unavailable")

        if not self.s.live_data:  # air-gapped / CI mode: snapshots *are* the configured source
            raw = self.acquire_snapshot(key, degraded=False)
            raw.prov.notes.append("LIVE_DATA=false: using bundled snapshot by configuration")
            if fault == "schema":
                raw.df = self._drift(spec, raw.df)
            return raw

        cached = self.cache.get_fresh(key)
        if cached:
            payload, info = cached
            mode, url, retrieved = "cache", info.get("url", spec.page_url), info.get("retrieved_at", utcnow_iso())
        else:
            payload, url = await self._fetch_live(spec)
            retrieved = utcnow_iso()
            self.cache.put(key, payload, {"url": url, "retrieved_at": retrieved})
            mode = "live"

        df = self._parse(spec, payload)
        if fault == "schema":
            df = self._drift(spec, df)
        prov = self._prov(spec, mode=mode, retrieved_at=retrieved, url=spec.page_url)
        prov.notes.append(f"fetched via {url.split('?')[0]}")
        return RawDataset(spec, df, prov)

    def acquire_snapshot(self, key: str, *, degraded: bool = True) -> RawDataset:
        """Load the bundled snapshot (a real past download). ``degraded`` marks a fallback after a live failure."""
        spec = self.spec(key)
        if spec.source == "internal":  # the internal extract is its own source; there is nothing older to fall back to
            raise DataSourceError(f"no snapshot exists for internal dataset '{key}'", code="not_found", source="internal")
        path = self._snapshot_path(spec)
        if not path.exists():
            raise DataSourceError(f"no bundled snapshot for '{key}'", code="not_found", source=spec.source)
        meta = self._manifest().get(key, {})
        df = self._parse(spec, path.read_bytes())
        prov = self._prov(spec, mode="snapshot", retrieved_at=meta.get("retrieved_at", "unknown"), url=spec.page_url, degraded=degraded)
        prov.notes.append(
            f"bundled snapshot retrieved {meta.get('retrieved_at', 'at an unknown time')}; "
            + ("live source was unavailable so results may lag the latest release" if degraded else "used by configuration")
        )
        return RawDataset(spec, df, prov)

    # ------------------------------------------------------------- snapshots
    def _snapshot_path(self, spec: DatasetSpec):
        return self.s.snapshot_dir / spec.snapshot_file

    def _manifest(self) -> dict[str, Any]:
        try:
            return json.loads((self.s.snapshot_dir / "manifest.json").read_text())
        except (OSError, ValueError):
            return {}

    def write_snapshot(self, spec: DatasetSpec, payload: bytes, url: str) -> None:
        """Used by ``scripts/refresh_snapshots.py``; records provenance next to the file."""
        self.s.snapshot_dir.mkdir(parents=True, exist_ok=True)
        self._snapshot_path(spec).write_bytes(payload)
        manifest = self._manifest()
        manifest[spec.key] = {
            "dataset_id": spec.dataset_id, "title": spec.title, "source_url": url, "page_url": spec.page_url,
            "retrieved_at": utcnow_iso(), "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest(),
        }
        (self.s.snapshot_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True))

    # -------------------------------------------------------------- internals
    def _prov(self, spec: DatasetSpec, *, mode: str, retrieved_at: str, url: str, degraded: bool = False) -> Provenance:
        return Provenance(
            dataset_key=spec.key, source=spec.source, source_name=spec.source_name, title=spec.title,
            dataset_id=spec.dataset_id, url=url, fmt=spec.fmt, retrieved_at=retrieved_at, mode=mode,
            degraded=degraded, is_mock=spec.is_mock, licence=spec.licence,
        )

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self._transport, follow_redirects=True)

    async def fetch_live_payload(self, spec: DatasetSpec) -> tuple[bytes, str]:
        return await self._fetch_live(spec)

    async def _fetch_live(self, spec: DatasetSpec) -> tuple[bytes, str]:
        async with self._client() as client:
            if spec.source == "mom":
                return await self._fetch_datagov(client, spec)
            if spec.source == "singstat":
                return await self._fetch_singstat(client, spec)
        raise DataSourceError(f"no live adapter for source '{spec.source}'", code="unavailable", source=spec.source)

    async def _fetch_datagov(self, client: httpx.AsyncClient, spec: DatasetSpec) -> tuple[bytes, str]:
        """Data.gov.sg v1: ``poll-download`` returns a short-lived presigned URL for the CSV."""
        poll_url = f"{self.s.datagov_base_url}/datasets/{spec.dataset_id}/poll-download"
        download_url = ""
        for attempt in range(4):
            resp = await get_with_retry(client, poll_url, self.s, source="data.gov.sg", sleep=self._sleep)
            try:
                body = resp.json()
                data = body.get("data") or {}
            except ValueError as exc:
                raise SchemaError(f"data.gov.sg returned non-JSON for {spec.dataset_id}", source="mom") from exc
            if data.get("status") == "DOWNLOAD_SUCCESS" and data.get("url"):
                download_url = data["url"]
                break
            if body.get("code", 0) != 0:
                raise DataSourceError(f"data.gov.sg: {body.get('errorMsg') or 'dataset not available'}", code="not_found", source="mom")
            await self._sleep(1.0 * (attempt + 1))
        if not download_url:
            raise DataSourceError("data.gov.sg: download not ready after polling", code="timeout", source="mom", retryable=True)
        resp = await get_with_retry(client, download_url, self.s, source="data.gov.sg", sleep=self._sleep)
        return resp.content, f"{self.s.datagov_base_url}/datasets/{spec.dataset_id}/poll-download"

    async def _fetch_singstat(self, client: httpx.AsyncClient, spec: DatasetSpec) -> tuple[bytes, str]:
        years = ",".join(str(y) for y in range(SINGSTAT_FIRST_YEAR, datetime.now(UTC).year + 1))
        url = f"{self.s.singstat_base_url}/tabledata/{spec.dataset_id}"
        resp = await get_with_retry(client, url, self.s, source="singstat", params={"timeFilter": years}, sleep=self._sleep)
        return resp.content, f"{url}?timeFilter={SINGSTAT_FIRST_YEAR}..{datetime.now(UTC).year}"

    def _load_internal(self, spec: DatasetSpec) -> RawDataset:
        path = self.s.internal_dir / spec.snapshot_file
        if not path.exists():
            raise DataSourceError(f"internal extract {path.name} not found", code="not_found", source="internal")
        df = self._parse(spec, path.read_bytes())
        retrieved = datetime.fromtimestamp(path.stat().st_mtime, UTC).replace(microsecond=0).isoformat()
        prov = self._prov(spec, mode="internal", retrieved_at=retrieved, url=spec.page_url)
        prov.notes.append("MOCK internal dataset generated by scripts/generate_internal_mock.py - illustrative only")
        return RawDataset(spec, df, prov)

    def _parse(self, spec: DatasetSpec, payload: bytes) -> pd.DataFrame:
        try:
            if spec.fmt == "csv":
                df = pd.read_csv(io.BytesIO(payload), dtype=str, keep_default_na=False)
            elif spec.fmt == "xlsx":
                df = pd.read_excel(io.BytesIO(payload), sheet_name=0, dtype=str, keep_default_na=False)
            elif spec.fmt == "json":
                df = self._parse_singstat_json(spec, payload)
            else:
                raise SchemaError(f"unsupported format {spec.fmt}", source=spec.source)
        except SchemaError:
            raise
        except Exception as exc:  # corrupt payload (HTML error page, truncated download...)
            raise SchemaError(f"{spec.key}: could not parse {spec.fmt} payload ({exc.__class__.__name__}: {exc})", source=spec.source) from exc
        df.columns = [str(c).strip() for c in df.columns]
        return df

    @staticmethod
    def _parse_singstat_json(spec: DatasetSpec, payload: bytes) -> pd.DataFrame:
        body = json.loads(payload)
        data = body.get("Data") or {}
        rows = data.get("row")
        if not rows:
            detail = body.get("Message") or data.get("error") or "no 'Data.row' in response"
            raise SchemaError(f"{spec.key}: SingStat response had no table rows ({detail})", source="singstat")
        return pd.DataFrame(rows)
