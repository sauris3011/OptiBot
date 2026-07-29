"""Calibrate the semantic-cache similarity threshold.

The threshold in config.py is not a guess — it comes from running this against
a labelled pair set. Re-run it after changing the embedding model, because the
right value is model-specific.

    python scripts/calibrate_cache.py

Bias: precision over recall. A false cache hit serves one customer another
customer's answer; a missed hit only costs one model call.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.config import settings  # noqa: E402
from app.services.embeddings import backend_name, cosine, embed_one  # noqa: E402

# Pairs that should share a cached answer.
SHOULD_HIT: list[tuple[str, str]] = [
    ("Where is my order ORD-10042?", "What's the status of order ORD-10042?"),
    ("Where is my order ORD-10042?", "Can you track ORD-10042 for me?"),
    ("What is your return policy?", "How long do I have to return an item?"),
    ("What is your return policy?", "Tell me about returns"),
    ("How much does express shipping cost?", "What's the price for express shipping?"),
    ("Has ORD-10042 been delivered?", "Was ORD-10042 delivered yet?"),
    ("Do you ship internationally?", "Can you ship to another country?"),
]

# Pairs that must NOT share an answer. Several are deliberately near-misses —
# same topic, different fact — because those are the dangerous collisions.
SHOULD_MISS: list[tuple[str, str]] = [
    ("Where is my order?", "What is your return policy?"),
    ("What is your return policy?", "What is the warranty on electronics?"),
    ("How much does express shipping cost?", "How long do refunds take?"),
    ("Where is my order ORD-10042?", "I want to cancel my order"),
    ("Do you ship internationally?", "Do you offer gift wrapping?"),
    ("What is your return policy?", "Is there a restocking fee on returns?"),
    ("How long does standard shipping take?", "How much does express shipping cost?"),
    ("Can I cancel my order after it ships?", "Can I change items in my order?"),
]

SWEEP = [0.70, 0.75, 0.78, 0.80, 0.82, 0.85, 0.88, 0.90, 0.93, 0.95]


def score(pairs: list[tuple[str, str]]) -> list[tuple[float, str, str]]:
    return [(cosine(embed_one(a), embed_one(b)), a, b) for a, b in pairs]


def main() -> int:
    print(f"embedding backend: {backend_name()}")
    print(f"configured threshold: {settings.cache_threshold}\n")

    hits = score(SHOULD_HIT)
    misses = score(SHOULD_MISS)

    print("SHOULD HIT (want above threshold):")
    for s, a, b in sorted(hits):
        flag = "ok " if s >= settings.cache_threshold else "MISS"
        print(f"  {flag} {s:.3f}  {a[:38]!r} ~ {b[:38]!r}")

    print("\nSHOULD MISS (want below threshold):")
    for s, a, b in sorted(misses, reverse=True):
        flag = "FALSE HIT" if s >= settings.cache_threshold else "ok       "
        print(f"  {flag} {s:.3f}  {a[:38]!r} ~ {b[:38]!r}")

    lowest_hit = min(s for s, _, _ in hits)
    highest_miss = max(s for s, _, _ in misses)
    print(f"\nlowest  should-hit : {lowest_hit:.3f}")
    print(f"highest should-miss: {highest_miss:.3f}")
    print(f"separation margin  : {lowest_hit - highest_miss:+.3f}")
    if lowest_hit <= highest_miss:
        print("  (classes overlap — no threshold separates them perfectly;\n"
              "   pick the highest value with zero false hits)")

    print(f"\n{'threshold':>10} {'recall':>10} {'false hits':>12}   verdict")
    best: float | None = None
    for thr in SWEEP:
        tp = sum(1 for s, _, _ in hits if s >= thr)
        fp = sum(1 for s, _, _ in misses if s >= thr)
        verdict = "unsafe" if fp else "safe"
        if fp == 0 and best is None:
            best = thr
        marker = " <- configured" if abs(thr - settings.cache_threshold) < 1e-9 else ""
        print(f"{thr:>10.2f} {tp:>4}/{len(hits):<5} {fp:>5}/{len(misses):<6} {verdict}{marker}")

    if best is not None:
        print(f"\nlowest threshold with zero false hits: {best:.2f}")
        if settings.cache_threshold < best:
            print(f"WARNING: configured {settings.cache_threshold} is below that — "
                  "the cache may serve wrong answers.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
