"""Shared types for data-source adapters."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

import pandas as pd


class DataSourceError(Exception):
    """A data source could not deliver usable data. ``code`` lets agents pick a recovery strategy."""

    def __init__(self, message: str, *, code: str = "unavailable", source: str = "", retryable: bool = False):
        super().__init__(message)
        self.code = code  # unavailable | rate_limited | timeout | not_found | schema | disabled
        self.source = source
        self.retryable = retryable

    def to_dict(self) -> dict[str, Any]:
        return {"error": str(self), "code": self.code, "source": self.source, "retryable": self.retryable}


class SchemaError(DataSourceError):
    def __init__(self, message: str, *, source: str = ""):
        super().__init__(message, code="schema", source=source, retryable=False)


@dataclass(frozen=True)
class DatasetSpec:
    """Catalog entry describing one dataset the platform knows how to fetch."""

    key: str  # e.g. "mom.job_vacancies"
    source: str  # mom | singstat | internal
    source_name: str
    title: str
    description: str
    metric: str
    fmt: str  # csv | json | xlsx
    dataset_id: str  # data.gov.sg dataset id, SingStat table id, or internal file name
    page_url: str  # human-facing page, used in citations
    keywords: tuple[str, ...] = ()
    required_columns: tuple[str, ...] = ()
    snapshot_file: str = ""
    is_mock: bool = False
    licence: str = "Singapore Open Data Licence v1.0"
    # SingStat tables with several national series (not by industry): the seriesNo values to keep.
    # More than one is summed after cleaning (e.g. 3rd + 4th + 5th-and-higher order births).
    series_nos: tuple[str, ...] = ()


@dataclass
class Provenance:
    dataset_key: str
    source: str
    source_name: str
    title: str
    dataset_id: str
    url: str
    fmt: str
    retrieved_at: str
    mode: str  # live | cache | snapshot | internal
    degraded: bool = False  # True when we fell back because the live source failed
    is_mock: bool = False
    licence: str = ""
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RawDataset:
    spec: DatasetSpec
    df: pd.DataFrame  # parsed but not yet normalised/cleaned (all columns as strings for csv)
    prov: Provenance


def utcnow_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()
