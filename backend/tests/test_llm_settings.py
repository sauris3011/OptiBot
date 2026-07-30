"""Runtime-override precedence, URL normalisation, and the no-key-on-disk rule."""

from __future__ import annotations

import json

import pytest

from app import llm_settings
from app.config import settings


class TestPrecedence:
    def test_falls_back_when_nothing_is_configured(self):
        assert llm_settings.model_for("simple") == "gemini-2.5-flash"
        assert llm_settings.model_for("baseline") == "gemini-2.5-pro"
        assert llm_settings.model_source("simple") == "fallback"

    def test_env_beats_fallback(self, monkeypatch):
        monkeypatch.setattr(settings, "model_simple", "genailab-maas-gpt-4o")
        llm_settings.reset_for_tests()
        assert llm_settings.model_for("simple") == "genailab-maas-gpt-4o"
        assert llm_settings.model_source("simple") == "env"

    def test_runtime_beats_env(self, monkeypatch):
        monkeypatch.setattr(settings, "model_simple", "from-env")
        llm_settings.apply(models={"simple": "from-panel"})
        assert llm_settings.model_for("simple") == "from-panel"
        assert llm_settings.model_source("simple") == "runtime"

    def test_saving_one_slot_leaves_the_others_on_env(self, monkeypatch):
        """Pinning an untouched slot would stop it tracking LITELLM_MODEL_*."""
        monkeypatch.setattr(settings, "model_complex", "env-complex")
        llm_settings.reset_for_tests()
        llm_settings.apply(models={"simple": "picked"})
        assert llm_settings.model_for("simple") == "picked"
        assert llm_settings.model_source("simple") == "runtime"
        assert llm_settings.model_for("complex") == "env-complex"
        assert llm_settings.model_source("complex") == "env"

    def test_base_url_precedence(self, monkeypatch):
        monkeypatch.setattr(settings, "litellm_base_url_env", "https://env.example/litellm")
        llm_settings.reset_for_tests()
        assert llm_settings.base_url() == "https://env.example/litellm"
        llm_settings.apply(base_url="https://panel.example/gw")
        assert llm_settings.base_url() == "https://panel.example/gw"

    def test_api_key_precedence_and_source(self, monkeypatch):
        assert llm_settings.api_key_source() == "none"
        monkeypatch.setattr(settings, "litellm_api_key_env", "sk-from-env")
        assert llm_settings.api_key() == "sk-from-env"
        assert llm_settings.api_key_source() == "env"
        llm_settings.apply(models={"simple": "x"}, api_key="sk-from-panel")
        assert llm_settings.api_key() == "sk-from-panel"
        assert llm_settings.api_key_source() == "runtime"


class TestNormalisation:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("http://localhost:4000", "http://localhost:4000"),
            ("http://localhost:4000/", "http://localhost:4000"),
            # litellm needs api_base to end in /v1, which we append — so a pasted
            # /v1 has to be stripped or every request 404s on /v1/v1/...
            ("http://localhost:4000/v1", "http://localhost:4000"),
            ("http://localhost:4000/v1/", "http://localhost:4000"),
            ("http://localhost:4000/V1", "http://localhost:4000"),
            ("https://host/litellm/", "https://host/litellm"),
            ("  https://host/litellm/v1  ", "https://host/litellm"),
            ("", "http://localhost:4000"),
            (None, "http://localhost:4000"),
        ],
    )
    def test_normalize_base_url(self, raw, expected):
        assert llm_settings.normalize_base_url(raw) == expected

    def test_derived_urls_keep_a_mount_prefix(self):
        llm_settings.apply(base_url="https://host/litellm/v1")
        assert llm_settings.chat_api_base() == "https://host/litellm/v1"
        assert llm_settings.models_url() == "https://host/litellm/v1/models"
        assert llm_settings.chat_url() == "https://host/litellm/v1/chat/completions"

    @pytest.mark.parametrize(
        "secret,expected",
        [(None, ""), ("", ""), ("short", "********"), ("sk-abcdefghijklmnop", "sk-a...mnop")],
    )
    def test_mask_secret(self, secret, expected):
        assert llm_settings.mask_secret(secret) == expected


class TestValidation:
    @pytest.mark.parametrize("bad", ["ftp://host", "not a url", "file:///etc/passwd"])
    def test_rejects_non_http_base_url(self, bad):
        with pytest.raises(llm_settings.ConfigError):
            llm_settings.apply(base_url=bad)

    def test_rejects_blank_alias(self):
        with pytest.raises(llm_settings.ConfigError):
            llm_settings.apply(models={"simple": "   "})

    def test_rejects_unknown_slot(self):
        with pytest.raises(llm_settings.ConfigError):
            llm_settings.apply(models={"turbo": "gemini-2.5-flash"})

    def test_aliases_are_stripped(self):
        llm_settings.apply(models={"simple": "  gemini-2.5-flash  "})
        assert llm_settings.model_for("simple") == "gemini-2.5-flash"


class TestPersistence:
    def test_api_key_is_never_written_to_disk(self):
        """The one invariant this module exists to guarantee."""
        llm_settings.apply(
            models={"simple": "gemini-2.5-flash"},
            base_url="https://host/litellm",
            api_key="sk-super-secret-value",
        )
        raw = llm_settings.runtime_path().read_text(encoding="utf-8")
        assert "sk-super-secret-value" not in raw
        assert "api_key" not in json.loads(raw)
        # ...and it is still live in this process.
        assert llm_settings.api_key() == "sk-super-secret-value"

    def test_persisted_document_shape(self):
        llm_settings.apply(models={"simple": "gemini-2.5-flash"}, base_url="http://gw:4000")
        doc = json.loads(llm_settings.runtime_path().read_text(encoding="utf-8"))
        assert doc["version"] == llm_settings.RUNTIME_SCHEMA_VERSION
        assert doc["base_url"] == "http://gw:4000"
        assert doc["models"] == {"simple": "gemini-2.5-flash"}
        assert doc["updated_at"]

    def test_slots_accumulate_across_saves(self):
        llm_settings.apply(models={"simple": "a"})
        llm_settings.apply(models={"complex": "b"})
        doc = json.loads(llm_settings.runtime_path().read_text(encoding="utf-8"))
        assert doc["models"] == {"simple": "a", "complex": "b"}

    def test_key_only_update_does_not_create_a_file(self, monkeypatch):
        monkeypatch.setattr(settings, "model_simple", "env-simple")
        llm_settings.reset_for_tests()
        llm_settings.apply(api_key="sk-x")
        assert llm_settings.api_key() == "sk-x"
        assert llm_settings.model_source("simple") == "env"

    def test_corrupt_file_degrades_to_env(self, monkeypatch):
        llm_settings.runtime_path().write_text("{not json", encoding="utf-8")
        monkeypatch.setattr(settings, "model_simple", "env-simple")
        llm_settings.reset_for_tests()
        # No exception, and the environment layer still answers.
        assert llm_settings.model_for("simple") == "env-simple"
        assert llm_settings.base_url() == "http://localhost:4000"

    def test_reports_changed_fields(self):
        first = llm_settings.apply(models={"simple": "genailab-maas-gpt-4o"})
        assert first["models"] is True
        assert first["api_key"] is False
        again = llm_settings.apply(models={"simple": "genailab-maas-gpt-4o"})
        assert again["models"] is False

    def test_resaving_the_effective_alias_is_not_a_change(self):
        """The fallback for 'simple' is already gemini-2.5-flash."""
        assert llm_settings.apply(models={"simple": "gemini-2.5-flash"})["models"] is False
