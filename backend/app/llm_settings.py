"""Mutable runtime configuration for the LiteLLM gateway.

``app.config`` is frozen at import, which is right for everything the operator
sets once in ``backend/.env``. But the gateway key, base URL and per-slot model
aliases are picked in the UI while the app is running, so they need a layer that
can change. This is it.

Precedence, per value: **persisted runtime file → environment → built-in
fallback**. The persisted file only ever holds the base URL and the three model
aliases; the API key is applied in-process and is *never* written to disk, so a
restart deliberately forgets it (put it in ``backend/.env`` to keep it).

Imports nothing heavier than the standard library plus ``app.config`` — the root
``scripts/`` import this, and ``services/gateway.py`` imports it, so it must stay
free of litellm/httpx to avoid an import cycle and a slow CLI startup.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from app.config import settings

Slot = Literal["baseline", "simple", "complex"]
SLOTS: tuple[Slot, ...] = ("baseline", "simple", "complex")

RUNTIME_SCHEMA_VERSION = 1
API_KEY_ENV_VAR = "LITELLM_API_KEY"
_DEFAULT_BASE_URL = "http://localhost:4000"

# Offered in the settings panel when GET /v1/models is unreachable, ordered
# cheap -> expensive. `genailab-maas-gpt-4o` is the TCS GenAI Lab MaaS alias;
# if your gateway exposes something else, type it into the Custom alias field.
FALLBACK_MODELS: list[str] = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.5-pro",
    "genailab-maas-gpt-4o",
]

# Zero-config defaults encode the intended lab shape: fast -> simple,
# strong -> baseline + complex. Both aliases exist on the lab gateway and in the
# bundled usecase3-main/litellm/config.yaml, so a local proxy works with no .env.
_SLOT_FALLBACK: dict[Slot, str] = {
    "baseline": "gemini-2.5-pro",
    "simple": "gemini-2.5-flash",
    "complex": "gemini-2.5-pro",
}

SLOT_LABELS: dict[Slot, str] = {
    "baseline": "Baseline — every query",
    "simple": "Optimized · simple tier",
    "complex": "Optimized · complex tier",
}

SLOT_DESCRIPTIONS: dict[Slot, str] = {
    "baseline": "The unoptimized arm. One expensive model for every query, by design.",
    "simple": "Order lookups and short factual answers. Point this at the fast, cheap model.",
    "complex": "Disputes, multi-order questions, escalations. Point this at the strong model.",
}

# FastAPI runs sync endpoints in a threadpool, so reads and writes can race.
_LOCK = threading.RLock()
_RUNTIME_API_KEY: str | None = None  # in-process ONLY, never serialised
_CACHE: dict | None = None  # parsed runtime file; invalidated on write


class ConfigError(ValueError):
    """A rejected configuration update, safe to show to the user."""


# --------------------------------------------------------------------------- #
# Normalisation
# --------------------------------------------------------------------------- #


def normalize_base_url(raw: str | None) -> str:
    """Strip a trailing slash and a trailing ``/v1``.

    litellm needs an ``api_base`` that already ends in ``/v1``, so we append it
    ourselves. People reasonably paste the URL either way; without this a pasted
    ``/v1`` produces ``/v1/v1/chat/completions``, which the gateway answers with
    a 404 that reads exactly like a missing model.
    """
    url = (raw or "").strip().rstrip("/")
    if url.lower().endswith("/v1"):
        url = url[:-3].rstrip("/")
    return url or _DEFAULT_BASE_URL


def mask_secret(secret: str | None) -> str:
    if not secret:
        return ""
    if len(secret) <= 8:
        return "*" * 8
    return f"{secret[:4]}...{secret[-4:]}"


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #


def runtime_path() -> Path:
    return settings.llm_runtime_path


def _read_runtime() -> dict:
    """Parsed runtime file, or ``{}`` if it is missing or unreadable.

    A corrupt file must degrade to the environment layer, never crash boot.
    Memoised and invalidated on write only, so hand-editing the file needs a
    backend restart.
    """
    global _CACHE
    with _LOCK:
        if _CACHE is not None:
            return _CACHE
        try:
            raw = runtime_path().read_text(encoding="utf-8")
            parsed = json.loads(raw)
            _CACHE = parsed if isinstance(parsed, dict) else {}
        except (OSError, ValueError):
            _CACHE = {}
        return _CACHE


def _write_runtime(*, base_url: str | None, models: dict[str, str]) -> None:
    """Persist the runtime document.

    The document is assembled from an explicit whitelist rather than from
    ``**kwargs`` so that no future caller can smuggle the API key onto disk.
    """
    doc: dict = {
        "version": RUNTIME_SCHEMA_VERSION,
        "models": {slot: models[slot] for slot in SLOTS if models.get(slot)},
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if base_url:
        doc["base_url"] = base_url

    path = runtime_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)  # atomic on Windows and POSIX

    global _CACHE
    _CACHE = doc


def is_persisted() -> bool:
    return bool(_read_runtime())


# --------------------------------------------------------------------------- #
# Resolved values
# --------------------------------------------------------------------------- #


def _resolve_base_url() -> str:
    runtime = _read_runtime().get("base_url")
    if isinstance(runtime, str) and runtime.strip():
        return normalize_base_url(runtime)
    return normalize_base_url(settings.litellm_base_url_env)


def base_url() -> str:
    # apply() takes a `base_url` keyword, which shadows this name inside it, so
    # the resolution itself lives in _resolve_base_url().
    return _resolve_base_url()


def chat_api_base() -> str:
    """What litellm wants for ``api_base`` — the gateway root plus ``/v1``."""
    return f"{base_url()}/v1"


def models_url() -> str:
    return f"{base_url()}/v1/models"


def chat_url() -> str:
    """Shown in the settings panel so a misconfigured URL is obvious."""
    return f"{base_url()}/v1/chat/completions"


def api_key() -> str | None:
    with _LOCK:
        if _RUNTIME_API_KEY:
            return _RUNTIME_API_KEY
    return settings.litellm_api_key_env


def api_key_source() -> Literal["runtime", "env", "none"]:
    with _LOCK:
        if _RUNTIME_API_KEY:
            return "runtime"
    return "env" if settings.litellm_api_key_env else "none"


def has_api_key() -> bool:
    return bool(api_key())


def masked_api_key() -> str:
    return mask_secret(api_key())


def model_for(slot: Slot) -> str:
    runtime = (_read_runtime().get("models") or {}).get(slot)
    if isinstance(runtime, str) and runtime.strip():
        return runtime.strip()
    env = getattr(settings, f"model_{slot}", "")  # already OPTIBOT_MODEL_* aware
    if env and env.strip():
        return env.strip()
    return _SLOT_FALLBACK[slot]


def model_source(slot: Slot) -> Literal["runtime", "env", "fallback"]:
    runtime = (_read_runtime().get("models") or {}).get(slot)
    if isinstance(runtime, str) and runtime.strip():
        return "runtime"
    env = getattr(settings, f"model_{slot}", "")
    return "env" if env and env.strip() else "fallback"


def all_models() -> dict[str, str]:
    return {slot: model_for(slot) for slot in SLOTS}


# --------------------------------------------------------------------------- #
# Mutation
# --------------------------------------------------------------------------- #


def _validate_base_url(raw: str) -> str:
    normalized = normalize_base_url(raw)
    parsed = urlparse(normalized)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ConfigError(
            f"'{raw}' is not a usable gateway URL. Expected something like "
            "http://localhost:4000 or https://your-gateway.example.com/litellm."
        )
    return normalized


def apply(
    *,
    models: dict[str, str] | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
) -> dict[str, bool]:
    """Apply a partial configuration update.

    ``models`` and ``base_url`` are persisted; ``api_key`` is applied to this
    process only. Returns which of the three actually changed so the caller can
    word its message honestly.

    Raises ConfigError for a blank alias, an unknown slot, or a base URL that is
    not http/https.
    """
    global _RUNTIME_API_KEY

    cleaned_models: dict[str, str] = {}
    for slot, alias in (models or {}).items():
        if slot not in SLOTS:
            raise ConfigError(f"Unknown model slot '{slot}'. Expected one of {', '.join(SLOTS)}.")
        alias = (alias or "").strip()
        if not alias:
            raise ConfigError(f"The model alias for the '{slot}' slot cannot be blank.")
        cleaned_models[slot] = alias

    cleaned_base = _validate_base_url(base_url) if base_url and base_url.strip() else None
    cleaned_key = (api_key or "").strip() or None

    with _LOCK:
        # Did the caller actually move anything? Compared against the *effective*
        # values, so re-saving the same aliases reports "no change" honestly.
        changed = {
            "models": any(alias != model_for(slot) for slot, alias in cleaned_models.items()),
            "base_url": bool(cleaned_base) and cleaned_base != _resolve_base_url(),
            "api_key": bool(cleaned_key),
        }

        # Only touch the file when something persistable was submitted. A key-only
        # update must not create it: that would pin all three slots to today's
        # values and stop them tracking LITELLM_MODEL_* in backend/.env.
        if cleaned_models or cleaned_base:
            current = _read_runtime()
            # Only the submitted slots are pinned. Deliberately *not* seeded from
            # the effective values: freezing a slot the operator never touched
            # would stop it tracking LITELLM_MODEL_* in backend/.env, and the
            # panel already shows each slot's source so nothing is hidden.
            merged_models = dict(current.get("models") or {})
            merged_models.update(cleaned_models)
            _write_runtime(
                base_url=cleaned_base or current.get("base_url"), models=merged_models
            )

        if cleaned_key:
            _RUNTIME_API_KEY = cleaned_key

    return changed


def reset_for_tests() -> None:
    """Drop the in-process key and the parsed-file cache. Tests only."""
    global _RUNTIME_API_KEY, _CACHE
    with _LOCK:
        _RUNTIME_API_KEY = None
        _CACHE = None
