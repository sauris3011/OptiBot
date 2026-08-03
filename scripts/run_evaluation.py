"""Run the golden dataset through both pipelines and report the before/after.

Needs a reachable LiteLLM gateway: set LITELLM_BASE_URL and LITELLM_API_KEY in
backend/.env. Model aliases resolve exactly as the app resolves them, so if you
picked models in the settings panel this measures those.

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

from app import llm_settings  # noqa: E402
from app.services import chat_service, evaluation, llm_client, metrics_service  # noqa: E402
from app.services.cache_service import semantic_cache  # noqa: E402
from app.services.embeddings import backend_name  # noqa: E402

REPORTS = ROOT / "reports"

# load_cases/grade also back the in-app "Play" button — see app/services/evaluation.py.
load_cases = evaluation.load_cases
grade = evaluation.grade
aggregate = evaluation.aggregate


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

    # Fail fast. Without a key every case returns an error row after a full
    # timeout, and the report looks like a measurement rather than a
    # misconfiguration.
    if not llm_settings.has_api_key():
        print(
            f"{llm_settings.API_KEY_ENV_VAR} is not set. Add it to backend/.env "
            "(the settings panel applies keys in-process only, which this script "
            "cannot see).",
            file=sys.stderr,
        )
        return 2

    if args.reset:
        metrics_service.init_db()
        metrics_service.reset()
        semantic_cache.clear()
        print("cleared previous metrics and cache")

    metrics_service.init_db()
    cases = load_cases(args.limit)
    print(f"embedding backend : {backend_name()}")
    print(f"litellm gateway   : {llm_settings.base_url()}")
    # Echo the resolved aliases and price sources so the report is never ambiguous
    # about which models produced it, or whether their costs were real.
    for slot, alias in llm_settings.all_models().items():
        (price_in, price_out), source = llm_client.price_for_with_source(alias)
        note = "  <-- unpriced, cost figures are a fallback" if source == "default" else ""
        print(
            f"model {slot:<9} : {alias} "
            f"(${price_in:.2f}/${price_out:.2f} per Mtok, {source}){note}"
        )
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
