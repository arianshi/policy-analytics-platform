"""SQLite persistence: analyses, agent events, cleaned datasets, LLM call log."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from .data_quality import ProcessedDataset

SCHEMA = """
CREATE TABLE IF NOT EXISTS analyses (
    id TEXT PRIMARY KEY, query TEXT NOT NULL, status TEXT NOT NULL, options TEXT NOT NULL DEFAULT '{}',
    title TEXT, error TEXT, result TEXT, facts INTEGER DEFAULT 0, duration_ms INTEGER,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_analyses_created ON analyses(created_at DESC);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, analysis_id TEXT NOT NULL, seq INTEGER NOT NULL, ts TEXT NOT NULL,
    agent TEXT NOT NULL, type TEXT NOT NULL, message TEXT NOT NULL, data TEXT NOT NULL DEFAULT '{}'
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_events_seq ON events(analysis_id, seq);
CREATE TABLE IF NOT EXISTS datasets (
    key TEXT PRIMARY KEY, source TEXT, title TEXT, mode TEXT, degraded INTEGER, is_mock INTEGER, retrieved_at TEXT,
    rows INTEGER, dq_score INTEGER, dq TEXT, provenance TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS dataset_rows (
    dataset_key TEXT NOT NULL, year INTEGER NOT NULL, sector TEXT, sector_label TEXT, metric TEXT, value REAL, unit TEXT
);
CREATE INDEX IF NOT EXISTS idx_rows_key ON dataset_rows(dataset_key);
CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT, analysis_id TEXT, ts TEXT NOT NULL, agent TEXT, task TEXT, provider TEXT, model TEXT,
    input_tokens INTEGER, output_tokens INTEGER, cost_usd REAL, latency_ms INTEGER, ok INTEGER, error TEXT
);
"""


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class Database:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def ping(self) -> bool:
        with self._lock:
            return self._conn.execute("SELECT 1").fetchone()[0] == 1

    # ------------------------------------------------------------------ analyses
    def create_analysis(self, analysis_id: str, query: str, options: dict[str, Any]) -> None:
        t = now()
        with self._lock, self._conn:
            self._conn.execute("INSERT INTO analyses (id, query, status, options, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                               (analysis_id, query, "queued", json.dumps(options), t, t))

    def set_status(self, analysis_id: str, status: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("UPDATE analyses SET status=?, updated_at=? WHERE id=?", (status, now(), analysis_id))

    def save_result(self, analysis_id: str, status: str, result: dict[str, Any] | None, error: str = "") -> None:
        title = (result or {}).get("report", {}) and result["report"].get("title") if result else None
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE analyses SET status=?, result=?, error=?, title=?, facts=?, duration_ms=?, updated_at=?, completed_at=? WHERE id=?",
                (status, json.dumps(result, default=str) if result else None, error or None, title, len((result or {}).get("facts", [])),
                 (result or {}).get("duration_ms"), now(), now(), analysis_id))

    def get_analysis(self, analysis_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM analyses WHERE id=?", (analysis_id,)).fetchone()
        return self._analysis_row(row, full=True) if row else None

    def list_analyses(self, limit: int = 30, offset: int = 0) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM analyses ORDER BY created_at DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
        return [self._analysis_row(r, full=False) for r in rows]

    def delete_analysis(self, analysis_id: str) -> bool:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM events WHERE analysis_id=?", (analysis_id,))
            self._conn.execute("DELETE FROM llm_calls WHERE analysis_id=?", (analysis_id,))
            return self._conn.execute("DELETE FROM analyses WHERE id=?", (analysis_id,)).rowcount > 0

    def fail_orphans(self) -> int:
        """Analyses left queued/running by a previous process can never finish - mark them failed."""
        with self._lock, self._conn:
            return self._conn.execute(
                "UPDATE analyses SET status='failed', error='Interrupted by a server restart', completed_at=?, updated_at=? WHERE status IN ('queued','running')",
                (now(), now())).rowcount

    @staticmethod
    def _analysis_row(row: sqlite3.Row, *, full: bool) -> dict[str, Any]:
        d = {k: row[k] for k in ("id", "query", "status", "title", "error", "facts", "duration_ms", "created_at", "updated_at", "completed_at")}
        d["options"] = json.loads(row["options"] or "{}")
        if full:
            d["result"] = json.loads(row["result"]) if row["result"] else None
        return d

    # ------------------------------------------------------------------ events
    def add_event(self, analysis_id: str, agent: str, type_: str, message: str, data: dict[str, Any] | None, ts: str | None = None) -> dict[str, Any]:
        with self._lock, self._conn:
            seq = (self._conn.execute("SELECT COALESCE(MAX(seq),0)+1 FROM events WHERE analysis_id=?", (analysis_id,)).fetchone()[0])
            ts = ts or now()
            self._conn.execute("INSERT INTO events (analysis_id, seq, ts, agent, type, message, data) VALUES (?,?,?,?,?,?,?)",
                               (analysis_id, seq, ts, agent, type_, message, json.dumps(data or {}, default=str)))
        return {"analysis_id": analysis_id, "seq": seq, "ts": ts, "agent": agent, "type": type_, "message": message, "data": data or {}}

    def get_events(self, analysis_id: str, after: int = 0) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM events WHERE analysis_id=? AND seq>? ORDER BY seq", (analysis_id, after)).fetchall()
        return [{"analysis_id": r["analysis_id"], "seq": r["seq"], "ts": r["ts"], "agent": r["agent"], "type": r["type"], "message": r["message"],
                 "data": json.loads(r["data"])} for r in rows]

    # ------------------------------------------------------------------ datasets
    def store_dataset(self, p: ProcessedDataset) -> int:
        df: pd.DataFrame = p.df[["year", "sector", "sector_label", "metric", "value", "unit"]]
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM dataset_rows WHERE dataset_key=?", (p.spec.key,))
            self._conn.executemany(
                "INSERT INTO dataset_rows (dataset_key, year, sector, sector_label, metric, value, unit) VALUES (?,?,?,?,?,?,?)",
                [(p.spec.key, int(r.year), r.sector if isinstance(r.sector, str) else None, r.sector_label, r.metric, float(r.value), r.unit) for r in df.itertuples()])
            self._conn.execute(
                "INSERT OR REPLACE INTO datasets (key, source, title, mode, degraded, is_mock, retrieved_at, rows, dq_score, dq, provenance, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (p.spec.key, p.spec.source, p.spec.title, p.prov.mode, int(p.prov.degraded), int(p.prov.is_mock), p.prov.retrieved_at, len(df), p.dq.score,
                 json.dumps(p.dq.to_dict(), default=str), json.dumps(p.prov.to_dict()), now()))
        return len(df)

    def list_datasets(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT key, source, title, mode, degraded, is_mock, retrieved_at, rows, dq_score, updated_at FROM datasets ORDER BY key").fetchall()
        return [dict(r) for r in rows]

    def dataset_rows(self, key: str, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT year, sector, sector_label, metric, value, unit FROM dataset_rows WHERE dataset_key=? ORDER BY sector, year LIMIT ?",
                                      (key, limit)).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ llm
    def add_llm_call(self, rec: dict[str, Any]) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO llm_calls (analysis_id, ts, agent, task, provider, model, input_tokens, output_tokens, cost_usd, latency_ms, ok, error) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (rec.get("analysis_id"), now(), rec.get("agent"), rec.get("task"), rec.get("provider"), rec.get("model"), rec.get("input_tokens", 0),
                 rec.get("output_tokens", 0), rec.get("cost_usd", 0.0), rec.get("latency_ms", 0), int(bool(rec.get("ok"))), rec.get("error", "")))

    def llm_totals(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT provider, model, SUM(ok) AS ok_calls, COUNT(*)-SUM(ok) AS failed_calls, SUM(input_tokens) AS input_tokens, SUM(output_tokens) AS output_tokens, "
                "ROUND(SUM(cost_usd),6) AS cost_usd, CAST(AVG(CASE WHEN ok=1 THEN latency_ms END) AS INTEGER) AS avg_latency_ms FROM llm_calls GROUP BY provider, model").fetchall()
        return [dict(r) for r in rows]
