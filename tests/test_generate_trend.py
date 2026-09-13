"""Generate Trend: NL selection → existing D3 validators → existing trends contract."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest
from dfip_analytics.ask import classify_intent, parse_trend_request_text
from dfip_analytics.filters import FilterValidationError
from dfip_analytics.trends import parse_generated_trend_selection
from dfip_api.ask_llm import (
    AskLlmError,
    GeminiAskLlm,
    ScriptedAskLlm,
    gemini_trend_selection_schema,
)

from test_d1_kpi_overview import CLIENT_B
from test_d2_global_filters import _store as d2_store
from test_d3_dynamic_trends import TRENDS, _d3_store
from test_d8_contextual_ask import _post
from test_p5_api import CLIENT_ID
from test_p9_authz import jwt_app, jwt_headers


def _trend_json(**overrides) -> str:
    payload = {
        "metric": "revenue_inr",
        "secondary": "none",
        "grain": "day",
        "breakdown": "none",
        "compare": "omit",
    }
    payload.update(overrides)
    return json.dumps(payload)


def test_generate_trend_intent_and_deterministic_selection() -> None:
    decision = classify_intent("Show revenue and ROAS by day for October.", source="overview")
    assert decision.intent == "generate_trend"
    assert classify_intent("Summarize this trend.", source="trend").intent == "summarize_trend"
    assert classify_intent("Why did revenue fall?", source="kpi", metric="revenue_inr").intent == (
        "explain_metric_change"
    )
    selected = parse_trend_request_text("Show revenue by day")
    assert selected["metric"] == "revenue_inr"
    assert selected["grain"] == "day"
    assert selected["secondary"] is None
    pair = parse_trend_request_text("Show revenue and ROAS by day")
    assert pair["metric"] == "revenue_inr"
    assert pair["secondary"] == "overall_roas"
    channel = parse_trend_request_text("Show revenue by channel")
    assert channel["breakdown"] == "channel"
    none = parse_trend_request_text("Show revenue by day with no comparison")
    assert none["compare"] == "none"
    complex_q = (
        "Show revenue and ROAS by campaign monthly, comparing the current period "
        "with the previous period."
    )
    assert classify_intent(complex_q, source="overview").intent == "generate_trend"
    compared = (
        "Show revenue and ROAS by campaign for the current period compared with "
        "the previous period."
    )
    assert classify_intent(compared, source="overview").intent == "generate_trend"


def test_generate_trend_valid_single_metric_grain_provider_off() -> None:
    response, http = _post(_d3_store(), "Show revenue by day")
    assert http.app.state.ask_llm is None
    body = response.json()
    assert body["status"] == "answered"
    assert body["intent"] == "generate_trend"
    assert body["operation"] == "generate_trend"
    assert body["llm_used"] is False
    assert body["evidence"]["selection"]["metric"] == "revenue_inr"
    assert body["evidence"]["selection"]["grain"] == "day"
    assert body["evidence"]["trend"]["point_count"] >= 1
    assert "D3" in body["answer"] or "Trends will show" in body["answer"]


def test_generate_trend_valid_metric_pair_and_compare_none() -> None:
    llm = ScriptedAskLlm(
        text=_trend_json(secondary="overall_roas", compare="none"),
    )
    response, _http = _post(
        _d3_store(),
        "Show revenue and ROAS by day",
        filters={"compare": "none"},
        llm=llm,
    )
    body = response.json()
    assert body["status"] == "answered"
    assert body["llm_used"] is True
    assert body["evidence"]["selection"]["metric"] == "revenue_inr"
    assert body["evidence"]["selection"]["secondary"] == "overall_roas"
    assert body["evidence"]["selection"]["compare"] == "none"
    assert body["applied"]["compare"] == "none"
    assert llm.calls
    assert "EVIDENCE:" not in llm.calls[0][1]
    assert CLIENT_ID not in llm.calls[0][1]


def test_generate_trend_valid_dimension_breakdown() -> None:
    llm = ScriptedAskLlm(text=_trend_json(breakdown="channel", grain="month"))
    response, _http = _post(_d3_store(), "Show revenue by channel", llm=llm)
    body = response.json()
    assert body["status"] == "answered"
    assert body["evidence"]["selection"]["breakdown"] == "channel"
    assert body["evidence"]["selection"]["grain"] == "month"


def test_generate_trend_rejects_unsupported_metric_dimension_grain() -> None:
    for question, needle in (
        ("Show EBITDA by day", "metric"),
        ("Show revenue by store", "dimension"),
        ("Show revenue by hour", "grain"),
    ):
        response, _http = _post(_d3_store(), question)
        body = response.json()
        assert body["status"] == "unsupported", question
        assert body["intent"] == "generate_trend", question
        assert needle in body["answer"].lower() or needle in (body["empty_reason"] or "")


def test_generate_trend_rejects_malformed_and_invalid_structured_output() -> None:
    bad = ScriptedAskLlm(text="{not-json")
    fallback, _http = _post(_d3_store(), "Show revenue by day", llm=bad)
    assert fallback.json()["status"] == "answered"
    assert fallback.json()["llm_used"] is False
    invented = ScriptedAskLlm(text=_trend_json(metric="ebitda"))
    rejected, _http = _post(_d3_store(), "Generate a trend", llm=invented)
    assert rejected.json()["status"] == "unsupported"
    extra = ScriptedAskLlm(text=_trend_json(sql="SELECT 1"))
    blocked, _http = _post(_d3_store(), "Generate a trend", llm=extra)
    assert blocked.json()["status"] == "unsupported"
    combo = parse_generated_trend_selection
    try:
        combo(
            {
                "metric": "revenue_inr",
                "secondary": "overall_roas",
                "grain": "day",
                "breakdown": "channel",
                "compare": "omit",
            }
        )
        raise AssertionError("secondary+breakdown must fail")
    except FilterValidationError:
        pass
    complex_q = (
        "Show revenue and ROAS by campaign monthly, comparing the current period "
        "with the previous period."
    )
    complex_resp, _http = _post(_d3_store(), complex_q)
    complex_body = complex_resp.json()
    assert complex_body["intent"] == "generate_trend"
    assert complex_body["status"] == "unsupported"
    assert "secondary" in complex_body["answer"].lower()
    by_campaign, _http = _post(_d3_store(), "Show revenue by campaign monthly")
    campaign_body = by_campaign.json()
    assert campaign_body["status"] == "answered"
    assert campaign_body["intent"] == "generate_trend"
    assert campaign_body["evidence"]["selection"]["breakdown"] == "campaign_id"
    assert campaign_body["evidence"]["selection"]["grain"] == "month"
    assert campaign_body["evidence"]["selection"].get("compare") != "none"


def test_generate_trend_gemini_unavailable_falls_back_without_inventing() -> None:
    down = ScriptedAskLlm(error=AskLlmError("provider down"))
    ok, _http = _post(_d3_store(), "Show revenue by day", llm=down)
    assert ok.json()["status"] == "answered"
    assert ok.json()["llm_used"] is False
    vague, _http = _post(_d3_store(), "Generate a trend of whatever looks interesting", llm=down)
    assert vague.json()["status"] == "unsupported"
    assert vague.json()["llm_used"] is False


def test_generate_trend_provider_off_makes_zero_gemini_calls(monkeypatch) -> None:
    def boom(*_args, **_kwargs):
        raise AssertionError("provider=none must not call Gemini")

    monkeypatch.setattr("dfip_api.ask_llm.httpx.post", boom)
    response, http = _post(_d3_store(), "Show revenue by day")
    assert http.app.state.ask_llm is None
    assert response.json()["llm_used"] is False
    assert response.json()["status"] == "answered"


def test_generate_trend_refuses_scope_widen_before_gemini() -> None:
    llm = ScriptedAskLlm(text=_trend_json())
    refused, _http = _post(_d3_store(), "Show me another company's revenue by day", llm=llm)
    assert refused.json()["status"] == "refused"
    assert refused.json()["empty_reason"] == "scope_widen"
    assert llm.calls == []
    excel, _http = _post(_d3_store(), "Give me the trend from the raw Excel file", llm=llm)
    assert excel.json()["status"] == "refused"
    assert excel.json()["empty_reason"] == "unpublished"
    assert llm.calls == []
    foreign, _http = _post(_d3_store(), f"Show revenue by day for {CLIENT_B}", llm=llm)
    assert foreign.json()["status"] == "refused"
    assert llm.calls == []


def test_generate_trend_existing_d3_contract_unchanged() -> None:
    http, *_rest = jwt_app(publication_store=_d3_store())
    headers = jwt_headers("client")
    body = http.get(
        TRENDS, headers=headers, params={"metric": "revenue_inr", "grain": "day"}
    ).json()
    assert body["selection"]["metric"] == "revenue_inr"
    assert body["selection"]["grain"] == "day"
    none = http.get(
        TRENDS, headers=headers, params={"metric": "revenue_inr", "grain": "day", "compare": "none"}
    ).json()
    assert none["comparison_shown"] is False
    schema = gemini_trend_selection_schema()
    assert "NUMBER" not in str(schema)
    assert "revenue_inr" in schema["properties"]["metric"]["enum"]
    assert "day" in schema["properties"]["grain"]["enum"]


def test_generate_trend_gemini_schema_is_sent_not_wording_schema(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["json"] = json
        request = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {"content": {"parts": [{"text": _trend_json(secondary="overall_roas")}]}}
                ]
            },
            request=request,
        )

    monkeypatch.setattr("dfip_api.ask_llm.httpx.post", fake_post)
    llm = GeminiAskLlm(
        api_key="test-key-not-real",
        api_base="https://gemini.test/v1beta",
        model="gemini-flash-test",
        timeout_seconds=5,
        max_output_tokens=300,
    )
    response, _http = _post(_d3_store(), "Show revenue and ROAS by day", llm=llm)
    assert response.json()["status"] == "answered"
    assert response.json()["llm_used"] is True
    payload = captured["json"]
    assert isinstance(payload, dict)
    schema = payload["generationConfig"]["responseSchema"]
    assert schema == gemini_trend_selection_schema()
    assert "user_claim" not in schema["properties"]


def test_generate_trend_ui_applies_existing_trend_keys_only() -> None:
    root = Path(__file__).resolve().parents[1] / "apps" / "web" / "static" / "js"
    state = (root / "analytics-state.js").read_text(encoding="utf-8")
    app_js = (root / "app.js").read_text(encoding="utf-8")
    views = (root / "views.js").read_text(encoding="utf-8")
    assert "export function withTrendSelection" in state
    assert "queryFromTrendForm" in state
    assert "withTrendSelection(" in app_js
    assert "data-overview-trend" in views
    assert "generate-trend-panel" not in views
    assert "DFIP_ASK_API_KEY" not in app_js
    d2 = d2_store()
    overview, _http = _post(
        d2, "Why did revenue fall?", source="kpi", focus={"metric": "revenue_inr"}
    )
    assert overview.json()["intent"] == "explain_metric_change"


def test_generate_trend_control_is_visible_on_trends_toolbar() -> None:
    root = Path(__file__).resolve().parents[1] / "apps" / "web" / "static"
    views = (root / "js" / "views.js").read_text(encoding="utf-8")
    app_js = (root / "js" / "app.js").read_text(encoding="utf-8")
    css = (root / "css" / "app.css").read_text(encoding="utf-8")
    trend = views[
        views.index("export function overviewTrendSection") : views.index("const DRILL_DIM_LABELS")
    ]
    control = views[
        views.index("function generateTrendControl") : views.index("function askAboutButton")
    ]
    assert 'data-overview-trend-form="true"' in trend
    assert "${generateTrendControl()}" in trend
    assert trend.index("${generateTrendControl()}") < trend.index("Open in explorer")
    assert 'data-generate-trend="true"' in control
    assert 'data-generate-trend-open="true"' in control
    assert "Generate Trend" in control
    assert "hidden" in control
    assert 'role="dialog"' in control
    assert 'aria-haspopup="dialog"' in control
    assert 'data-generate-trend-form="true"' in control
    assert 'data-generate-trend-input="true"' in control
    assert 'data-generate-trend-submit="true"' in control
    assert 'data-generate-trend-cancel="true"' in control
    assert 'data-generate-trend-status="true"' in control
    assert 'aria-live="polite"' in control
    assert 'placeholder="Show revenue and ROAS by day"' in control
    assert "Gemini" not in control
    assert "DFIP_ASK_API_KEY" not in control
    assert "generate-trend-panel" not in views

    assert "toggleGenerateTrendPopover" in app_js
    assert "closeGenerateTrendPopover" in app_js
    assert "handleGenerateTrendKeydown" in app_js
    assert 'form.dataset.generateTrendForm === "true"' in app_js
    assert "submitGenerateTrendFrom" in app_js
    assert "generateTrendPayloadFromHost" in app_js
    assert 'closest("[data-generate-trend-submit]")' in app_js
    assert 'closest("[data-generate-trend-open]")' in app_js
    assert "askPayloadFromForm(form, query)" in app_js
    assert "generateTrendAskPayload" in app_js
    assert "postOverviewAsk(payload)" in app_js
    assert "applyGeneratedTrendSelection" in app_js
    assert "generatedTrendSelectionFromAsk" in app_js
    assert "withTrendSelection(currentLocation().query, selection)" in app_js
    assert "generateTrendStatusView" in app_js
    assert "data-generate-trend-loading" in app_js
    assert "submit.disabled = true" in app_js
    assert "window.location.reload" not in app_js
    assert "location.reload" not in app_js
    assert "generativelanguage.googleapis.com" not in app_js
    assert "DFIP_ASK_API_KEY" not in app_js
    assert "x-goog-api-key" not in app_js
    assert 'type="button" data-generate-trend-submit="true"' in control
    assert 'type="submit" data-generate-trend-submit' not in control
    assert ".generate-trend-popover" in css
    assert ".generate-trend-popover[hidden]" in css
    assert "data-overview-trend-autosubmit" in trend
    assert app_js.index('closest("[data-generate-trend-submit]")') < app_js.index(
        'const link = event.target.closest("a")'
    )
    assert app_js.index("function submitGenerateTrendFrom") < app_js.index(
        'closest("[data-generate-trend-submit]")'
    )
    assert "refreshOverviewInPlace(query)" in app_js
    assert 'route.name === "client-overview"' in app_js


def _run_node(script: Path) -> dict:
    node = shutil.which("node")
    assert node is not None
    done = subprocess.run([node, str(script)], capture_output=True, text=True, check=True)
    return json.loads(done.stdout)


def test_generate_trend_submit_handler_chain_updates_existing_trend_keys(tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not available")
    state = (
        Path(__file__).resolve().parents[1]
        / "apps"
        / "web"
        / "static"
        / "js"
        / "analytics-state.js"
    )
    script = tmp_path / "generate_trend_submit.mjs"
    script.write_text(
        f"""
import {{
  generateTrendAskPayload,
  generatedTrendSelectionFromAsk,
  overviewHref,
  withTrendSelection,
}} from {json.dumps(state.resolve().as_uri())};

const QUESTION = "Show revenue and ROAS by day";
const SUCCESS = {{
  intent: "generate_trend",
  status: "answered",
  evidence: {{
    selection: {{
      metric: "revenue_inr",
      secondary: "overall_roas",
      grain: "day",
      breakdown: null,
    }},
  }},
}};

async function runSubmit({{ question, query, body, error, popoverOpen }}) {{
  const chartBefore = query.toString();
  const calls = {{ post: [], navigate: [], reload: 0 }};
  const payload = generateTrendAskPayload(question, query);
  if (!payload.question) {{
    return {{
      applied: false,
      popoverOpen,
      chartBefore,
      chartAfter: chartBefore,
      payload,
      calls,
      reason: "empty",
    }};
  }}
  calls.post.push({{ path: "/api/v1/analytics/ask", payload }});
  if (error) {{
    return {{
      applied: false,
      popoverOpen,
      chartBefore,
      chartAfter: chartBefore,
      payload,
      error,
      calls,
    }};
  }}
  const selection = generatedTrendSelectionFromAsk(body);
  if (!selection) {{
    return {{
      applied: false,
      popoverOpen,
      chartBefore,
      chartAfter: chartBefore,
      payload,
      body,
      calls,
    }};
  }}
  const href = overviewHref(withTrendSelection(query, selection));
  calls.navigate.push(href);
  const next = new URL(href, "http://127.0.0.1:3000");
  return {{
    applied: true,
    popoverOpen: false,
    href,
    payload,
    body,
    calls,
    chartBefore,
    chartAfter: next.searchParams.toString(),
    keys: {{
      trend_metric: next.searchParams.get("trend_metric"),
      trend_secondary: next.searchParams.get("trend_secondary"),
      trend_grain: next.searchParams.get("trend_grain"),
      trend_breakdown: next.searchParams.get("trend_breakdown"),
    }},
  }};
}}

const query = new URLSearchParams();
const success = await runSubmit({{
  question: QUESTION,
  query,
  body: SUCCESS,
  popoverOpen: true,
}});
const unsupported = await runSubmit({{
  question: QUESTION,
  query: new URLSearchParams("trend_metric=total_cost"),
  body: {{ intent: "generate_trend", status: "unsupported", evidence: {{}} }},
  popoverOpen: true,
}});
const refused = await runSubmit({{
  question: QUESTION,
  query: new URLSearchParams("trend_metric=total_cost"),
  body: {{ intent: "generate_trend", status: "refused", evidence: {{}} }},
  popoverOpen: true,
}});
const malformed = await runSubmit({{
  question: QUESTION,
  query: new URLSearchParams("trend_metric=total_cost"),
  body: {{ intent: "generate_trend", status: "answered", evidence: {{}} }},
  popoverOpen: true,
}});
const provider = await runSubmit({{
  question: QUESTION,
  query: new URLSearchParams("trend_metric=total_cost"),
  error: {{ status: 503, message: "language model was unavailable" }},
  popoverOpen: true,
}});
const timeout = await runSubmit({{
  question: QUESTION,
  query: new URLSearchParams("trend_metric=total_cost"),
  error: {{ code: "NETWORK_FAILURE", message: "timed out" }},
  popoverOpen: true,
}});
const openPayload = generateTrendAskPayload("", query);

console.log(JSON.stringify({{
  openControl: true,
  success,
  unsupported,
  refused,
  malformed,
  provider,
  timeout,
  openPayload,
}}));
""",
        encoding="utf-8",
    )
    out = _run_node(script)
    success = out["success"]
    assert out["openControl"] is True
    assert success["payload"]["question"] == "Show revenue and ROAS by day"
    assert success["payload"]["source"] == "overview"
    assert success["calls"]["post"][0]["path"] == "/api/v1/analytics/ask"
    assert success["applied"] is True
    assert success["popoverOpen"] is False
    assert success["calls"]["reload"] == 0
    assert success["href"].startswith("/client/overview?")
    assert success["keys"]["trend_metric"] == "revenue_inr"
    assert success["keys"]["trend_secondary"] == "overall_roas"
    assert success["keys"]["trend_grain"] is None
    assert success["keys"]["trend_breakdown"] is None
    assert "trend_metric=revenue_inr" in success["href"]
    assert "trend_secondary=overall_roas" in success["href"]
    assert success["chartAfter"] != success["chartBefore"]
    for case in ("unsupported", "refused", "malformed", "provider", "timeout"):
        result = out[case]
        assert result["applied"] is False
        assert result["popoverOpen"] is True
        assert result["chartAfter"] == result["chartBefore"]
        assert result["calls"]["navigate"] == []
        assert result["calls"]["reload"] == 0
    assert out["openPayload"]["question"] == ""
    assert out["openPayload"]["source"] == "overview"
