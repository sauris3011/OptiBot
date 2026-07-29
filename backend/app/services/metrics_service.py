"""Layer 6's storage half — metrics collection and the audit trail.

One SQLite table per concern:

``interactions``  one row per request, the source for every dashboard number.
``audit_log``     the governance record: who asked what, which model answered,
                  which guardrails fired, whether PII was present and masked.

Everything written here is PII-masked at the call site. The audit browser in
the dashboard reads straight from this table, so nothing unmasked can reach it.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any

from app.config import DB_PATH

_lock = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS interactions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp         TEXT    NOT NULL,
    session_id        TEXT    NOT NULL,
    mode              TEXT    NOT NULL,
    query_masked      TEXT    NOT NULL,
    tier              TEXT,
    model             TEXT,
    input_tokens      INTEGER NOT NULL DEFAULT 0,
    output_tokens     INTEGER NOT NULL DEFAULT 0,
    total_tokens      INTEGER NOT NULL DEFAULT 0,
    latency_ms        INTEGER NOT NULL DEFAULT 0,
    cost_usd          REAL    NOT NULL DEFAULT 0,
    cache_hit         INTEGER NOT NULL DEFAULT 0,
    rag_used          INTEGER NOT NULL DEFAULT 0,
    rag_sources       TEXT,
    confidence        REAL,
    guardrail_events  TEXT,
    blocked           INTEGER NOT NULL DEFAULT 0,
    error             TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp         TEXT    NOT NULL,
    session_id        TEXT    NOT NULL,
    mode              TEXT    NOT NULL,
    event_type        TEXT    NOT NULL,
    query_masked      TEXT,
    response_masked   TEXT,
    model             TEXT,
    pii_detected      TEXT,
    pii_masked        INTEGER NOT NULL DEFAULT 0,
    guardrail_events  TEXT,
    detail            TEXT
);

CREATE INDEX IF NOT EXISTS idx_interactions_mode ON interactions(mode);
CREATE INDEX IF NOT EXISTS idx_interactions_ts   ON interactions(timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_ts          ON audit_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_type        ON audit_log(event_type);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _lock, _connect() as conn:
        conn.executescript(_SCHEMA)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _dumps(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (list, dict)):
        return json.dumps(value)
    return str(value)


_INTERACTION_FIELDS = (
    "session_id", "mode", "query_masked", "tier", "model",
    "input_tokens", "output_tokens", "total_tokens", "latency_ms",
    "cost_usd", "cache_hit", "rag_used", "rag_sources", "confidence",
    "guardrail_events", "blocked", "error",
)

_AUDIT_FIELDS = (
    "session_id", "mode", "event_type", "query_masked", "response_masked",
    "model", "pii_detected", "pii_masked", "guardrail_events", "detail",
)

_JSON_FIELDS = {"rag_sources", "guardrail_events", "pii_detected", "detail"}

# A NOT NULL column with a DEFAULT still rejects an explicitly-passed NULL —
# the default only applies when the column is omitted from the INSERT. Callers
# legitimately omit these (a blocked request never reaches the model, so it has
# no token counts), so fill them here rather than making every call site do it.
_NUMERIC_DEFAULTS = {
    "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
    "latency_ms": 0, "cost_usd": 0.0, "cache_hit": 0, "rag_used": 0,
    "blocked": 0, "pii_masked": 0,
}


def _row_values(row: dict, fields: tuple[str, ...]) -> list:
    values = [_now()]
    for field in fields:
        value = row.get(field)
        if value is None and field in _NUMERIC_DEFAULTS:
            value = _NUMERIC_DEFAULTS[field]
        elif field in _JSON_FIELDS:
            value = _dumps(value)
        elif isinstance(value, bool):
            value = int(value)
        values.append(value)
    return values


def record_interaction(row: dict) -> int:
    values = _row_values(row, _INTERACTION_FIELDS)
    placeholders = ", ".join("?" * len(values))
    with _lock, _connect() as conn:
        cur = conn.execute(
            f"INSERT INTO interactions (timestamp, {', '.join(_INTERACTION_FIELDS)}) "
            f"VALUES ({placeholders})",
            values,
        )
        return int(cur.lastrowid)


def record_audit(row: dict) -> None:
    values = _row_values(row, _AUDIT_FIELDS)
    placeholders = ", ".join("?" * len(values))
    with _lock, _connect() as conn:
        conn.execute(
            f"INSERT INTO audit_log (timestamp, {', '.join(_AUDIT_FIELDS)}) "
            f"VALUES ({placeholders})",
            values,
        )


# --------------------------------------------------------------------------
# Aggregation for the dashboard
# --------------------------------------------------------------------------

_AGG_SQL = """
SELECT
    COUNT(*)                                          AS requests,
    COALESCE(AVG(total_tokens), 0)                    AS avg_tokens,
    COALESCE(SUM(total_tokens), 0)                    AS total_tokens,
    COALESCE(AVG(latency_ms), 0)                      AS avg_latency_ms,
    COALESCE(AVG(cost_usd), 0)                        AS avg_cost_usd,
    COALESCE(SUM(cost_usd), 0)                        AS total_cost_usd,
    COALESCE(AVG(confidence), 0)                      AS avg_confidence,
    COALESCE(SUM(cache_hit), 0)                       AS cache_hits,
    COALESCE(SUM(rag_used), 0)                        AS rag_used,
    COALESCE(SUM(blocked), 0)                         AS blocked
FROM interactions
WHERE mode = ? AND error IS NULL
"""


def mode_summary(mode: str) -> dict:
    with _lock, _connect() as conn:
        row = conn.execute(_AGG_SQL, (mode,)).fetchone()
        models = conn.execute(
            "SELECT model, COUNT(*) n FROM interactions "
            "WHERE mode = ? AND error IS NULL AND model IS NOT NULL "
            "GROUP BY model ORDER BY n DESC",
            (mode,),
        ).fetchall()
        tiers = conn.execute(
            "SELECT tier, COUNT(*) n FROM interactions "
            "WHERE mode = ? AND error IS NULL AND tier IS NOT NULL "
            "GROUP BY tier",
            (mode,),
        ).fetchall()

    requests = int(row["requests"] or 0)
    return {
        "mode": mode,
        "requests": requests,
        "avg_tokens": round(float(row["avg_tokens"] or 0), 1),
        "total_tokens": int(row["total_tokens"] or 0),
        "avg_latency_ms": round(float(row["avg_latency_ms"] or 0), 1),
        "avg_cost_usd": round(float(row["avg_cost_usd"] or 0), 6),
        "total_cost_usd": round(float(row["total_cost_usd"] or 0), 6),
        "avg_confidence": round(float(row["avg_confidence"] or 0), 3),
        "cache_hits": int(row["cache_hits"] or 0),
        "cache_hit_rate": round(int(row["cache_hits"] or 0) / requests, 4) if requests else 0.0,
        "rag_used": int(row["rag_used"] or 0),
        "rag_rate": round(int(row["rag_used"] or 0) / requests, 4) if requests else 0.0,
        "blocked": int(row["blocked"] or 0),
        "model_mix": {r["model"]: r["n"] for r in models},
        "tier_mix": {r["tier"]: r["n"] for r in tiers},
    }


def _pct_change(baseline: float, optimized: float) -> float:
    """Percentage reduction from baseline to optimized (positive = better)."""
    if baseline == 0:
        return 0.0
    return round((baseline - optimized) / baseline * 100, 1)


def comparison() -> dict:
    base = mode_summary("baseline")
    opt = mode_summary("optimized")
    return {
        "baseline": base,
        "optimized": opt,
        "deltas": {
            "tokens_reduction_pct": _pct_change(base["avg_tokens"], opt["avg_tokens"]),
            "latency_reduction_pct": _pct_change(base["avg_latency_ms"], opt["avg_latency_ms"]),
            "cost_reduction_pct": _pct_change(base["avg_cost_usd"], opt["avg_cost_usd"]),
            "confidence_gain_pct": _pct_change(opt["avg_confidence"], base["avg_confidence"]),
            "monthly_cost_baseline_10k": round(base["avg_cost_usd"] * 10_000, 2),
            "monthly_cost_optimized_10k": round(opt["avg_cost_usd"] * 10_000, 2),
        },
    }


def recent_interactions(limit: int = 50, mode: str | None = None) -> list[dict]:
    sql = "SELECT * FROM interactions"
    params: list[Any] = []
    if mode:
        sql += " WHERE mode = ?"
        params.append(mode)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with _lock, _connect() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def recent_audit(limit: int = 100, event_type: str | None = None) -> list[dict]:
    sql = "SELECT * FROM audit_log"
    params: list[Any] = []
    if event_type:
        sql += " WHERE event_type = ?"
        params.append(event_type)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with _lock, _connect() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def governance_summary() -> dict:
    with _lock, _connect() as conn:
        events = conn.execute(
            "SELECT event_type, COUNT(*) n FROM audit_log GROUP BY event_type"
        ).fetchall()
        # Both counters are scoped to rows that actually contained PII — a
        # masked flag on a row with no PII is not evidence of anything.
        pii_rows = conn.execute(
            "SELECT COUNT(*) n FROM audit_log "
            "WHERE pii_detected IS NOT NULL AND pii_detected NOT IN ('[]', 'null')"
        ).fetchone()
        masked = conn.execute(
            "SELECT COUNT(*) n FROM audit_log "
            "WHERE pii_detected IS NOT NULL AND pii_detected NOT IN ('[]', 'null') "
            "AND pii_masked = 1"
        ).fetchone()
        total_audit = conn.execute("SELECT COUNT(*) n FROM audit_log").fetchone()
        total_inter = conn.execute("SELECT COUNT(*) n FROM interactions").fetchone()
        blocked = conn.execute(
            "SELECT COUNT(*) n FROM interactions WHERE blocked = 1"
        ).fetchone()

    pii_events = int(pii_rows["n"] or 0)
    masked_events = int(masked["n"] or 0)
    return {
        "event_counts": {r["event_type"]: r["n"] for r in events},
        "pii_events": pii_events,
        "pii_masked_events": masked_events,
        # The headline governance number: unmasked PII that reached storage.
        "pii_unmasked_events": max(pii_events - masked_events, 0),
        "blocked_requests": int(blocked["n"] or 0),
        "audit_records": int(total_audit["n"] or 0),
        "interactions": int(total_inter["n"] or 0),
        "audit_coverage_pct": (
            100.0 if int(total_inter["n"] or 0) == 0
            else round(min(int(total_audit["n"] or 0) / int(total_inter["n"]), 1.0) * 100, 1)
        ),
    }


def reset() -> None:
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM interactions")
        conn.execute("DELETE FROM audit_log")
