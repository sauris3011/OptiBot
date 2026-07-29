from __future__ import annotations

from fastapi import APIRouter, Query

from app.services import metrics_service
from app.services.cache_service import semantic_cache

router = APIRouter(prefix="/api", tags=["metrics"])


@router.get("/metrics/summary")
def summary() -> dict:
    """Per-mode aggregates plus the computed before/after deltas."""
    data = metrics_service.comparison()
    data["cache"] = semantic_cache.stats()
    return data


@router.get("/metrics/interactions")
def interactions(
    limit: int = Query(50, ge=1, le=500),
    mode: str | None = Query(None, pattern="^(baseline|optimized)$"),
) -> dict:
    return {"interactions": metrics_service.recent_interactions(limit, mode)}


@router.get("/metrics/governance")
def governance() -> dict:
    return metrics_service.governance_summary()


@router.get("/metrics/audit")
def audit(
    limit: int = Query(100, ge=1, le=500),
    event_type: str | None = Query(None),
) -> dict:
    """Audit trail. Every row here is PII-masked at write time."""
    return {"audit": metrics_service.recent_audit(limit, event_type)}


@router.post("/metrics/reset")
def reset() -> dict:
    """Clear metrics, audit log, and cache — used to start a clean demo run."""
    metrics_service.reset()
    semantic_cache.clear()
    return {"status": "reset"}
