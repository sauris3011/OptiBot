"""Run the golden dataset through both pipelines and report the before/after.

Usage (from the repo root, with backend/.env configured):

    python scripts/run_evaluation.py                 # both modes, full set
    python scripts/run_evaluation.py --mode optimized
    python scripts/run_evaluation.py --limit 6 --reset

Writes reports/evaluation.json and prints the comparison table that backs the
metrics claims in the PRD.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.config import DATA_DIR  # noqa: E402
from app.services import chat_service, metrics_service, order_service  # noqa: E402
from app.services.cache_service import semantic_cache  # noqa: E402
from app.services.embeddings import backend_name  # noqa: E402

REPORTS = ROOT / "reports"


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


def load_cases(limit: int | None) -> list[dict]:
    raw = json.loads((DATA_DIR / "golden_queries.json").read_text(encoding="utf-8"))
    mapping = pick_orders()
    for case in raw:
        for placeholder, order_id in mapping.items():
            case["query"] = case["query"].replace(placeholder, order_id)
    return raw[:limit] if limit else raw


def grade(case: dict, result) -> dict:
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


def run_mode(cases: list[dict], mode: str, delay: float) -> list[dict]:
    rows: list[dict] = []
    print(f"\n{'=' * 74}\n  {mode.upper()}  ({len(cases)} cases)\n{'=' * 74}")

    for i, case in enumerate(cases, 1):
        started = time.perf_counter()
        result = chat_service.handle(case["query"], f"eval-{mode}", mode)
        elapsed = int((time.perf_counter() - started) * 1000)

        if result.error:
            print(f"  {i:>2}. {case['id']:<16} ERROR  {result.error}")
            rows.append({"id": case["id"], "error": result.error, "passed": False})
            continue

        verdict = grade(case, result)
        mark = "PASS" if verdict["passed"] else "FAIL"
        tier = result.metrics.tier or "-"
        cached = " cache" if result.metrics.cache_hit else ""
        print(
            f"  {i:>2}. {case['id']:<16} {mark}  "
            f"{result.metrics.total_tokens:>5} tok  "
            f"{elapsed:>5}ms  ${result.metrics.cost_usd:.5f}  "
            f"[{tier}{cached}]"
        )
        if verdict["reason"]:
            print(f"      -> {verdict['reason']}")

        rows.append(
            {
                "id": case["id"],
                "query": case["query"],
                "difficulty": case.get("difficulty"),
                "mode": mode,
                "passed": verdict["passed"],
                "checks": verdict["checks"],
                "reason": verdict["reason"],
                "response": result.response,
                "model": result.metrics.model,
                "tier": result.metrics.tier,
                "tokens": result.metrics.total_tokens,
                "latency_ms": result.metrics.latency_ms,
                "cost_usd": result.metrics.cost_usd,
                "cache_hit": result.metrics.cache_hit,
                "rag_used": result.metrics.rag_used,
                "sources": result.sources,
                "confidence": result.metrics.confidence,
                "blocked": result.metrics.blocked,
                "guardrail_events": result.metrics.guardrail_events,
                "pii_detected": result.metrics.pii_detected,
                "pii_masked": result.metrics.pii_masked,
            }
        )
        if delay:
            time.sleep(delay)
    return rows


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


def delta(base: float, opt: float) -> str:
    if base == 0:
        return "—"
    change = (base - opt) / base * 100
    return f"{change:+.1f}%".replace("+", "-", 1) if change > 0 else f"+{-change:.1f}%"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["baseline", "optimized", "both"], default="both")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--delay", type=float, default=0.4,
                        help="seconds between calls, to stay under rate limits")
    parser.add_argument("--reset", action="store_true",
                        help="clear stored metrics before running")
    args = parser.parse_args()

    if args.reset:
        metrics_service.init_db()
        metrics_service.reset()
        semantic_cache.clear()
        print("cleared previous metrics and cache")

    metrics_service.init_db()
    cases = load_cases(args.limit)
    print(f"embedding backend : {backend_name()}")
    print(f"golden cases      : {len(cases)}")

    results: dict[str, list[dict]] = {}
    modes = ["baseline", "optimized"] if args.mode == "both" else [args.mode]
    for mode in modes:
        # A fresh cache per mode keeps the hit rate honest: it measures repeat
        # questions inside this run, not leftovers from a previous one.
        semantic_cache.clear()
        results[mode] = run_mode(cases, mode, args.delay)

    summary = {m: aggregate(rows) for m, rows in results.items()}

    print(f"\n{'=' * 74}\n  SUMMARY\n{'=' * 74}")
    if len(summary) == 2 and summary["baseline"] and summary["optimized"]:
        b, o = summary["baseline"], summary["optimized"]
        rows = [
            ("Accuracy", f"{b['accuracy_pct']}%", f"{o['accuracy_pct']}%",
             f"{o['accuracy_pct'] - b['accuracy_pct']:+.1f}pp"),
            ("Avg tokens", b["avg_tokens"], o["avg_tokens"],
             delta(b["avg_tokens"], o["avg_tokens"])),
            ("Avg latency (ms)", b["avg_latency_ms"], o["avg_latency_ms"],
             delta(b["avg_latency_ms"], o["avg_latency_ms"])),
            ("Avg cost ($)", f"{b['avg_cost_usd']:.5f}", f"{o['avg_cost_usd']:.5f}",
             delta(b["avg_cost_usd"], o["avg_cost_usd"])),
            ("Cost / 10k queries ($)", f"{b['avg_cost_usd'] * 10000:.2f}",
             f"{o['avg_cost_usd'] * 10000:.2f}",
             delta(b["avg_cost_usd"], o["avg_cost_usd"])),
            ("Cache hits", b["cache_hits"], o["cache_hits"], "—"),
            ("RAG-grounded", b["rag_used"], o["rag_used"], "—"),
            ("Injections blocked", b["blocked"], o["blocked"], "—"),
            ("Unmasked PII stored", b["pii_unmasked"], o["pii_unmasked"], "—"),
        ]
        print(f"  {'Metric':<24}{'Baseline':>14}{'Optimized':>14}{'Change':>12}")
        print(f"  {'-' * 62}")
        for name, bv, ov, dv in rows:
            print(f"  {name:<24}{str(bv):>14}{str(ov):>14}{str(dv):>12}")
    else:
        for mode, agg in summary.items():
            print(f"  {mode}: {agg}")

    REPORTS.mkdir(exist_ok=True)
    out = REPORTS / "evaluation.json"
    out.write_text(
        json.dumps(
            {
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "embedding_backend": backend_name(),
                "summary": summary,
                "results": results,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nwrote {out.relative_to(ROOT)}")

    failures = [
        r for rows in results.values() for r in rows
        if not r.get("passed") and "error" not in r
    ]
    if failures:
        print(f"\n{len(failures)} case(s) failed — see the report for detail.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
