"""Pricing resolution.

Worth testing because the failure mode is silent: an unmatched alias falls back to
$3.00/$15.00 per Mtok, which is a plausible number, so a mispriced lab model would
quietly understate the cost-reduction headline instead of failing.
"""

from __future__ import annotations

import pytest

from app.services import llm_client


@pytest.mark.parametrize(
    "alias,rate,source",
    [
        ("gemini-2.5-flash", (0.30, 2.50), "exact"),
        ("gemini-2.5-pro", (1.25, 10.00), "exact"),
        ("claude-haiku-4-5", (1.00, 5.00), "exact"),
        ("genailab-maas-gpt-4o", (2.50, 10.00), "exact"),
        # The provider prefix litellm needs must not defeat the lookup.
        ("openai/gemini-2.5-pro", (1.25, 10.00), "exact"),
        ("GEMINI-2.5-FLASH", (0.30, 2.50), "exact"),
        # Version-suffixed and lab-prefixed variants fall through to substrings.
        ("gemini-2.5-flash-002", (0.30, 2.50), "prefix"),
        ("vertex-gemini-2.5-pro", (1.25, 10.00), "prefix"),
        ("maas-gpt-4o-mini-2024", (0.15, 0.60), "prefix"),
        ("claude-sonnet-4-9", (3.00, 15.00), "prefix"),
        ("some-unknown-lab-model", (3.00, 15.00), "default"),
    ],
)
def test_price_for_with_source(alias, rate, source):
    assert llm_client.price_for_with_source(alias) == (rate, source)


def test_flash_lite_beats_flash():
    """Longest-prefix ordering: 'gemini-2.5-flash' is a substring of the lite id."""
    lite, _ = llm_client.price_for_with_source("vertex-gemini-2.5-flash-lite-001")
    assert lite == (0.10, 0.40)


def test_price_for_keeps_its_signature():
    assert llm_client.price_for("gemini-2.5-flash") == (0.30, 2.50)


def test_estimate_cost_arithmetic():
    # 1M input at $0.30 + 1M output at $2.50
    assert llm_client.estimate_cost("gemini-2.5-flash", 1_000_000, 1_000_000) == 2.80
    assert llm_client.estimate_cost("gemini-2.5-flash", 0, 0) == 0.0


class TestResolveCost:
    def test_local_table_wins_over_the_gateway(self):
        """Deliberate: mixing cost sources across the two arms corrupts the diff."""
        cost, basis = llm_client.resolve_cost("gemini-2.5-flash", 1_000, 1_000, 99.0)
        assert basis == "local-exact"
        assert cost == pytest.approx(0.0028)

    def test_gateway_fills_in_for_an_unknown_alias(self):
        cost, basis = llm_client.resolve_cost("mystery-model", 1_000, 1_000, 0.0042)
        assert (cost, basis) == (0.0042, "gateway")

    def test_default_rate_is_last_resort(self):
        cost, basis = llm_client.resolve_cost("mystery-model", 1_000_000, 0, None)
        assert (cost, basis) == (3.00, "local-default")

    def test_prefix_match_is_reported(self):
        _, basis = llm_client.resolve_cost("gemini-2.5-flash-002", 10, 10, 5.0)
        assert basis == "local-prefix"
