"""Layer 1 — query complexity classification.

Decides which tier a query belongs to. The router turns that tier into a model
choice, which is where most of the cost reduction comes from. Deliberately
rule-based: it runs in microseconds and costs nothing, so the routing decision
never eats the saving it creates.

Tiers
-----
``simple``   direct order lookup            -> cheap model
``medium``   policy / FAQ question          -> capable model + RAG
``complex``  multi-order, disputes, refunds -> capable model + enriched context
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.services.order_service import extract_order_ids

# A question about a policy needs retrieval; a question about an order needs
# the database. Some queries need both, which is why classification returns
# flags rather than a single label.
_POLICY_TERMS = re.compile(
    r"\b(polic\w+|return|returns|refund|refunds|exchange|warrant\w+|guarantee|"
    r"ship(?:ping)?\s+(?:cost|time|speed|option|estimate)|how\s+long|how\s+much|"
    r"cancel|cancellation|price\s*match|discount|promo|coupon|gift\s*wrap|"
    r"payment|insur\w+|final\s+sale|restocking|terms|eligib\w+)\b",
    re.IGNORECASE,
)

_ORDER_TERMS = re.compile(
    r"\b(where|status|track\w*|arriv\w+|deliver\w*|eta|shipped|dispatch\w*|"
    r"my\s+order|order\s+status|when\s+will)\b",
    re.IGNORECASE,
)

_COMPLEX_TERMS = re.compile(
    r"\b(damaged?|broken|defect\w+|faulty|wrong\s+item|missing|never\s+(?:arrived|came)|"
    r"stolen|lost|dispute|chargeback|complain\w+|escalat\w+|manager|supervisor|"
    r"lawyer|legal|fraud|unauthorized|unauthorised|compensat\w+|angry|unacceptable)\b",
    re.IGNORECASE,
)

_ESCALATE_TERMS = re.compile(
    r"\b(speak\s+to\s+(?:a\s+)?(?:human|person|agent|representative)|"
    r"real\s+person|human\s+agent|stolen|fraud|chargeback|legal|lawyer|"
    r"unauthorized\s+(?:charge|payment)|unauthorised\s+(?:charge|payment))\b",
    re.IGNORECASE,
)

_MULTI_INTENT = re.compile(r"\b(and\s+also|also|both|as\s+well\s+as|plus)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Classification:
    tier: str                 # simple | medium | complex
    order_ids: list[str]
    needs_rag: bool
    needs_order_data: bool
    should_escalate: bool
    reason: str

    @property
    def is_cacheable(self) -> bool:
        """Complex cases are bespoke — caching them risks a wrong answer."""
        return self.tier in ("simple", "medium")


def classify(query: str) -> Classification:
    order_ids = extract_order_ids(query)
    has_policy = bool(_POLICY_TERMS.search(query))
    has_order_intent = bool(_ORDER_TERMS.search(query)) or bool(order_ids)
    has_complex = bool(_COMPLEX_TERMS.search(query))
    should_escalate = bool(_ESCALATE_TERMS.search(query))

    reasons: list[str] = []

    if has_complex or should_escalate:
        tier = "complex"
        reasons.append("issue/dispute language detected")
    elif len(order_ids) > 1:
        tier = "complex"
        reasons.append("multiple order numbers referenced")
    elif has_order_intent and has_policy and _MULTI_INTENT.search(query):
        tier = "complex"
        reasons.append("compound order + policy request")
    elif has_policy:
        tier = "medium"
        reasons.append("policy/FAQ question")
    elif has_order_intent:
        tier = "simple"
        reasons.append("direct order lookup")
    else:
        # Unknown intent gets the capable model. Cheapness is not worth a
        # wrong answer on a query we could not categorise.
        tier = "medium"
        reasons.append("unclassified — defaulting to capable model")

    needs_rag = has_policy or tier == "complex"
    needs_order_data = has_order_intent or tier == "complex"

    return Classification(
        tier=tier,
        order_ids=order_ids,
        needs_rag=needs_rag,
        needs_order_data=needs_order_data,
        should_escalate=should_escalate,
        reason="; ".join(reasons),
    )
