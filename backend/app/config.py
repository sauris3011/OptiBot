"""Central configuration for OptiBot.

Everything that differs between the *baseline* and *optimized* pipelines is
declared here so the two paths stay honestly comparable: same data, same
question, different machinery.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR.parent
DATA_DIR = BACKEND_DIR / "app" / "data"
POLICY_DIR = DATA_DIR / "policies"
DB_PATH = BACKEND_DIR / "optibot.db"

load_dotenv(BACKEND_DIR / ".env")


class Settings:
    """Runtime settings, read once at import."""

    # --- Models (LiteLLM routes on these) -------------------------------
    # Baseline deliberately sends *every* query to the expensive model.
    model_baseline: str = os.getenv("OPTIBOT_MODEL_BASELINE", "claude-sonnet-5")
    # Optimized routes by classified complexity.
    model_simple: str = os.getenv("OPTIBOT_MODEL_SIMPLE", "claude-haiku-4-5")
    model_complex: str = os.getenv("OPTIBOT_MODEL_COMPLEX", "claude-sonnet-5")

    max_tokens_baseline: int = 1024
    max_tokens_optimized: int = 700

    # --- Semantic cache --------------------------------------------------
    # Calibrated against all-MiniLM-L6-v2 on a labelled pair set (see
    # scripts/calibrate_cache.py). The should-hit and should-miss classes
    # overlap slightly, so this is tuned for precision over recall: a false
    # cache hit serves one customer another customer's answer, which costs
    # far more than a missed hit. 0.80 gave zero false hits on that set while
    # still matching genuine paraphrases (~0.82+).
    cache_threshold: float = float(os.getenv("OPTIBOT_CACHE_THRESHOLD", "0.80"))
    cache_ttl_order: int = int(os.getenv("OPTIBOT_CACHE_TTL_ORDER", "300"))
    cache_ttl_policy: int = int(os.getenv("OPTIBOT_CACHE_TTL_POLICY", "1800"))
    cache_max_entries: int = 500

    # --- RAG -------------------------------------------------------------
    rag_top_k: int = int(os.getenv("OPTIBOT_RAG_TOP_K", "3"))
    rag_min_score: float = float(os.getenv("OPTIBOT_RAG_MIN_SCORE", "0.15"))
    rag_chunk_tokens: int = 300
    rag_chunk_overlap: int = 50

    # --- Guardrails ------------------------------------------------------
    max_input_chars: int = 500
    rate_limit_per_minute: int = 20
    low_confidence_threshold: float = 0.7

    # --- Misc ------------------------------------------------------------
    api_key: str | None = os.getenv("ANTHROPIC_API_KEY")
    cors_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]

    @property
    def has_api_key(self) -> bool:
        return bool(self.api_key)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
