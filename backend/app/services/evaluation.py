"""The golden-query dataset — loading, order-placeholder binding, and grading.

Shared by two callers that must never disagree about what "correct" means:
``scripts/run_evaluation.py`` (the terminal harness) and
``services/simulation_service.py`` (the in-app Play button). Previously this
logic lived only in the script; splitting it out means a case that passes here
passes the same way everywhere, instead of two grading implementations quietly
drifting apart.
"""

from __future__ import annotations

import json

from app.config import DATA_DIR
from app.models.schemas import ChatResponse
from app.services import order_service

# A hand-picked subset for the in-app "Quick demo" run. Chosen to touch every
# optimization layer in as few calls as possible, rather than just taking the
# first N cases (which would be four order lookups and four policy questions —
# no caching, no hallucination check, no governance).
#
#   simple-01/02  direct lookup, then the same question rephrased -> cache hit
#   policy-01/03  RAG-grounded policy answers, two different documents
#   complex-01    multi-field context, complex-tier routing
#   hallucination-01  an order ID that does not exist -> tests fabrication
#   injection-01  prompt injection -> input guardrail
#   pii-01        PII inside the query itself -> masking guardrail
QUICK_DEMO_CASE_IDS: tuple[str, ...] = (
    "simple-01",
    "simple-02",
    "policy-01",
    "policy-03",
    "complex-01",
    "hallucination-01",
    "injection-01",
    "pii-01",
)


def pick_orders() -> dict[str, str]:
    """Bind the golden-set placeholders to real orders in the dataset."""
    wanted = {
        "{ORDER_IN_TRANSIT}": "In Transit",
        "{ORDER_DELIVERED}": "Delivered",
        "{ORDER_PROCESSING}": "Processing",
    }
    chosen: dict[str, str] = {}
    for placeholder, status in wanted.items():
        for order_id in order_service.all_order_ids():
            order = order_service.get_order(order_id)
            if order and order["status"] == status:
                chosen[placeholder] = order_id
                break
        else:
            raise RuntimeError(f"No order with status {status} in the dataset")
    return chosen


def load_cases(
    *, limit: int | None = None, only_ids: tuple[str, ...] | None = None
) -> list[dict]:
    """The golden dataset with order placeholders resolved.

    ``only_ids`` selects a curated subset, in the given order — used for the
    Quick demo. ``limit`` truncates the full set in file order — used by the
    CLI script. Passing both is not supported; ``only_ids`` wins.
    """
    raw = json.loads((DATA_DIR / "golden_queries.json").read_text(encoding="utf-8"))
    mapping = pick_orders()
    for case in raw:
        for placeholder, order_id in mapping.items():
            case["query"] = case["query"].replace(placeholder, order_id)

    if only_ids:
        by_id = {case["id"]: case for case in raw}
        missing = [cid for cid in only_ids if cid not in by_id]
        if missing:
            raise RuntimeError(f"Unknown golden-query id(s): {missing}")
        return [by_id[cid] for cid in only_ids]

    return raw[:limit] if limit else raw


def grade(case: dict, result: ChatResponse) -> dict:
    """Score one response. Every check is derived from the golden entry."""
    text = (result.response or "").lower()
    checks: dict[str, bool | None] = {}

    if case.get("expect_blocked"):
        checks["blocked_as_expected"] = result.metrics.blocked
        # A blocked request is correct by definition; skip content checks.
        return {
            "passed": bool(result.metrics.blocked),
            "checks": checks,
            "reason": "" if result.metrics.blocked else "injection was not blocked",
        }

    reasons: list[str] = []

    if "must_contain_any" in case:
        hit = any(t.lower() in text for t in case["must_contain_any"])
        checks["contains_expected"] = hit
        if not hit:
            reasons.append(f"missing any of {case['must_contain_any']}")

    if "forbidden" in case:
        bad = [t for t in case["forbidden"] if t.lower() in text]
        checks["no_forbidden_content"] = not bad
        if bad:
            reasons.append(f"contains hallucinated {bad}")

    if case.get("expects_rag"):
        checks["used_retrieval"] = result.metrics.rag_used
        if not result.metrics.rag_used and result.metrics.mode == "optimized":
            reasons.append("expected retrieval but none ran")

    if case.get("expected_source"):
        got = set(result.sources) | set(result.metrics.rag_sources)
        checks["cited_expected_source"] = case["expected_source"] in got
        if case["expected_source"] not in got and result.metrics.mode == "optimized":
            reasons.append(f"did not cite {case['expected_source']}")

    if case.get("expect_pii"):
        checks["pii_masked"] = result.metrics.pii_masked
        if not result.metrics.pii_masked and result.metrics.mode == "optimized":
            reasons.append("PII was not masked before storage")

    # Content correctness is the pass/fail signal; retrieval and citation are
    # reported but do not fail a case on their own.
    hard = [
        checks.get("contains_expected", True),
        checks.get("no_forbidden_content", True),
    ]
    return {
        "passed": all(bool(c) for c in hard),
        "checks": checks,
        "reason": "; ".join(reasons),
    }


def aggregate(rows: list[dict]) -> dict:
    scored = [r for r in rows if "error" not in r]
    if not scored:
        return {}
    n = len(scored)
    graded = [r for r in scored if not r.get("blocked")]
    return {
        "cases": n,
        "passed": sum(1 for r in scored if r["passed"]),
        "accuracy_pct": round(sum(1 for r in scored if r["passed"]) / n * 100, 1),
        "avg_tokens": round(sum(r["tokens"] for r in scored) / n, 1),
        "avg_latency_ms": round(sum(r["latency_ms"] for r in scored) / n, 1),
        "avg_cost_usd": round(sum(r["cost_usd"] for r in scored) / n, 6),
        "total_cost_usd": round(sum(r["cost_usd"] for r in scored), 5),
        "cache_hits": sum(1 for r in scored if r["cache_hit"]),
        "rag_used": sum(1 for r in scored if r["rag_used"]),
        "blocked": sum(1 for r in scored if r["blocked"]),
        "pii_unmasked": sum(
            1 for r in scored if r["pii_detected"] and not r["pii_masked"]
        ),
        "avg_confidence": round(
            sum(r["confidence"] for r in graded) / len(graded), 3
        ) if graded else 0.0,
    }
