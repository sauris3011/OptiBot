"""Gateway transport: rate-limit detection, cost extraction, errors, discovery."""

from __future__ import annotations

import json
import os
import ssl

import httpx
import litellm
import pytest
import respx

from app import llm_settings
from app.config import settings
from app.services import gateway

MODELS_URL = "http://localhost:4000/v1/models"


class TestRateLimitDetection:
    def test_rate_limit_error_class(self):
        exc = litellm.exceptions.RateLimitError(
            message="slow down", llm_provider="openai", model="gemini-2.5-flash"
        )
        assert gateway._is_rate_limited(exc) is True

    def test_status_code_attribute(self):
        exc = RuntimeError("nope")
        exc.status_code = 429
        assert gateway._is_rate_limited(exc) is True

    def test_wrapped_body_code(self):
        """Gateways routinely wrap an upstream 429 in a non-429 envelope."""
        exc = RuntimeError('APIError: {"error":{"code":"429","message":"quota"}}')
        assert gateway._is_rate_limited(exc) is True

    def test_resource_exhausted(self):
        assert gateway._is_rate_limited(RuntimeError("RESOURCE_EXHAUSTED")) is True

    def test_a_500_is_not_retried(self):
        """An unreachable gateway arrives as APIError(500); retrying it just
        triples the time a misconfigured URL takes to report itself."""
        exc = RuntimeError("APIError: OpenAIException - Connection error.")
        exc.status_code = 500
        assert gateway._is_rate_limited(exc) is False


class _Usage:
    """Stands in for litellm's pydantic Usage, including its extras dict."""

    def __init__(self, **extra):
        self.prompt_tokens = 10
        self.completion_tokens = 5
        self.model_extra = extra


class _Response:
    def __init__(self, usage=None, hidden=None, **extra):
        self.usage = usage
        self._hidden_params = hidden or {}
        self.model_extra = extra


class TestReportedCost:
    def test_declared_usage_field(self):
        usage = _Usage()
        usage.response_cost = 0.5
        assert gateway.reported_cost(_Response(usage=usage)) == 0.5

    def test_usage_model_extra(self):
        """The likeliest silent bug: proxy-added fields are not declared, so a
        plain getattr on the pydantic model returns None."""
        assert gateway.reported_cost(_Response(usage=_Usage(response_cost=0.25))) == 0.25

    def test_usage_cost_alias(self):
        assert gateway.reported_cost(_Response(usage=_Usage(cost=0.125))) == 0.125

    def test_top_level_extra(self):
        assert gateway.reported_cost(_Response(response_cost=0.75)) == 0.75

    def test_hidden_params(self):
        assert gateway.reported_cost(_Response(hidden={"response_cost": 0.9})) == 0.9

    def test_zero_is_not_an_answer(self):
        """A gateway that does not price an alias reports 0.0."""
        assert gateway.reported_cost(_Response(usage=_Usage(response_cost=0.0))) is None

    def test_absent(self):
        assert gateway.reported_cost(_Response(usage=_Usage())) is None

    def test_non_numeric_is_ignored(self):
        assert gateway.reported_cost(_Response(usage=_Usage(response_cost="free"))) is None


class TestDescribeError:
    def test_auth(self):
        exc = litellm.exceptions.AuthenticationError(
            message="invalid x-api-key", llm_provider="openai", model="m"
        )
        message = gateway.describe_error(exc, "https://gw/litellm", "m")
        assert "authentication" in message
        assert "https://gw/litellm" in message
        assert "provider keys" in message

    def test_connection_names_the_url_litellm_dropped(self):
        exc = litellm.exceptions.APIConnectionError(
            message="Connection error.", llm_provider="openai", model="m"
        )
        message = gateway.describe_error(exc, "https://gw/litellm", "m")
        assert "Cannot reach" in message
        assert "https://gw/litellm" in message
        assert "LITELLM_CA_BUNDLE" in message

    def test_not_found_names_the_model(self):
        exc = litellm.exceptions.NotFoundError(
            message="model not found", llm_provider="openai", model="ghost"
        )
        assert "'ghost'" in gateway.describe_error(exc, "http://gw", "ghost")

    def test_unknown_falls_back_to_the_raw_message(self):
        assert "kaboom" in gateway.describe_error(RuntimeError("kaboom"), "http://gw", "m")


class TestTlsResolution:
    def test_default_verifies(self, monkeypatch):
        monkeypatch.setattr(settings, "litellm_ca_bundle", None)
        monkeypatch.setattr(settings, "litellm_ssl_verify_raw", "")
        assert gateway._resolve_verify() == (True, "default", None)

    @pytest.mark.parametrize("raw", ["false", "FALSE", " 0 ", "no", "off"])
    def test_escape_hatch_spellings(self, monkeypatch, raw):
        monkeypatch.setattr(settings, "litellm_ca_bundle", None)
        monkeypatch.setattr(settings, "litellm_ssl_verify_raw", raw)
        verify, source, warning = gateway._resolve_verify()
        assert (verify, source, warning) == (False, "LITELLM_SSL_VERIFY", None)

    def test_truthy_values_keep_verification_on(self, monkeypatch):
        monkeypatch.setattr(settings, "litellm_ca_bundle", None)
        monkeypatch.setattr(settings, "litellm_ssl_verify_raw", "true")
        assert gateway._resolve_verify()[0] is True

    def test_ca_bundle_that_exists(self, monkeypatch, tmp_path):
        bundle = tmp_path / "corp.pem"
        bundle.write_text("-----BEGIN CERTIFICATE-----", encoding="utf-8")
        monkeypatch.setattr(settings, "litellm_ca_bundle", str(bundle))
        assert gateway._resolve_verify() == (str(bundle), "LITELLM_CA_BUNDLE", None)

    def test_missing_ca_bundle_warns_instead_of_crashing_at_import(self, monkeypatch):
        """httpx raises a bare OSError for a bad bundle path, which would kill the
        process at import and leave no way to diagnose it in the UI."""
        monkeypatch.setattr(settings, "litellm_ca_bundle", r"C:\nope\missing.pem")
        monkeypatch.setattr(settings, "litellm_ssl_verify_raw", "")
        verify, source, warning = gateway._resolve_verify()
        assert (verify, source) == (True, "default")
        assert "missing.pem" in warning

    def test_ssl_verify_env_var_is_evicted_before_litellm_loads(self):
        """litellm reads SSL_VERIFY at import and passes the raw string to httpx as a
        CA path, so app/__init__.py has to pop it before litellm is ever imported.
        Anything later is too late — gateway.py imports litellm at its top."""
        assert "SSL_VERIFY" not in os.environ

    def test_litellm_price_map_stays_offline(self):
        """Otherwise litellm fetches its cost map from GitHub at import, which in an
        air-gapped lab is a slow import ending in a timeout. OptiBot prices locally."""
        assert os.environ.get("LITELLM_LOCAL_MODEL_COST_MAP") == "True"

    def test_a_file_that_is_not_a_valid_pem_degrades(self, tmp_path):
        """is_file() passes but httpx raises ssl.SSLError, so the client
        construction itself has to be guarded."""
        bad = tmp_path / "bad.pem"
        bad.write_text("-----BEGIN CERTIFICATE-----\nnope\n", encoding="utf-8")
        with pytest.raises((OSError, ssl.SSLError)):
            gateway._build_sessions(str(bad))


class TestAuthHeaders:
    def test_sends_both_forms(self):
        """Some corporate gateways only honour x-litellm-api-key."""
        assert gateway.auth_headers("sk-1") == {
            "Authorization": "Bearer sk-1",
            "x-litellm-api-key": "sk-1",
        }

    def test_no_key_no_headers(self):
        assert gateway.auth_headers(None) == {}


class TestKeyResolution:
    def test_loopback_without_a_key_uses_the_sentinel(self):
        assert gateway._resolve_key("http://localhost:4000", None) == gateway._NOAUTH_SENTINEL

    def test_remote_without_a_key_fails_with_guidance(self):
        with pytest.raises(gateway.GatewayError) as excinfo:
            gateway._resolve_key("https://gateway.example.com", None)
        assert "gear icon" in str(excinfo.value)
        assert "LITELLM_API_KEY" in str(excinfo.value)

    def test_override_wins(self):
        assert gateway._resolve_key("https://gateway.example.com", "sk-pending") == "sk-pending"


class TestListModelIds:
    @respx.mock
    def test_sends_the_hardening_flags_and_both_auth_headers(self, monkeypatch):
        monkeypatch.setattr(llm_settings, "api_key", lambda: "sk-test")
        route = respx.get(MODELS_URL).mock(
            return_value=httpx.Response(
                200, json={"data": [{"id": "gemini-2.5-pro"}, {"id": "gemini-2.5-flash"}]}
            )
        )
        assert gateway.list_model_ids() == ["gemini-2.5-flash", "gemini-2.5-pro"]

        request = route.calls.last.request
        # Without these four flags some hardened deployments answer 405.
        for flag in gateway._MODELS_QUERY:
            assert request.url.params[flag] == "false"
        assert request.headers["authorization"] == "Bearer sk-test"
        assert request.headers["x-litellm-api-key"] == "sk-test"

    @respx.mock
    def test_dedupes_and_drops_blanks(self):
        respx.get(MODELS_URL).mock(
            return_value=httpx.Response(
                200,
                json={"data": [{"id": "a"}, {"id": "a"}, {"id": " "}, {"id": None}, {"id": "b"}]},
            )
        )
        assert gateway.list_model_ids() == ["a", "b"]

    @respx.mock
    def test_405_becomes_a_gateway_error(self):
        respx.get(MODELS_URL).mock(return_value=httpx.Response(405, text="Method Not Allowed"))
        with pytest.raises(gateway.GatewayError) as excinfo:
            gateway.list_model_ids()
        assert "405" in str(excinfo.value)

    @respx.mock
    def test_non_json_is_explained(self):
        respx.get(MODELS_URL).mock(return_value=httpx.Response(200, text="<html>login</html>"))
        with pytest.raises(gateway.GatewayError) as excinfo:
            gateway.list_model_ids()
        assert "non-JSON" in str(excinfo.value)

    @respx.mock
    def test_connect_error(self):
        respx.get(MODELS_URL).mock(side_effect=httpx.ConnectError("refused"))
        with pytest.raises(gateway.GatewayError) as excinfo:
            gateway.list_model_ids()
        assert "Cannot reach" in str(excinfo.value)

    @respx.mock
    def test_base_url_override_with_a_pasted_v1(self):
        route = respx.get("https://gw.example/litellm/v1/models").mock(
            return_value=httpx.Response(200, json={"data": [{"id": "x"}]})
        )
        gateway.list_model_ids(base_url="https://gw.example/litellm/v1/")
        assert route.called


CHAT_URL = "http://localhost:4000/v1/chat/completions"


def _chat_payload(content="OK", **usage_extra):
    usage = {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12, **usage_extra}
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "model": "gemini-2.5-flash",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": content},
             "finish_reason": "stop"}
        ],
        "usage": usage,
    }


class TestChat:
    @respx.mock
    def test_request_shape_on_the_wire(self, monkeypatch):
        monkeypatch.setattr(llm_settings, "api_key", lambda: "sk-test")
        route = respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=_chat_payload()))

        result = gateway.chat(
            model="gemini-2.5-flash",
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=16,
        )

        request = route.calls.last.request
        # Both auth forms — some corporate gateways only honour the second.
        assert request.headers["authorization"] == "Bearer sk-test"
        assert request.headers["x-litellm-api-key"] == "sk-test"
        body = json.loads(request.content)
        # The alias goes through raw: the openai/ prefix is litellm routing only
        # and must not reach the gateway, or it 404s as an unknown model.
        assert body["model"] == "gemini-2.5-flash"
        assert body["max_tokens"] == 16
        assert result.text == "OK"
        assert (result.input_tokens, result.output_tokens) == (10, 2)

    @respx.mock
    def test_reads_the_gateway_cost_out_of_usage(self, monkeypatch):
        monkeypatch.setattr(llm_settings, "api_key", lambda: "sk-test")
        respx.post(CHAT_URL).mock(
            return_value=httpx.Response(200, json=_chat_payload(response_cost=0.000123))
        )
        result = gateway.chat(model="gemini-2.5-flash", messages=[{"role": "user", "content": "hi"}])
        assert result.reported_cost_usd == 0.000123

    @respx.mock
    def test_empty_content_is_a_failure_not_a_low_confidence_answer(self, monkeypatch):
        monkeypatch.setattr(llm_settings, "api_key", lambda: "sk-test")
        respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=_chat_payload(content="  ")))
        with pytest.raises(gateway.GatewayError) as excinfo:
            gateway.chat(model="gemini-2.5-flash", messages=[{"role": "user", "content": "hi"}])
        assert "empty content" in str(excinfo.value)

    @respx.mock
    def test_retries_a_429_then_succeeds(self, monkeypatch):
        monkeypatch.setattr(llm_settings, "api_key", lambda: "sk-test")
        monkeypatch.setattr(gateway, "_jitter", lambda _: 0.0)
        respx.post(CHAT_URL).mock(
            side_effect=[
                httpx.Response(429, json={"error": {"message": "slow down", "code": "429"}}),
                httpx.Response(200, json=_chat_payload()),
            ]
        )
        assert gateway.chat(
            model="gemini-2.5-flash", messages=[{"role": "user", "content": "hi"}]
        ).text == "OK"

    @respx.mock
    def test_does_not_retry_a_404(self, monkeypatch):
        monkeypatch.setattr(llm_settings, "api_key", lambda: "sk-test")
        route = respx.post(CHAT_URL).mock(
            return_value=httpx.Response(404, json={"error": {"message": "model not found"}})
        )
        with pytest.raises(gateway.GatewayError):
            gateway.chat(model="ghost", messages=[{"role": "user", "content": "hi"}])
        assert route.call_count == 1


class TestResolveModels:
    @respx.mock
    def test_live_list(self):
        respx.get(MODELS_URL).mock(
            return_value=httpx.Response(200, json={"data": [{"id": "gemini-2.5-flash"}]})
        )
        models, source, warning = gateway.resolve_models()
        assert (models, source, warning) == (["gemini-2.5-flash"], "litellm", None)

    @respx.mock
    def test_unreachable_falls_back_with_the_reason(self):
        respx.get(MODELS_URL).mock(side_effect=httpx.ConnectError("refused"))
        models, source, warning = gateway.resolve_models()
        assert models == llm_settings.FALLBACK_MODELS
        assert source == "fallback"
        assert "Cannot reach" in warning

    @respx.mock
    def test_empty_list_is_explained_not_shown_as_success(self):
        """A hardened gateway with an access-group filter answers 200 with []."""
        respx.get(MODELS_URL).mock(return_value=httpx.Response(200, json={"data": []}))
        models, source, warning = gateway.resolve_models()
        assert models == llm_settings.FALLBACK_MODELS
        assert source == "fallback"
        assert "empty model list" in warning
