"""Read-only access to the synthetic order dataset.

This is the grounding layer: every factual claim the optimized pipeline makes
about an order has to come from here, and the output guardrail verifies it.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Any

from app.config import DATA_DIR

ORDER_ID_RE = re.compile(r"\bORD[-\s]?(\d{4,6})\b", re.IGNORECASE)


def _load(filename: str) -> list[dict]:
    path = DATA_DIR / filename
    if not path.exists():
        raise FileNotFoundError(
            f"{path} is missing. Run: python scripts/generate_data.py"
        )
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _dataset() -> dict[str, Any]:
    orders = _load("orders.json")
    customers = _load("customers.json")
    shipments = _load("shipments.json")
    products = _load("products.json")
    return {
        "orders": {o["order_id"]: o for o in orders},
        "customers": {c["customer_id"]: c for c in customers},
        "shipments": {s["order_id"]: s for s in shipments},
        "products": {p["product_id"]: p for p in products},
    }


def extract_order_ids(text: str) -> list[str]:
    """Pull every ORD-xxxxx mentioned, normalised and de-duplicated in order."""
    seen: list[str] = []
    for match in ORDER_ID_RE.finditer(text):
        candidate = f"ORD-{match.group(1)}"
        if candidate not in seen:
            seen.append(candidate)
    return seen


def order_exists(order_id: str) -> bool:
    return order_id.upper() in _dataset()["orders"]


def get_order(order_id: str) -> dict | None:
    return _dataset()["orders"].get(order_id.upper())


def get_shipment(order_id: str) -> dict | None:
    return _dataset()["shipments"].get(order_id.upper())


def get_customer(customer_id: str) -> dict | None:
    return _dataset()["customers"].get(customer_id)


def all_order_ids() -> list[str]:
    return list(_dataset()["orders"].keys())


def sample_order_ids(n: int = 5) -> list[str]:
    return all_order_ids()[:n]


def build_order_context(order_id: str, *, verbose: bool) -> dict | None:
    """Assemble the order payload the model is allowed to speak from.

    ``verbose=True`` reproduces the baseline's habit of dumping the entire
    record (including customer PII and every tracking scan) into the prompt.
    ``verbose=False`` sends only the fields that answer order questions, which
    is most of where the optimized pipeline's token saving comes from.
    """
    order = get_order(order_id)
    if order is None:
        return None

    shipment = get_shipment(order_id)
    customer = get_customer(order["customer_id"])

    if verbose:
        payload = dict(order)
        payload["customer"] = customer
        payload["shipment"] = shipment
        return payload

    latest_scan = None
    if shipment and shipment["events"]:
        last = shipment["events"][-1]
        latest_scan = f"{last['status']} at {last['location']} ({last['timestamp'][:10]})"

    compact: dict[str, Any] = {
        "order_id": order["order_id"],
        "status": order["status"],
        "order_date": order["order_date"],
        "estimated_delivery": order.get("estimated_delivery"),
        "shipping_method": order["shipping_method"],
        "total": order["total"],
        "items": [
            {"name": i["name"], "quantity": i["quantity"]} for i in order["items"]
        ],
    }
    if order.get("carrier"):
        compact["carrier"] = order["carrier"]
        compact["tracking_number"] = order["tracking_number"]
    if order.get("delivered_date"):
        compact["delivered_date"] = order["delivered_date"]
    if order.get("return_status"):
        compact["return_status"] = order["return_status"]
    if order.get("cancelled_date"):
        compact["cancelled_date"] = order["cancelled_date"]
    if latest_scan:
        compact["latest_tracking_scan"] = latest_scan
    if customer:
        compact["ship_to_city"] = (
            f"{customer['address']['city']}, {customer['address']['state']}"
        )
    return compact
