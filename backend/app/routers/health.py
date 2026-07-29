from __future__ import annotations

from fastapi import APIRouter

from app.config import settings
from app.models.schemas import HealthResponse
from app.services import order_service, rag_service
from app.services.embeddings import backend_name

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Boot check.

    Surfaces the embedding backend explicitly so a demo never runs on the
    lexical fallback without anyone realising the semantic layers are degraded.
    """
    return HealthResponse(
        status="ok",
        api_key_configured=settings.has_api_key,
        embedding_backend=backend_name(),
        rag_index=rag_service.index_stats(),
        models={
            "baseline": settings.model_baseline,
            "simple": settings.model_simple,
            "complex": settings.model_complex,
        },
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
