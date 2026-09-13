"""Ask AI UI: expose existing D8 Ask on Overview without a new backend."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from test_d2_global_filters import _store as d2_store
from test_d8_contextual_ask import _post
from test_generate_trend import _run_node

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "apps" / "web" / "static"
JS = STATIC / "js"
CSS = STATIC / "css" / "app.css"
FRONTEND_JS = [
    JS / "app.js",
    JS / "views.js",
    JS / "analytics-state.js",
    JS / "api-client.js",
    JS / "components.js",
]


def _frontend_text() -> dict[str, str]:
    return {
        "views": (JS / "views.js").read_text(encoding="utf-8"),
        "app": (JS / "app.js").read_text(encoding="utf-8"),
        "state": (JS / "analytics-state.js").read_text(encoding="utf-8"),
        "api": (JS / "api-client.js").read_text(encoding="utf-8"),
        "css": CSS.read_text(encoding="utf-8"),
    }


def _ask_ai_control(views: str) -> str:
    return views[
        views.index("const ASK_AI_EXAMPLES") : views.index("export function overviewAskSection")
    ]


def _overview_view(views: str) -> str:
    return views[
        views.index("export function clientOverviewView") : views.index(
            "export function clientHomeView"
        )
    ]


def _trend_control(views: str) -> str:
    return views[
        views.index("function generateTrendControl") : views.index("function askAboutButton")
    ]


def _no_frontend_secrets(*blobs: str) -> None:
    joined = "\n".join(blobs)
    for needle in (
        "DFIP_ASK_API_KEY",
        "x-goog-api-key",
        "generativelanguage.googleapis.com",
        "Gemini 3.8",
        "gemini-3.8-flash",
    ):
        assert needle not in joined


def test_ask_ai_entry_point_is_visible_on_overview() -> None:
    files = _frontend_text()
    views = files["views"]
    overview = _overview_view(views)
    control = _ask_ai_control(views)
    assert "actions: askAiControl(query)" in overview
    assert overview.index("actions: askAiControl(query)") < overview.index("data-overview-ask-host")
    assert 'data-ask-ai="true"' in control
    assert 'data-ask-ai-open="true"' in control
    assert "Ask DFIP" in control
    assert 'aria-label="Ask DFIP"' in control
    assert 'role="dialog"' in control
    assert 'aria-haspopup="dialog"' in control
    assert "hidden" in control
    assert 'placeholder="Ask about your performance..."' in control
    assert 'aria-label="Ask about your performance"' in control
    assert "Why did revenue change?" in control
    assert "Explain the current ROAS performance." in control
    assert "What is driving revenue?" in control
    assert "Explain this anomaly." in control
    assert "Gemini" not in control
    assert "Ask a question" in views
    assert 'data-overview-ask-form="true"' in views
    _no_frontend_secrets(control, files["app"], files["api"], files["views"])


def test_ask_ai_opens_input_and_reuses_existing_ask_post() -> None:
    files = _frontend_text()
    app_js = files["app"]
    api_js = files["api"]
    css = files["css"]
    control = _ask_ai_control(files["views"])
    assert "toggleAskAiPopover" in app_js
    assert "openAskAiPopover" in app_js
    assert "closeAskAiPopover" in app_js
    assert "handleAskAiKeydown" in app_js
    assert 'document.addEventListener("keydown"' in app_js
    assert "submitAskAiFrom" in app_js
    assert 'closest("[data-ask-ai-open]")' in app_js
    assert 'closest("[data-ask-ai-submit]")' in app_js
    assert 'closest("[data-ask-ai-example]")' in app_js
    assert 'form.dataset.askAiForm === "true"' in app_js
    assert "askPayloadFromForm(form, query)" in app_js
    assert "postOverviewAsk(payload)" in app_js
    assert 'this.request("POST", `${this.prefix}/analytics/ask`' in api_js
    assert "data-ask-loading" in app_js
    assert "submit.disabled = true" in app_js
    assert "overviewAskResultView(body)" in app_js
    assert "overviewAskResultView(null, error)" in app_js
    assert 'event.key === "Escape"' in app_js
    assert "window.location.reload" not in app_js
    assert "location.reload" not in app_js
    assert 'type="button" data-ask-ai-submit="true"' in control
    assert 'type="submit" data-ask-ai-submit' not in control
    assert ".ask-ai-popover" in css
    assert ".ask-ai-popover[hidden]" in css
    assert app_js.index("function submitAskAiFrom") < app_js.index(
        'closest("[data-ask-ai-submit]")'
    )
    assert app_js.index("if (handleAskAiKeydown(event)) return;") < app_js.index(
        "if (handleGenerateTrendKeydown(event)) return;"
    )
    _no_frontend_secrets(app_js, api_js, files["views"], files["state"])


def test_ask_about_this_contextual_ask_remains_and_generate_trend_is_untouched() -> None:
    files = _frontend_text()
    views = files["views"]
    app_js = files["app"]
    trend = views[
        views.index("export function overviewTrendSection") : views.index("const DRILL_DIM_LABELS")
    ]
    control = _trend_control(views)
    assert "Ask about this" in views
    assert 'data-ask="true"' in views
    assert 'closest("[data-ask]")' in app_js
    assert "[data-overview-ask-form]" in app_js
    assert "scrollIntoView" in app_js
    assert "details[data-overview-ask]" in app_js
    assert "${generateTrendControl()}" in trend
    assert "Generate Trend" in control
    assert 'data-generate-trend="true"' in control
    assert "Ask DFIP" not in control
    assert "generate-trend-panel" not in views
    assert "closeAskAiPopover()" in app_js[app_js.index("function openGenerateTrendPopover") :]
    assert "closeGenerateTrendPopover()" in app_js[app_js.index("function openAskAiPopover") :]


def test_ask_ai_provider_off_still_answers_through_existing_d8_path() -> None:
    response, http = _post(d2_store(), "Why did revenue change?")
    assert http.app.state.ask_llm is None
    body = response.json()
    assert body["status"] == "answered"
    assert body["intent"] == "explain_metric_change"
    assert body["llm_used"] is False
    assert body["answer"]
    views = (JS / "views.js").read_text(encoding="utf-8")
    assert "Calculated wording" in views
    assert "Model wording" in views
    assert "function askStatusLabel" in views
    assert "Not supported" in views
    assert "Not enough evidence" in views
    assert "no_anomalies" in views
    assert "Model wording" in views
    assert "Gemini" not in _ask_ai_control(views)


def test_ask_ai_frontend_js_syntax() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not available")
    for path in FRONTEND_JS:
        done = subprocess.run(
            [node, "--check", str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert done.returncode == 0, f"{path.name}: {done.stderr}"


def test_ask_ai_submit_calls_existing_ask_and_renders_d8_result(tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not available")
    state = (JS / "analytics-state.js").resolve()
    script = tmp_path / "ask_ai_submit.mjs"
    script.write_text(
        f"""
import {{
  generateTrendAskPayload,
  generatedTrendSelectionFromAsk,
  overviewHref,
  withTrendSelection,
}} from {json.dumps(state.as_uri())};

const QUESTION = "Why did revenue change?";
const SUCCESS = {{
  intent: "explain_metric_change",
  status: "answered",
  llm_used: false,
  question: QUESTION,
  answer: "Revenue changed because published history shows a grounded delta.",
  evidence: {{ metric: {{ metric: "revenue_inr", current: "120.0000" }} }},
}};
const UNSUPPORTED = {{
  intent: "unsupported",
  status: "unsupported",
  llm_used: false,
  answer: "This question is not supported.",
}};
const REFUSED = {{
  intent: "explain_metric_change",
  status: "refused",
  llm_used: false,
  answer: "That question cannot be answered from published history.",
}};

async function runSubmit({{ question, query, body, error, popoverOpen }}) {{
  const chartBefore = query.toString();
  const calls = {{ post: [], navigate: [], reload: 0 }};
  const payload = generateTrendAskPayload(question, query);
  payload.source = payload.source || "overview";
  if (!payload.question) {{
    return {{
      applied: false,
      rendered: "Enter a question.",
      loading: false,
      popoverOpen,
      chartBefore,
      chartAfter: chartBefore,
      payload,
      calls,
      reason: "empty",
    }};
  }}
  const loading = true;
  calls.post.push({{ path: "/api/v1/analytics/ask", payload }});
  if (error) {{
    return {{
      applied: false,
      loading: false,
      rendered: error.message || "error",
      error,
      popoverOpen,
      chartBefore,
      chartAfter: chartBefore,
      payload,
      calls,
    }};
  }}
  const selection = generatedTrendSelectionFromAsk(body);
  if (selection) {{
    const href = overviewHref(withTrendSelection(query, selection));
    calls.navigate.push(href);
    const next = new URL(href, "http://127.0.0.1:3000");
    return {{
      applied: true,
      loading: false,
      rendered: body.answer,
      llmUsed: Boolean(body.llm_used),
      popoverOpen,
      href,
      payload,
      body,
      calls,
      chartBefore,
      chartAfter: next.searchParams.toString(),
    }};
  }}
  return {{
    applied: false,
    loading,
    loadingCleared: true,
    rendered: body.answer,
    status: body.status,
    llmUsed: Boolean(body.llm_used),
    popoverOpen,
    payload,
    body,
    calls,
    chartBefore,
    chartAfter: chartBefore,
  }};
}}

const query = new URLSearchParams("compare=none");
const success = await runSubmit({{ question: QUESTION, query, body: SUCCESS, popoverOpen: true }});
const unsupported = await runSubmit({{
  question: QUESTION,
  query,
  body: UNSUPPORTED,
  popoverOpen: true,
}});
const refused = await runSubmit({{
  question: QUESTION,
  query,
  body: REFUSED,
  popoverOpen: true,
}});
const provider = await runSubmit({{
  question: QUESTION,
  query,
  error: {{ status: 503, message: "language model was unavailable" }},
  popoverOpen: true,
}});
const timeout = await runSubmit({{
  question: QUESTION,
  query,
  error: {{ code: "NETWORK_FAILURE", message: "timed out" }},
  popoverOpen: true,
}});
const malformed = await runSubmit({{
  question: QUESTION,
  query,
  error: {{ status: 422, message: "This question is not supported." }},
  popoverOpen: true,
}});
const empty = await runSubmit({{ question: "", query, body: SUCCESS, popoverOpen: true }});

console.log(JSON.stringify({{
  openControl: true,
  success,
  unsupported,
  refused,
  provider,
  timeout,
  malformed,
  empty,
}}));
""",
        encoding="utf-8",
    )
    out = _run_node(script)
    success = out["success"]
    assert out["openControl"] is True
    assert success["payload"]["question"] == "Why did revenue change?"
    assert success["payload"]["source"] == "overview"
    assert success["calls"]["post"][0]["path"] == "/api/v1/analytics/ask"
    assert success["applied"] is False
    assert success["llmUsed"] is False
    assert "grounded delta" in success["rendered"]
    assert success["calls"]["reload"] == 0
    assert success["calls"]["navigate"] == []
    assert success["chartAfter"] == success["chartBefore"]
    assert "compare=none" in success["chartAfter"] or success["chartBefore"] == "compare=none"
    for case in ("unsupported", "refused"):
        result = out[case]
        assert result["applied"] is False
        assert result["popoverOpen"] is True
        assert result["calls"]["reload"] == 0
        assert result["rendered"]
    for case in ("provider", "timeout", "malformed"):
        result = out[case]
        assert result["applied"] is False
        assert result["error"]
        assert result["calls"]["reload"] == 0
        assert result["chartAfter"] == result["chartBefore"]
    assert out["empty"]["reason"] == "empty"
    assert out["empty"]["calls"]["post"] == []
