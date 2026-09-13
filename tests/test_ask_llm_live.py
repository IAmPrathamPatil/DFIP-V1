"""Opt-in live Gemini Ask wording. Synthetic evidence only. Skipped in CI."""

from __future__ import annotations

import json as json_codec
import logging
import os
import time

import httpx
import pytest
from dfip_analytics.ask import (
    NUMBER_RE,
    _number_forms,
    classify_intent,
    coerce_llm_wording,
    explain_llm_answer,
)
from dfip_api.ask_llm import (
    GEMINI_DEFAULT_API_BASE,
    GEMINI_INTENT_MAX_OUTPUT_TOKENS,
    GeminiAskLlm,
    _extract_gemini_text,
    build_ask_llm,
    gemini_ask_intent_schema,
    gemini_trend_selection_schema,
)
from dfip_config.settings import Settings

from test_d2_global_filters import _store as d2_store
from test_d3_dynamic_trends import _d3_store
from test_d8_contextual_ask import _post

LIVE_ENABLED = os.environ.get("DFIP_ASK_LIVE", "").strip().lower() in {"1", "true", "yes"}
LIVE_MAX_ATTEMPTS = 3
LIVE_RETRY_BACKOFF_SECONDS = (1.0, 2.0)
LIVE_MAX_OUTPUT_TOKENS = 4096


def _live_key() -> str:
    return os.environ.get("DFIP_ASK_API_KEY", "").strip()


def _live_model() -> str:
    configured = os.environ.get("DFIP_ASK_MODEL", "").strip()
    if configured and not configured.lower().startswith("gpt-"):
        return configured
    return "gemini-2.5-flash"


def _cites_evidence_current(answer: str, current: object) -> bool:
    """True when the wording cites the focused metric current in any allowed form."""
    allowed = _number_forms(str(current))
    if not allowed:
        return False
    for match in NUMBER_RE.finditer(str(answer or "")):
        if allowed.intersection(_number_forms(match.group(0))):
            return True
    return False


def _capture_request_meta(meta: dict[str, object], url, headers, payload, timeout) -> None:
    system_parts = payload.get("systemInstruction", {}).get("parts", [])
    user_parts = (payload.get("contents") or [{}])[0].get("parts", [])
    config = payload.get("generationConfig") or {}
    meta["url"] = url
    meta["timeout"] = timeout
    meta["has_goog_key"] = bool(headers and headers.get("x-goog-api-key"))
    meta["has_authorization"] = bool(headers and headers.get("Authorization"))
    meta["key_in_url"] = "key=" in str(url).lower()
    meta["has_system"] = bool(system_parts and system_parts[0].get("text"))
    meta["has_user"] = bool(user_parts and user_parts[0].get("text"))
    meta["temperature"] = config.get("temperature")
    meta["max_output_tokens"] = config.get("maxOutputTokens")
    system_text = system_parts[0].get("text", "") if system_parts else ""
    meta["system_grounded"] = "appear directly in EVIDENCE" in system_text
    meta["system_asks_current"] = "focused metric current" in system_text
    meta["system_complete"] = "mid-sentence" in system_text
    meta["system_no_derived"] = "Do not calculate" in system_text
    meta["system_current_is"] = "using is or current" in system_text
    meta["system_json"] = "Prefer JSON" in system_text
    meta["response_mime"] = config.get("responseMimeType")
    meta["has_response_schema"] = isinstance(config.get("responseSchema"), dict)
    schema = config.get("responseSchema") or {}
    meta["user_text"] = user_parts[0].get("text", "") if user_parts else ""
    meta["user_has_evidence"] = "EVIDENCE:" in str(meta["user_text"])
    meta["schema_has_operation"] = "operation" in (schema.get("properties") or {})
    meta["schema_has_user_claim"] = "user_claim" in (schema.get("properties") or {})
    meta["schema_has_number_type"] = "NUMBER" in str(schema)
    thinking = config.get("thinkingConfig") or {}
    meta["thinking_level"] = thinking.get("thinkingLevel") if isinstance(thinking, dict) else None
    meta["has_thinking_budget"] = "thinkingBudget" in config or "thinking_budget" in str(config)


def _capture_response_meta(meta: dict[str, object], response) -> None:
    """Record finish/usage fields only. Never store prompt, evidence, or answer text."""
    meta["last_status"] = response.status_code
    if response.status_code != 200:
        return
    try:
        body = response.json()
    except ValueError:
        return
    if not isinstance(body, dict):
        return
    first = (body.get("candidates") or [None])[0]
    if isinstance(first, dict):
        meta["finish_reason"] = first.get("finishReason")
    usage = body.get("usageMetadata") or {}
    if isinstance(usage, dict):
        meta["thoughts_token_count"] = usage.get("thoughtsTokenCount")
        meta["candidates_token_count"] = usage.get("candidatesTokenCount")
    try:
        meta["gemini_text"] = _extract_gemini_text(body)
    except Exception:
        meta["gemini_text"] = None


def _timeout_phase(exc: BaseException) -> str | None:
    if isinstance(exc, httpx.ConnectTimeout):
        return "connect"
    if isinstance(exc, httpx.ReadTimeout):
        return "read"
    if isinstance(exc, httpx.WriteTimeout):
        return "write"
    if isinstance(exc, httpx.PoolTimeout):
        return "pool"
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    return None


def _transient_network_failure(exc: BaseException) -> str | None:
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.NetworkError):
        return "network"
    return None


def _post_with_transient_retry(
    post, url, *, headers, json, timeout, statuses: list[int], meta: dict[str, object]
):
    """Retry transient 5xx and network/timeouts. 4xx/2xx return immediately. Harness-only."""
    payload = json or {}
    last = None
    meta["invoked"] = True
    meta["attempts"] = 0
    for attempt in range(LIVE_MAX_ATTEMPTS):
        meta["attempts"] = attempt + 1
        if attempt == 0:
            _capture_request_meta(meta, url, headers, payload, timeout)
        try:
            response = post(url, headers=headers, json=json, timeout=timeout)
        except Exception as exc:
            kind = _transient_network_failure(exc)
            if kind is None:
                meta["failure_class"] = "error"
                raise
            meta["failure_class"] = kind
            phase = _timeout_phase(exc)
            if phase:
                meta["timeout_phase"] = phase
            if attempt + 1 >= LIVE_MAX_ATTEMPTS:
                raise
            delay = LIVE_RETRY_BACKOFF_SECONDS[min(attempt, len(LIVE_RETRY_BACKOFF_SECONDS) - 1)]
            time.sleep(delay)
            continue
        statuses.append(response.status_code)
        if response.status_code < 500:
            _capture_response_meta(meta, response)
            meta["failure_class"] = "http_4xx" if response.status_code >= 400 else "http_2xx"
            return response
        meta["failure_class"] = "http_5xx"
        last = response
        if attempt + 1 >= LIVE_MAX_ATTEMPTS:
            _capture_response_meta(meta, response)
            break
        delay = LIVE_RETRY_BACKOFF_SECONDS[min(attempt, len(LIVE_RETRY_BACKOFF_SECONDS) - 1)]
        time.sleep(delay)
    return last


def _skip_if_live_blocked(meta: dict[str, object], statuses: list[int]) -> None:
    failure = str(meta.get("failure_class") or "")
    if failure not in {"timeout", "network", "http_5xx"}:
        return
    pytest.skip(
        "BLOCKED/UNAVAILABLE: Gemini "
        f"{failure} after {meta.get('attempts') or len(statuses)} attempts "
        f"phase={meta.get('timeout_phase')!r} elapsed_ms={meta.get('elapsed_ms')!r} "
        f"HTTP statuses={statuses}"
    )


def test_live_harness_retries_transient_5xx_not_client_errors(monkeypatch) -> None:
    monkeypatch.setattr(time, "sleep", lambda *_args, **_kwargs: None)
    request = httpx.Request("POST", "https://gemini.test/v1beta/models/x:generateContent")
    sequence = [503, 503, 200]
    calls: list[int] = []

    def fake_post(url, headers=None, json=None, timeout=None):
        status = sequence[len(calls)]
        calls.append(status)
        return httpx.Response(status, json={"ok": status == 200}, request=request)

    statuses: list[int] = []
    meta: dict[str, object] = {}
    ok = _post_with_transient_retry(
        fake_post,
        str(request.url),
        headers={"x-goog-api-key": "test-key-not-real"},
        json={"generationConfig": {"temperature": 0, "maxOutputTokens": 300}},
        timeout=20.0,
        statuses=statuses,
        meta=meta,
    )
    assert statuses == [503, 503, 200]
    assert ok.status_code == 200
    assert meta["has_goog_key"] is True
    assert meta["key_in_url"] is False

    calls.clear()
    four_xx = [401]

    def fake_401(url, headers=None, json=None, timeout=None):
        calls.append(four_xx[0])
        return httpx.Response(401, json={"error": {"code": 401}}, request=request)

    denied_statuses: list[int] = []
    denied = _post_with_transient_retry(
        fake_401,
        str(request.url),
        headers={},
        json={},
        timeout=5.0,
        statuses=denied_statuses,
        meta={},
    )
    assert denied_statuses == [401]
    assert denied.status_code == 401
    assert len(calls) == 1


def test_live_harness_retries_timeouts_and_records_invocation(monkeypatch) -> None:
    monkeypatch.setattr(time, "sleep", lambda *_args, **_kwargs: None)
    request = httpx.Request("POST", "https://gemini.test/v1beta/models/x:generateContent")
    calls: list[str] = []

    def timeout_then_ok(url, headers=None, json=None, timeout=None):
        calls.append("try")
        if len(calls) < 3:
            raise httpx.ReadTimeout("read timeout", request=request)
        return httpx.Response(200, json={"ok": True}, request=request)

    recovered: dict[str, object] = {}
    ok = _post_with_transient_retry(
        timeout_then_ok,
        str(request.url),
        headers={"x-goog-api-key": "test-key-not-real"},
        json={"generationConfig": {"temperature": 0}},
        timeout=20.0,
        statuses=[],
        meta=recovered,
    )
    assert len(calls) == 3
    assert ok.status_code == 200
    assert recovered["invoked"] is True
    assert recovered["attempts"] == 3
    assert recovered["failure_class"] == "http_2xx"

    persistent: dict[str, object] = {}
    persistent_statuses: list[int] = []

    def always_timeout(url, headers=None, json=None, timeout=None):
        raise httpx.ReadTimeout("read timeout", request=request)

    with pytest.raises(httpx.TimeoutException):
        _post_with_transient_retry(
            always_timeout,
            str(request.url),
            headers={},
            json={},
            timeout=5.0,
            statuses=persistent_statuses,
            meta=persistent,
        )
    assert persistent_statuses == []
    assert persistent["invoked"] is True
    assert persistent["attempts"] == LIVE_MAX_ATTEMPTS
    assert persistent["failure_class"] == "timeout"


def test_live_grounding_accepts_allowed_number_forms_not_only_four_decimals() -> None:
    assert _cites_evidence_current("Revenue increased to 120.0000 from 25.0000.", "120.0000")
    assert _cites_evidence_current("Revenue increased to 120 from 25.", "120.0000")
    assert _cites_evidence_current("Revenue is 120.0 in this period.", "120.0000")
    assert not _cites_evidence_current("The evidence shows that revenue did increase.", "120.0000")
    assert not _cites_evidence_current("Revenue is 999.", "120.0000")
    assert not _cites_evidence_current(
        "Revenue did not fall in the provided data; instead, current",
        "120.0000",
    )


@pytest.mark.ask_live
@pytest.mark.skipif(
    not LIVE_ENABLED,
    reason="DFIP_ASK_LIVE is not set; live Gemini Ask is opt-in",
)
def test_live_gemini_ask_uses_native_generate_content_on_synthetic_evidence(
    monkeypatch, caplog
) -> None:
    key = _live_key()
    if not key:
        pytest.skip("DFIP_ASK_API_KEY is not set")
    model = _live_model()
    settings = Settings(
        _env_file=None,
        dfip_ask_provider="gemini",
        dfip_ask_api_key=key,
        dfip_ask_model=model,
        dfip_ask_api_base="",
        dfip_ask_timeout_seconds=20,
        dfip_ask_max_output_tokens=LIVE_MAX_OUTPUT_TOKENS,
    )
    llm = build_ask_llm(settings)
    assert isinstance(llm, GeminiAskLlm)
    assert llm._api_base == GEMINI_DEFAULT_API_BASE
    assert llm._model == model

    meta: dict[str, object] = {}
    statuses: list[int] = []
    real_post = httpx.post

    def wrapped(url, headers=None, json=None, timeout=None):
        return _post_with_transient_retry(
            real_post,
            url,
            headers=headers,
            json=json,
            timeout=timeout,
            statuses=statuses,
            meta=meta,
        )

    monkeypatch.setattr("dfip_api.ask_llm.httpx.post", wrapped)
    with caplog.at_level(logging.DEBUG):
        response, _http = _post(
            d2_store(),
            "Why did revenue fall?",
            source="kpi",
            focus={"metric": "revenue_inr"},
            llm=llm,
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "answered"
    assert body["intent"] == "explain_metric_change"
    assert body["operation"] == "explain_metric_change"

    if body["llm_used"] is not True:
        _skip_if_live_blocked(meta, statuses)
        if statuses and all(status >= 500 for status in statuses):
            pytest.skip(
                "BLOCKED/UNAVAILABLE: Gemini generateContent returned HTTP "
                f"{statuses} after {len(statuses)} attempts"
            )
        wording = str(meta.get("gemini_text") or "")
        rendered = coerce_llm_wording(wording, body.get("evidence") or {})
        _accepted, rule = explain_llm_answer(rendered, body.get("evidence") or {})
        pytest.fail(
            "Gemini wording was not accepted. "
            f"HTTP statuses={statuses} llm_used={body['llm_used']} "
            f"finishReason={meta.get('finish_reason')!r} "
            f"validator={rule!r} wording={wording[:400]!r} "
            f"rendered={rendered[:400]!r}"
        )

    assert body["llm_used"] is True
    current = (body.get("evidence") or {}).get("metric") or {}
    current_value = current.get("current")
    assert current_value == "120.0000"
    assert "revenue" in body["answer"].lower()
    assert _cites_evidence_current(body["answer"], current_value)
    derived = _number_forms("95")
    for match in NUMBER_RE.finditer(body["answer"]):
        assert derived.isdisjoint(_number_forms(match.group(0)))
    assert "999.9" not in body["answer"]
    assert body["answer"].rstrip().endswith((".", "?"))

    url = str(meta["url"])
    assert url.startswith(f"{GEMINI_DEFAULT_API_BASE}/models/")
    assert url.endswith(f"/{model}:generateContent")
    assert meta["has_goog_key"] is True
    assert meta["has_authorization"] is False
    assert meta["key_in_url"] is False
    assert meta["has_system"] is True
    assert meta["has_user"] is True
    assert meta["system_grounded"] is True
    assert meta["system_asks_current"] is True
    assert meta["system_complete"] is True
    assert meta["system_no_derived"] is True
    assert meta["system_current_is"] is True
    assert meta["system_json"] is True
    assert meta["response_mime"] == "application/json"
    assert meta["has_response_schema"] is True
    assert meta["schema_has_number_type"] is False
    assert meta["temperature"] == 0
    assert meta["max_output_tokens"] == LIVE_MAX_OUTPUT_TOKENS
    assert meta["timeout"] == 20.0
    assert meta.get("finish_reason") != "MAX_TOKENS"

    joined = " ".join(record.getMessage() for record in caplog.records)
    assert key not in joined
    assert "x-goog-api-key" not in joined.lower()
    assert "EVIDENCE:" not in joined
    assert "120.0000" not in joined
    assert statuses
    assert statuses[-1] < 400


@pytest.mark.ask_live
@pytest.mark.skipif(
    not LIVE_ENABLED,
    reason="DFIP_ASK_LIVE is not set; live Gemini Ask is opt-in",
)
def test_live_gemini_generate_trend_selection_on_synthetic_request(monkeypatch, caplog) -> None:
    key = _live_key()
    if not key:
        pytest.skip("DFIP_ASK_API_KEY is not set")
    model = _live_model()
    settings = Settings(
        _env_file=None,
        dfip_ask_provider="gemini",
        dfip_ask_api_key=key,
        dfip_ask_model=model,
        dfip_ask_api_base="",
        dfip_ask_timeout_seconds=20,
        dfip_ask_max_output_tokens=LIVE_MAX_OUTPUT_TOKENS,
    )
    llm = build_ask_llm(settings)
    assert isinstance(llm, GeminiAskLlm)

    meta: dict[str, object] = {}
    statuses: list[int] = []
    real_post = httpx.post

    def wrapped(url, headers=None, json=None, timeout=None):
        return _post_with_transient_retry(
            real_post,
            url,
            headers=headers,
            json=json,
            timeout=timeout,
            statuses=statuses,
            meta=meta,
        )

    monkeypatch.setattr("dfip_api.ask_llm.httpx.post", wrapped)
    with caplog.at_level(logging.DEBUG):
        response, _http = _post(
            _d3_store(),
            "Show revenue and ROAS by day",
            llm=llm,
        )
    assert response.status_code == 200, response.text
    body = response.json()
    if body["status"] != "answered" or body.get("llm_used") is not True:
        _skip_if_live_blocked(meta, statuses)
        if statuses and all(status >= 500 for status in statuses):
            pytest.skip(
                "BLOCKED/UNAVAILABLE: Gemini generateContent returned HTTP "
                f"{statuses} after {len(statuses)} attempts"
            )
        pytest.fail(
            "Generate Trend live selection was not accepted. "
            f"HTTP statuses={statuses} status={body.get('status')!r} "
            f"llm_used={body.get('llm_used')!r} finishReason={meta.get('finish_reason')!r} "
            f"empty_reason={body.get('empty_reason')!r} "
            f"wording={str(meta.get('gemini_text') or '')[:400]!r}"
        )
    selection = (body.get("evidence") or {}).get("selection") or {}
    assert body["intent"] == "generate_trend"
    assert selection.get("metric") == "revenue_inr"
    assert selection.get("secondary") == "overall_roas"
    assert selection.get("grain") == "day"
    assert body["evidence"]["trend"]["point_count"] >= 1
    assert meta.get("response_mime") == "application/json"
    assert meta.get("has_response_schema") is True
    assert meta.get("schema_has_number_type") is False
    assert meta.get("finish_reason") != "MAX_TOKENS"
    joined = " ".join(record.getMessage() for record in caplog.records)
    assert key not in joined
    assert "EVIDENCE:" not in joined
    schema = gemini_trend_selection_schema()
    assert "NUMBER" not in str(schema)


INTENT_LIVE_QUESTION = "Help me interpret what moved performance."


def test_provider_off_keeps_intent_paraphrase_unsupported_without_network(monkeypatch) -> None:
    complete_calls: list[int] = []

    def boom(*_args, **_kwargs):
        raise AssertionError("provider=none must not call Gemini")

    def spy_complete(self, **_kwargs):
        complete_calls.append(1)
        raise AssertionError("provider=none must not call LLM complete")

    monkeypatch.setenv("DFIP_ASK_PROVIDER", "gemini")
    monkeypatch.setenv("DFIP_ASK_API_KEY", "should-not-be-used")
    monkeypatch.setenv("DFIP_ASK_MODEL", "gemini-3.8-flash")
    monkeypatch.setattr("dfip_api.ask_llm.httpx.post", boom)
    monkeypatch.setattr("dfip_api.ask_llm.GeminiAskLlm.complete", spy_complete)
    decision = classify_intent(INTENT_LIVE_QUESTION, source="overview")
    assert decision.intent == "unsupported"
    assert decision.reason == "unsupported_intent"
    response, http = _post(d2_store(), INTENT_LIVE_QUESTION)
    assert http.app.state.settings.dfip_ask_provider == "none"
    assert http.app.state.ask_llm is None
    assert complete_calls == []
    body = response.json()
    assert body["status"] == "unsupported"
    assert body["intent"] == "unsupported"
    assert body["empty_reason"] == "unsupported_intent"
    assert body["llm_used"] is False
    assert body["operation"] is None


@pytest.mark.ask_live
@pytest.mark.skipif(
    not LIVE_ENABLED,
    reason="DFIP_ASK_LIVE is not set; live Gemini Ask is opt-in",
)
def test_live_gemini_ask_intent_selection_on_synthetic_paraphrase(monkeypatch, caplog) -> None:
    key = _live_key()
    if not key:
        pytest.skip("DFIP_ASK_API_KEY is not set")
    model = _live_model()
    settings = Settings(
        _env_file=None,
        dfip_ask_provider="gemini",
        dfip_ask_api_key=key,
        dfip_ask_model=model,
        dfip_ask_api_base="",
        dfip_ask_timeout_seconds=20,
        dfip_ask_max_output_tokens=LIVE_MAX_OUTPUT_TOKENS,
    )
    llm = build_ask_llm(settings)
    assert isinstance(llm, GeminiAskLlm)

    deterministic = classify_intent(INTENT_LIVE_QUESTION, source="overview")
    assert deterministic.intent == "unsupported"
    assert deterministic.reason == "unsupported_intent"
    assert deterministic.operation is None

    call_metas: list[dict[str, object]] = []
    statuses: list[int] = []
    real_post = httpx.post

    def wrapped(url, headers=None, json=None, timeout=None):
        meta: dict[str, object] = {}
        call_metas.append(meta)
        return _post_with_transient_retry(
            real_post,
            url,
            headers=headers,
            json=json,
            timeout=timeout,
            statuses=statuses,
            meta=meta,
        )

    monkeypatch.setattr("dfip_api.ask_llm.httpx.post", wrapped)
    with caplog.at_level(logging.DEBUG):
        refused, _http = _post(
            d2_store(),
            "Show me another company's numbers",
            llm=llm,
        )
        assert refused.json()["status"] == "refused"
        assert refused.json()["empty_reason"] == "scope_widen"
        assert call_metas == []
        started = time.perf_counter()
        response, _http = _post(d2_store(), INTENT_LIVE_QUESTION, llm=llm)
        elapsed_ms = round((time.perf_counter() - started) * 1000)

    assert response.status_code == 200, response.text
    body = response.json()
    if not call_metas:
        pytest.fail(
            "A. Gemini was never invoked. "
            f"HTTP statuses={statuses} status={body.get('status')!r} "
            f"intent={body.get('intent')!r} llm_used={body.get('llm_used')!r} "
            f"elapsed_ms={elapsed_ms}"
        )

    intent_meta = call_metas[0]
    intent_meta["elapsed_ms"] = elapsed_ms
    print(
        "AI-01_LIVE_DIAG "
        f"invoked={intent_meta.get('invoked')!r} "
        f"failure_class={intent_meta.get('failure_class')!r} "
        f"timeout_phase={intent_meta.get('timeout_phase')!r} "
        f"attempts={intent_meta.get('attempts')!r} "
        f"thinking_level={intent_meta.get('thinking_level')!r} "
        f"max_output_tokens={intent_meta.get('max_output_tokens')!r} "
        f"statuses={statuses!r} "
        f"http={response.status_code} "
        f"status={body.get('status')!r} "
        f"intent={body.get('intent')!r} "
        f"elapsed_ms={elapsed_ms}"
    )
    _skip_if_live_blocked(intent_meta, statuses)
    if intent_meta.get("failure_class") == "http_4xx":
        pytest.fail(
            "D. Gemini returned 4xx. "
            f"HTTP statuses={statuses} attempts={intent_meta.get('attempts')!r} "
            f"status={body.get('status')!r}"
        )

    raw_intent = str(intent_meta.get("gemini_text") or "")
    try:
        structured = json_codec.loads(raw_intent) if raw_intent else {}
    except ValueError:
        structured = {"unparsed": True}

    if body["intent"] in {"unsupported", "refused"}:
        if not raw_intent or structured.get("unparsed"):
            pytest.fail(
                "E. Gemini returned 2xx but malformed structured JSON. "
                f"HTTP statuses={statuses} status={body.get('status')!r} "
                f"intent={body.get('intent')!r} empty_reason={body.get('empty_reason')!r} "
                f"structured={structured!r} finishReason={intent_meta.get('finish_reason')!r}"
            )
        pytest.fail(
            "F. Gemini returned structured intent but downstream validation rejected it. "
            f"HTTP statuses={statuses} status={body.get('status')!r} "
            f"intent={body.get('intent')!r} empty_reason={body.get('empty_reason')!r} "
            f"structured={structured!r} finishReason={intent_meta.get('finish_reason')!r}"
        )

    assert body["intent"] == "identify_driver"
    assert body["operation"] == "identify_driver"
    assert body["status"] == "answered"
    evidence = body.get("evidence") or {}
    evidence_metric = (evidence.get("metric") or {}).get("metric") or evidence.get("metric")
    evidence_dimension = (
        (evidence.get("selection") or {}).get("dimension")
        or evidence.get("dimension")
        or (evidence.get("insight") or {}).get("dimension")
    )
    assert evidence_metric == "revenue_inr" or structured.get("metric") == "revenue_inr"
    print(
        "AI-01_LIVE "
        f"question={INTENT_LIVE_QUESTION!r} "
        f"deterministic={deterministic.intent}/{deterministic.reason} "
        f"structured={structured!r} "
        f"operation={body.get('operation')!r} "
        f"metric={evidence_metric!r} "
        f"dimension={evidence_dimension!r} "
        f"http={response.status_code} "
        f"status={body.get('status')!r} "
        f"empty_reason={body.get('empty_reason')!r} "
        f"llm_used={body.get('llm_used')!r} "
        f"gemini_calls={len(call_metas)} "
        f"model={model!r}"
    )
    assert body["operation"] in gemini_ask_intent_schema()["properties"]["operation"]["enum"]
    assert "rank_periods" not in gemini_ask_intent_schema()["properties"]["operation"]["enum"]
    assert intent_meta.get("thinking_level") == "low"
    assert intent_meta.get("has_thinking_budget") is False
    assert intent_meta.get("max_output_tokens") == GEMINI_INTENT_MAX_OUTPUT_TOKENS
    assert intent_meta.get("schema_has_operation") is True
    assert intent_meta.get("schema_has_user_claim") is False
    assert intent_meta.get("schema_has_number_type") is False
    assert intent_meta.get("user_has_evidence") is False
    assert str(intent_meta.get("user_text") or "").startswith("QUESTION:")
    assert INTENT_LIVE_QUESTION in str(intent_meta.get("user_text") or "")
    assert "EVIDENCE:" not in str(intent_meta.get("user_text") or "")
    user_blob = str(intent_meta.get("user_text") or "")
    assert "eyJ" not in user_blob
    assert "client_id" not in user_blob.lower()
    assert "database" not in user_blob.lower()
    assert str(intent_meta.get("url", "")).startswith(f"{GEMINI_DEFAULT_API_BASE}/models/")
    assert str(intent_meta.get("url", "")).endswith(f"/{model}:generateContent")
    assert intent_meta.get("has_goog_key") is True
    assert intent_meta.get("has_authorization") is False
    assert intent_meta.get("key_in_url") is False
    assert intent_meta.get("finish_reason") != "MAX_TOKENS"
    joined = " ".join(record.getMessage() for record in caplog.records)
    assert key not in joined
    assert "x-goog-api-key" not in joined.lower()
    schema = gemini_ask_intent_schema()
    assert "NUMBER" not in str(schema)
