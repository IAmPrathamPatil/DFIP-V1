"""Ask wording providers: factory, Gemini generateContent, no default network."""

from __future__ import annotations

import httpx
import pytest
from dfip_api.ask_llm import (
    GEMINI_DEFAULT_API_BASE,
    GEMINI_INTENT_MAX_OUTPUT_TOKENS,
    GEMINI_INTENT_THINKING_LEVEL,
    GEMINI_WORDING_SCHEMA,
    AskLlmError,
    GeminiAskLlm,
    HttpAskLlm,
    _extract_gemini_text,
    ask_provider_is_enabled,
    build_ask_llm,
    gemini_ask_intent_schema,
)
from dfip_config.settings import Settings

GEMINI_OK = {
    "candidates": [
        {
            "content": {
                "role": "model",
                "parts": [
                    {
                        "text": (
                            "Revenue increased versus the comparison period "
                            "to 120.0000 from 25.0000."
                        )
                    }
                ],
            }
        }
    ]
}


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


def _gemini(**overrides) -> GeminiAskLlm:
    values = {
        "api_key": "test-key-not-real",
        "api_base": "https://gemini.test/v1beta",
        "model": "gemini-flash-test",
        "timeout_seconds": 11.0,
        "max_output_tokens": 222,
    }
    values.update(overrides)
    return GeminiAskLlm(**values)


def _response(
    status: int, *, json_body=None, text: str | None = None, url: str = ""
) -> httpx.Response:
    request = httpx.Request(
        "POST",
        url or "https://gemini.test/v1beta/models/gemini-flash-test:generateContent",
    )
    if json_body is not None:
        return httpx.Response(status, json=json_body, request=request)
    return httpx.Response(status, text=text or "", request=request)


def test_build_ask_llm_none_openai_gemini_and_unknown() -> None:
    assert ask_provider_is_enabled("none") is False
    assert ask_provider_is_enabled("") is False
    assert ask_provider_is_enabled("gemini") is True
    assert build_ask_llm(_settings()) is None
    assert build_ask_llm(_settings(dfip_ask_provider="none", dfip_ask_api_key="secret")) is None
    assert (
        build_ask_llm(
            _settings(dfip_ask_provider="gemini", dfip_ask_api_key="", dfip_ask_model="x")
        )
        is None
    )
    gemini = build_ask_llm(
        _settings(
            dfip_ask_provider="gemini",
            dfip_ask_api_key="secret",
            dfip_ask_model="gemini-flash-test",
        )
    )
    assert isinstance(gemini, GeminiAskLlm)
    openai = build_ask_llm(_settings(dfip_ask_provider="openai", dfip_ask_api_key="sk-test"))
    assert isinstance(openai, HttpAskLlm)
    assert build_ask_llm(_settings(dfip_ask_provider="claude", dfip_ask_api_key="secret")) is None
    assert (
        build_ask_llm(
            _settings(dfip_ask_provider="gemini", dfip_ask_api_key="secret", dfip_ask_model="")
        )
        is None
    )


def test_gemini_factory_uses_native_base_when_openai_default_remains() -> None:
    llm = build_ask_llm(
        _settings(
            dfip_ask_provider="gemini",
            dfip_ask_api_key="secret",
            dfip_ask_model="gemini-flash-test",
            dfip_ask_api_base="https://api.openai.com/v1",
        )
    )
    assert isinstance(llm, GeminiAskLlm)
    assert llm._api_base == GEMINI_DEFAULT_API_BASE
    custom = build_ask_llm(
        _settings(
            dfip_ask_provider="gemini",
            dfip_ask_api_key="secret",
            dfip_ask_model="gemini-flash-test",
            dfip_ask_api_base="https://gemini-proxy.test/v1beta",
        )
    )
    assert isinstance(custom, GeminiAskLlm)
    assert custom._api_base == "https://gemini-proxy.test/v1beta"


def test_gemini_http_uses_configured_base_model_and_extracts_text(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured.update(url=url, headers=headers, json=json, timeout=timeout)
        return _response(200, json_body=GEMINI_OK, url=url)

    monkeypatch.setattr("dfip_api.ask_llm.httpx.post", fake_post)
    text = _gemini().complete(system="SYS", user="USER")
    assert text == ("Revenue increased versus the comparison period to 120.0000 from 25.0000.")
    assert captured["url"] == (
        "https://gemini.test/v1beta/models/gemini-flash-test:generateContent"
    )
    assert "key=" not in str(captured["url"])
    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert headers["x-goog-api-key"] == "test-key-not-real"
    assert "Authorization" not in headers
    payload = captured["json"]
    assert isinstance(payload, dict)
    assert payload["systemInstruction"]["parts"][0]["text"] == "SYS"
    assert payload["contents"][0]["parts"][0]["text"] == "USER"
    assert payload["generationConfig"]["temperature"] == 0
    assert payload["generationConfig"]["maxOutputTokens"] == 222
    assert payload["generationConfig"]["responseMimeType"] == "application/json"
    assert payload["generationConfig"]["responseSchema"] == GEMINI_WORDING_SCHEMA
    assert "NUMBER" not in str(payload["generationConfig"]["responseSchema"])
    assert "tools" not in payload
    assert captured["timeout"] == 11.0
    assert "thinkingConfig" not in payload["generationConfig"]
    assert "thinkingBudget" not in payload["generationConfig"]
    from dfip_api.ask_llm import gemini_trend_selection_schema

    captured.clear()
    trend_schema = gemini_trend_selection_schema()
    _gemini().complete(system="SYS", user="USER", response_schema=trend_schema)
    trend_payload = captured["json"]
    assert isinstance(trend_payload, dict)
    assert trend_payload["generationConfig"]["responseSchema"] == trend_schema
    assert "user_claim" not in trend_payload["generationConfig"]["responseSchema"]["properties"]
    assert "thinkingConfig" not in trend_payload["generationConfig"]


def test_gemini_intent_call_uses_low_thinking_and_small_output(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured.update(json=json, timeout=timeout)
        return _response(200, json_body=GEMINI_OK, url=url)

    monkeypatch.setattr("dfip_api.ask_llm.httpx.post", fake_post)
    schema = gemini_ask_intent_schema()
    _gemini().complete(
        system="SYS",
        user="QUESTION: Help me interpret performance drivers.",
        response_schema=schema,
        thinking_level=GEMINI_INTENT_THINKING_LEVEL,
        max_output_tokens=GEMINI_INTENT_MAX_OUTPUT_TOKENS,
    )
    payload = captured["json"]
    assert isinstance(payload, dict)
    config = payload["generationConfig"]
    assert config["thinkingConfig"] == {"thinkingLevel": "low"}
    assert "thinkingBudget" not in config
    assert "thinking_budget" not in str(config)
    assert config["maxOutputTokens"] == 512
    assert config["responseSchema"] == schema
    assert "NUMBER" not in str(schema)
    assert payload["contents"][0]["parts"][0]["text"].startswith("QUESTION:")
    assert "EVIDENCE:" not in payload["contents"][0]["parts"][0]["text"]


def test_gemini_strips_models_prefix_from_configured_model(monkeypatch) -> None:
    captured: dict[str, str] = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        return _response(200, json_body=GEMINI_OK, url=url)

    monkeypatch.setattr("dfip_api.ask_llm.httpx.post", fake_post)
    _gemini(model="models/gemini-flash-test").complete(system="s", user="u")
    assert captured["url"].endswith("/models/gemini-flash-test:generateContent")
    assert "/models/models/" not in captured["url"]


def test_gemini_timeout_http_errors_and_malformed_payloads_raise(monkeypatch) -> None:
    def timeout_post(*_args, **_kwargs):
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr("dfip_api.ask_llm.httpx.post", timeout_post)
    with pytest.raises(AskLlmError, match="timed out"):
        _gemini().complete(system="s", user="u")

    def connect_post(*_args, **_kwargs):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr("dfip_api.ask_llm.httpx.post", connect_post)
    with pytest.raises(AskLlmError, match="request failed"):
        _gemini().complete(system="s", user="u")

    def status_post(status: int):
        def inner(*_args, **_kwargs):
            return _response(status, json_body={"error": {"code": status}})

        return inner

    monkeypatch.setattr("dfip_api.ask_llm.httpx.post", status_post(400))
    with pytest.raises(AskLlmError, match="request failed"):
        _gemini().complete(system="s", user="u")
    monkeypatch.setattr("dfip_api.ask_llm.httpx.post", status_post(503))
    with pytest.raises(AskLlmError, match="request failed"):
        _gemini().complete(system="s", user="u")

    def malformed_post(*_args, **_kwargs):
        return _response(200, json_body={"candidates": []})

    monkeypatch.setattr("dfip_api.ask_llm.httpx.post", malformed_post)
    with pytest.raises(AskLlmError, match="invalid payload"):
        _gemini().complete(system="s", user="u")

    def not_json_post(*_args, **_kwargs):
        return _response(200, text="<html>nope</html>")

    monkeypatch.setattr("dfip_api.ask_llm.httpx.post", not_json_post)
    with pytest.raises(AskLlmError, match="invalid payload"):
        _gemini().complete(system="s", user="u")

    def tool_post(*_args, **_kwargs):
        return _response(
            200,
            json_body={
                "candidates": [{"content": {"parts": [{"functionCall": {"name": "run_sql"}}]}}]
            },
        )

    monkeypatch.setattr("dfip_api.ask_llm.httpx.post", tool_post)
    with pytest.raises(AskLlmError, match="invalid payload"):
        _gemini().complete(system="s", user="u")

    def code_post(*_args, **_kwargs):
        return _response(
            200,
            json_body={
                "candidates": [
                    {
                        "content": {
                            "parts": [{"executableCode": {"language": "PYTHON", "code": "1"}}]
                        }
                    }
                ]
            },
        )

    monkeypatch.setattr("dfip_api.ask_llm.httpx.post", code_post)
    with pytest.raises(AskLlmError, match="invalid payload"):
        _gemini().complete(system="s", user="u")


def test_gemini_http_error_does_not_log_key_or_prompt(monkeypatch, caplog) -> None:
    def fake_post(*_args, **_kwargs):
        return _response(429, json_body={"error": {"message": "quota"}})

    monkeypatch.setattr("dfip_api.ask_llm.httpx.post", fake_post)
    with caplog.at_level("WARNING"):
        with pytest.raises(AskLlmError):
            _gemini().complete(system="secret-system", user="secret-user-evidence")
    joined = " ".join(record.getMessage() for record in caplog.records)
    assert "test-key-not-real" not in joined
    assert "secret-system" not in joined
    assert "secret-user-evidence" not in joined
    assert "ask-llm-http purpose=unspecified status=429" in joined
    assert "status=429" in joined

    caplog.clear()
    with caplog.at_level("WARNING"):
        with pytest.raises(AskLlmError):
            _gemini().complete(
                system="secret-system", user="secret-user-evidence", purpose="wording"
            )
    purposeful = " ".join(record.getMessage() for record in caplog.records)
    assert "ask-llm-http purpose=wording status=429" in purposeful
    assert "secret-system" not in purposeful


def test_gemini_extracts_visible_text_and_skips_thought_parts() -> None:
    visible = "Revenue increased versus the comparison period to 120.0000 from 25.0000."
    mixed = {
        "candidates": [
            {
                "finishReason": "STOP",
                "content": {
                    "parts": [
                        {
                            "thought": True,
                            "text": "Because 999.9 looks like a drop I should refuse.",
                        },
                        {"text": visible},
                    ]
                },
            }
        ]
    }
    assert _extract_gemini_text(mixed) == visible
    from dfip_analytics.ask import validate_llm_answer

    evidence = {
        "metric": {
            "metric": "revenue_inr",
            "label": "Revenue",
            "current": "120.0000",
            "comparison": "25.0000",
            "delta_pct": "3.800000",
        }
    }
    assert validate_llm_answer(visible, evidence)
    assert (
        validate_llm_answer("Because 999.9 looks like a drop I should refuse. " + visible, evidence)
        is None
    )
    thoughts_only = {
        "candidates": [{"content": {"parts": [{"thought": True, "text": "Because 9."}]}}]
    }
    with pytest.raises(AskLlmError, match="invalid payload"):
        _extract_gemini_text(thoughts_only)


def test_ask_intent_schema_is_bounded_enums_only() -> None:
    schema = gemini_ask_intent_schema()
    operations = schema["properties"]["operation"]["enum"]
    assert "explain_metric_change" in operations
    assert "generate_trend" in operations
    assert "unsupported" in operations
    assert "rank_periods" not in operations
    assert "NUMBER" not in str(schema)
    assert schema["required"] == ["operation"]
