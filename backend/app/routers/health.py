from __future__ import annotations

import time

from fastapi import APIRouter

from app import llm_settings
from app.models.schemas import HealthResponse
from app.services import gateway, order_service, rag_service
from app.services.embeddings import backend_name

router = APIRouter(prefix="/api", tags=["health"])

# Reachability is memoised because the settings panel and a human with curl can
# both ask for it in quick succession, and each probe is a real round trip.
_PROBE_TTL_S = 30.0
_probe_cache: tuple[float, bool] | None = None


def _reachable_memoised() -> bool:
    global _probe_cache
    now = time.monotonic()
    if _probe_cache and now - _probe_cache[0] < _PROBE_TTL_S:
        return _probe_cache[1]
    reachable = gateway.is_reachable()
    _probe_cache = (now, reachable)
    return reachable


@router.get("/health", response_model=HealthResponse)
def health(probe: bool = False) -> HealthResponse:
    """Boot check.

    Surfaces the embedding backend explicitly so a demo never runs on the
    lexical fallback without anyone realising the semantic layers are degraded.

    ``probe=true`` additionally checks that the LiteLLM gateway answers. It is
    opt-in because the nav bar calls this endpoint on every page mount, and a
    gateway round trip there would make "API offline" mean "the gateway is slow".
    """
    return HealthResponse(
        status="ok",
        api_key_configured=llm_settings.has_api_key(),
        api_key_source=llm_settings.api_key_source(),
        litellm_base_url=llm_settings.base_url(),
        litellm_reachable=_reachable_memoised() if probe else None,
        embedding_backend=backend_name(),
        rag_index=rag_service.index_stats(),
        models=llm_settings.all_models(),
    )


@router.get("/sample-orders")
def sample_orders(limit: int = 6) -> dict:
    """A few real order IDs so the demo UI can offer working example queries."""
    orders = []
    for order_id in order_service.all_order_ids():
        order = order_service.get_order(order_id)
        if order and order["status"] in ("In Transit", "Delivered", "Processing"):
            orders.append({"order_id": order_id, "status": order["status"]})
        if len(orders) >= limit:
            break
    return {"orders": orders}
