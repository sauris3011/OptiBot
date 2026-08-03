"""The golden-query dataset: loading, order binding, and grading.

Shared by scripts/run_evaluation.py and services/simulation_service.py — a bug
here silently changes what "correct" means for both the CLI and the Play button.
"""

from __future__ import annotations

import pytest

from app.models.schemas import ChatMetrics, ChatResponse
from app.services import evaluation


def _response(
    *, response_text: str = "the answer", sources: list[str] | None = None, **metrics_kwargs
) -> ChatResponse:
    defaults = dict(mode="optimized", blocked=False, rag_used=False, pii_masked=False)
    defaults.update(metrics_kwargs)
    return ChatResponse(
        response=response_text,
        metrics=ChatMetrics(**defaults),
        sources=sources or [],
    )


class TestLoadCases:
    def test_full_set_has_23_cases_and_no_placeholders_left(self):
        cases = evaluation.load_cases()
        assert len(cases) == 23
        assert all("{ORDER_" not in c["query"] for c in cases)

    def test_quick_demo_is_the_curated_subset_in_order(self):
        cases = evaluation.load_cases(only_ids=evaluation.QUICK_DEMO_CASE_IDS)
        assert [c["id"] for c in cases] == list(evaluation.QUICK_DEMO_CASE_IDS)
        assert len(cases) == 8

    def test_limit_truncates_in_file_order(self):
        cases = evaluation.load_cases(limit=3)
        assert len(cases) == 3
        assert cases[0]["id"] == "simple-01"

    def test_unknown_id_raises(self):
        with pytest.raises(RuntimeError):
            evaluation.load_cases(only_ids=("not-a-real-case",))

    def test_quick_demo_covers_every_optimization_layer(self):
        """The curated set exists specifically to avoid an uninteresting demo —
        assert it still touches routing, caching, RAG, hallucination, injection
        and PII, not just direct order lookups."""
        cases = {c["id"]: c for c in evaluation.load_cases(only_ids=evaluation.QUICK_DEMO_CASE_IDS)}
        assert cases["simple-02"]["query"] != cases["simple-01"]["query"]  # cache: rephrased, same intent
        assert cases["policy-01"].get("expects_rag")
        assert cases["injection-01"].get("expect_blocked")
        assert cases["pii-01"].get("expect_pii")
        assert "ORD-99999" in cases["hallucination-01"]["query"]


class TestGrade:
    def test_injection_case_passes_only_when_blocked(self):
        case = {"expect_blocked": True}
        assert evaluation.grade(case, _response(blocked=True))["passed"] is True
        assert evaluation.grade(case, _response(blocked=False))["passed"] is False

    def test_must_contain_any(self):
        case = {"must_contain_any": ["In Transit", "in transit"]}
        hit = _response(response_text="Your order is In Transit right now.")
        miss = _response(response_text="Your order was delivered.")
        assert evaluation.grade(case, hit)["passed"] is True
        result = evaluation.grade(case, miss)
        assert result["passed"] is False
        assert "missing any of" in result["reason"]

    def test_forbidden_content_fails_even_if_contains_expected(self):
        case = {"must_contain_any": ["policy"], "forbidden": ["30-day"]}
        result = evaluation.grade(
            case, _response(response_text="Our policy allows a 30-day return.")
        )
        assert result["passed"] is False
        assert "hallucinated" in result["reason"]

    def test_rag_expectation_is_reported_but_not_a_hard_fail(self):
        """expects_rag affects the reason string; it does not flip passed on its
        own — content correctness is still the only hard gate."""
        case = {"must_contain_any": ["return"], "expects_rag": True}
        result = evaluation.grade(
            case, _response(response_text="You can return it.", rag_used=False)
        )
        assert result["passed"] is True
        assert "expected retrieval but none ran" in result["reason"]

    def test_pii_expectation_reported_for_optimized_only(self):
        case = {"expect_pii": True}
        opt = evaluation.grade(case, _response(mode="optimized", pii_masked=False))
        base = evaluation.grade(case, _response(mode="baseline", pii_masked=False))
        assert "PII was not masked" in opt["reason"]
        assert base["reason"] == ""  # baseline has no masking contract to hold it to


class TestAggregate:
    def test_empty_rows(self):
        assert evaluation.aggregate([]) == {}

    def test_basic_arithmetic(self):
        rows = [
            {"passed": True, "tokens": 10, "latency_ms": 100, "cost_usd": 0.01,
             "cache_hit": True, "rag_used": False, "blocked": False,
             "pii_detected": [], "pii_masked": False, "confidence": 0.9},
            {"passed": False, "tokens": 20, "latency_ms": 200, "cost_usd": 0.02,
             "cache_hit": False, "rag_used": True, "blocked": False,
             "pii_detected": ["email"], "pii_masked": False, "confidence": 0.5},
        ]
        agg = evaluation.aggregate(rows)
        assert agg["cases"] == 2
        assert agg["passed"] == 1
        assert agg["accuracy_pct"] == 50.0
        assert agg["avg_tokens"] == 15.0
        assert agg["cache_hits"] == 1
        assert agg["pii_unmasked"] == 1

    def test_error_rows_are_excluded_from_scoring(self):
        rows = [{"error": "boom", "passed": False}]
        assert evaluation.aggregate(rows) == {}
