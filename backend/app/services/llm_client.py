"""LiteLLM wrapper — Layer 1's execution half, plus token/cost accounting.

Prices are declared locally rather than read from LiteLLM's cost map. The map
lags new model releases, and a demo whose headline metric is cost reduction
cannot have its cost numbers silently fall back to zero for an unrecognised
model ID. Rates are Anthropic list prices in USD per million tokens.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass

import litellm

from app.config import settings

litellm.drop_params = True
litellm.suppress_debug_info = True

# USD per 1M tokens (input, output).
PRICING: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
}

_DEFAULT_PRICE = (3.00, 15.00)


class LLMError(RuntimeError):
    pass


@dataclass
class LLMResult:
    text: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    cost_usd: float
    stop_reason: str | None = None


def price_for(model: str) -> tuple[float, float]:
    key = model.split("/")[-1]
    return PRICING.get(key, _DEFAULT_PRICE)


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    in_rate, out_rate = price_for(model)
    return round(
        (input_tokens / 1_000_000) * in_rate + (output_tokens / 1_000_000) * out_rate,
        8,
    )


def select_model(mode: str, tier: str) -> str:
    """The routing decision itself.

    Baseline ignores the tier entirely — one expensive model for every query,
    which is the behaviour being measured against.
    """
    if mode == "baseline":
        return settings.model_baseline
    return settings.model_simple if tier == "simple" else settings.model_complex


def complete(
    *,
    model: str,
    system: str,
    messages: list[dict],
    max_tokens: int,
) -> LLMResult:
    if not settings.has_api_key:
        raise LLMError(
            "ANTHROPIC_API_KEY is not set. Copy backend/.env.example to "
            "backend/.env and add your key."
        )

    payload = [{"role": "system", "content": system}, *messages]
    started = time.perf_counter()
    try:
        response = litellm.completion(
            model=f"anthropic/{model}",
            messages=payload,
            max_tokens=max_tokens,
            api_key=settings.api_key,
        )
    except Exception as exc:  # noqa: BLE001 - surfaced to the caller as one type
        raise LLMError(f"{model}: {exc}") from exc

    latency_ms = int((time.perf_counter() - started) * 1000)

    choice = response.choices[0]
    text = (choice.message.content or "").strip()
    usage = response.usage
    input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)

    return LLMResult(
        text=text,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=latency_ms,
        cost_usd=estimate_cost(model, input_tokens, output_tokens),
        stop_reason=getattr(choice, "finish_reason", None),
    )


_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_structured(text: str) -> tuple[str, float, list[str]]:
    """Extract (response, confidence, sources) from the optimized model output.

    The optimized prompt asks for JSON. Models occasionally wrap it in prose or
    a code fence, so parse defensively and degrade to treating the whole reply
    as the response rather than failing the request.
    """
    if not text:
        return "", 0.0, []

    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*|\s*```$", "", candidate).strip()

    match = _JSON_BLOCK_RE.search(candidate)
    if match:
        try:
            data = json.loads(match.group(0))
            if isinstance(data, dict) and "response" in data:
                confidence = data.get("confidence", 0.8)
                try:
                    confidence = float(confidence)
                except (TypeError, ValueError):
                    confidence = 0.8
                sources = data.get("sources") or []
                if not isinstance(sources, list):
                    sources = [str(sources)]
                return (
                    str(data["response"]).strip(),
                    max(0.0, min(1.0, confidence)),
                    [str(s) for s in sources],
                )
        except json.JSONDecodeError:
            pass

    # Not JSON — the model answered in prose. Usable, but flag it as lower
    # confidence since the output contract was not honoured.
    return candidate, 0.6, []
