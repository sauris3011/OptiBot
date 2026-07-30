"""LiteLLM gateway configuration — what the gear icon in the UI talks to.

Conventions worth knowing before editing:

* Gateway and configuration failures return **HTTP 200 with ``status: "error"``**
  in the body, matching ``routers/chat.py``. Only a malformed request gets a 422.
  The frontend therefore has to branch on the body, not just on ``res.ok``.
* The raw API key is never logged, echoed, or written to disk. Only a masked form
  ever leaves this module.
* This endpoint accepts a secret over an unauthenticated local API. It is a
  local demo/lab tool — keep the backend bound to loopback.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter

from app import llm_settings
from app.config import settings
from app.models.schemas import (
    ApiKeyStatus,
    LlmConfig,
    LlmConfigResponse,
    LlmConfigSaveResponse,
    LlmConfigUpdate,
    LlmModelsResponse,
    LlmTestRequest,
    LlmTestResponse,
    LlmUsage,
    SlotConfig,
    TlsStatus,
)
from app.services import gateway, llm_client

log = logging.getLogger("optibot.llm_config")

router = APIRouter(prefix="/api", tags=["llm-config"])


def _slot_configs() -> list[SlotConfig]:
    slots: list[SlotConfig] = []
    for slot in llm_settings.SLOTS:
        alias = llm_settings.model_for(slot)
        (price_in, price_out), price_source = llm_client.price_for_with_source(alias)
        slots.append(
            SlotConfig(
                slot=slot,
                label=llm_settings.SLOT_LABELS[slot],
                description=llm_settings.SLOT_DESCRIPTIONS[slot],
                model=alias,
                source=llm_settings.model_source(slot),
                price_in_per_mtok=price_in,
                price_out_per_mtok=price_out,
                price_source=price_source,
            )
        )
    return slots


def _current_config() -> LlmConfig:
    return LlmConfig(
        base_url=llm_settings.base_url(),
        chat_url=llm_settings.chat_url(),
        models_url=llm_settings.models_url(),
        slots=_slot_configs(),
        api_key_status=ApiKeyStatus(
            env_var=llm_settings.API_KEY_ENV_VAR,
            is_set=llm_settings.has_api_key(),
            source=llm_settings.api_key_source(),
            masked_value=llm_settings.masked_api_key(),
        ),
        tls=TlsStatus(**gateway.tls_status()),
        request_timeout_s=settings.litellm_timeout_s,
        max_attempts=settings.litellm_max_attempts,
        runtime_config_path=str(llm_settings.runtime_path()),
        persisted=llm_settings.is_persisted(),
    )


@router.get("/llm-config", response_model=LlmConfigResponse)
def get_llm_config() -> LlmConfigResponse:
    """Full gateway state plus the live model list from the gateway."""
    models, source, warning = gateway.resolve_models()
    return LlmConfigResponse(
        models=models,
        models_source=source,
        warning=warning,
        config=_current_config(),
    )


@router.get("/llm-config/models", response_model=LlmModelsResponse)
def get_llm_models(
    base_url: str | None = None, api_key: str | None = None
) -> LlmModelsResponse:
    """Just the model list, so "Refresh models" can spin only that control."""
    models, source, warning = gateway.resolve_models(base_url=base_url, api_key=api_key)
    return LlmModelsResponse(models=models, models_source=source, warning=warning)


@router.post("/llm-config", response_model=LlmConfigSaveResponse)
def update_llm_config(payload: LlmConfigUpdate) -> LlmConfigSaveResponse:
    """Persist model slots and the base URL; apply the key to this process only."""
    if not payload.models and not payload.base_url and not payload.api_key:
        # Without this an empty POST reports success and changes nothing.
        return LlmConfigSaveResponse(
            status="error",
            message="Nothing to save — send at least one of models, base_url or api_key.",
            config=_current_config(),
        )

    try:
        changed = llm_settings.apply(
            models=dict(payload.models) if payload.models else None,
            base_url=payload.base_url,
            api_key=payload.api_key,
        )
    except llm_settings.ConfigError as exc:
        return LlmConfigSaveResponse(status="error", message=str(exc), config=_current_config())

    parts: list[str] = []
    if changed["models"] or changed["base_url"]:
        parts.append(f"Gateway settings saved to {llm_settings.runtime_path()}.")
    if changed["api_key"]:
        parts.append(
            f"The API key is applied in-process only — add "
            f"{llm_settings.API_KEY_ENV_VAR} to backend/.env to keep it across restarts."
        )
    if not parts:
        parts.append("No changes — the submitted values already match the active configuration.")

    log.info(
        "llm-config updated: models=%s base_url=%s api_key=%s",
        changed["models"],
        changed["base_url"],
        changed["api_key"],  # a bool, never the value
    )
    return LlmConfigSaveResponse(status="success", message=" ".join(parts), config=_current_config())


@router.post("/llm-config/test", response_model=LlmTestResponse)
def test_llm_config(payload: LlmTestRequest) -> LlmTestResponse:
    """One tiny round trip through the gateway.

    ``payload.api_key`` / ``payload.base_url`` are honoured but neither applied
    nor persisted, so the panel can validate a pending value before saving it.
    """
    model = (payload.model_id or "").strip()
    if not model:
        model = llm_settings.model_for(payload.slot or "simple")

    base = (
        llm_settings.normalize_base_url(payload.base_url)
        if payload.base_url and payload.base_url.strip()
        else llm_settings.base_url()
    )
    effective_key = (payload.api_key or "").strip() or llm_settings.api_key()
    tls = gateway.tls_status()
    config_used = {
        "Slot": payload.slot or "(ad hoc)",
        "Model": model,
        "Gateway": base,
        llm_settings.API_KEY_ENV_VAR: llm_settings.mask_secret(effective_key) or "(not set)",
        "TLS verify": "on" if tls["verify"] else "off",
    }

    messages = [{"role": "user", "content": "Reply with exactly: OK"}]
    call = dict(
        model=model,
        messages=messages,
        max_tokens=16,
        timeout_s=settings.litellm_test_timeout_s,
        api_key_override=payload.api_key,
        base_url_override=payload.base_url,
        diagnose=True,
    )

    try:
        try:
            result = gateway.chat(temperature=0, **call)
        except gateway.GatewayError as exc:
            # drop_params only drops parameters litellm *knows* a model rejects,
            # which it cannot know for an arbitrary gateway alias. Some
            # reasoning-tuned aliases 400 on any temperature at all.
            if "temperature" not in str(exc).lower():
                raise
            log.info("retrying the %s connection test without temperature", model)
            result = gateway.chat(**call)
    except gateway.GatewayError as exc:
        return LlmTestResponse(
            status="error",
            message=str(exc),
            slot=payload.slot,
            model=model,
            config_used=config_used,
        )

    cost_usd, cost_basis = llm_client.resolve_cost(
        model, result.input_tokens, result.output_tokens, result.reported_cost_usd
    )
    return LlmTestResponse(
        status="success",
        message=f"Connection successful. '{model}' is reachable through the LiteLLM gateway.",
        slot=payload.slot,
        model=model,
        latency_ms=result.latency_ms,
        response=result.text[:100],
        usage=LlmUsage(
            prompt_tokens=result.input_tokens,
            completion_tokens=result.output_tokens,
            total_tokens=result.input_tokens + result.output_tokens,
            cost_usd=cost_usd,
            cost_basis=cost_basis,
        ),
        config_used=config_used,
    )
