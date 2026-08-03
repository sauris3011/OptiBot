"""Shared fixtures. Run with: cd backend && python -m pytest"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app import llm_settings  # noqa: E402
from app.config import settings  # noqa: E402
from app.services import metrics_service  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch):
    """Point the runtime store at a temp file and clear the in-process key.

    ``settings`` is a plain object with class attributes read at import, so tests
    patch the attribute rather than the environment variable.
    """
    monkeypatch.setattr(settings, "llm_runtime_path", tmp_path / "llm_runtime.json")
    monkeypatch.setattr(settings, "model_baseline", "")
    monkeypatch.setattr(settings, "model_simple", "")
    monkeypatch.setattr(settings, "model_complex", "")
    monkeypatch.setattr(settings, "litellm_base_url_env", "http://localhost:4000")
    monkeypatch.setattr(settings, "litellm_api_key_env", None)
    llm_settings.reset_for_tests()
    yield
    llm_settings.reset_for_tests()


@pytest.fixture(autouse=True)
def clean_metrics_db():
    """metrics_service writes to the real on-disk optibot.db, not a temp file —
    without this, one test's interaction rows leak into the next test's counts."""
    metrics_service.init_db()
    metrics_service.reset()
    yield
    metrics_service.reset()
