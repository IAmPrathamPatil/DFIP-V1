# D8 — Contextual Ask

D8 adds a contextual analytical assistant on `/client/overview`. The user asks a natural-language question about the **current** Overview state. The server maps that question to an allowlisted D1–D7 operation, runs the existing contract under JWT company scope, then explains the structured result.

The language model is **not** the calculation source of truth. It does not generate SQL, access the database, invent KPIs, modify data, or widen company scope. When no model is configured, Ask still answers from the same calculated evidence using a template.

D9 Advanced Analytics Workspace integrates this Ask surface without changing D8 contracts. Saved analysis stores Overview state, not prompts. Sharing tokens and extra agents are **not** implemented.

## Architecture

```
User question + approved Overview context
  → refuse unsafe text (injection, SQL, scope, unpublished, mutate, secrets)
  → deterministic intent (allowlisted operation names only)
  → validate operation + parameters
  → existing D1–D7 AnalyticsService contract (JWT tenant)
  → compact structured evidence
  → template wording, optionally LLM wording of that evidence
  → numerical / causal / UUID validation
  → grounded answer or safe fallback
```

Intent classification is **deterministic**. The model never names operations, never chooses SQL, and never receives raw fact rows or filter option catalogs.

### Files

**New**

- `packages/analytics/dfip_analytics/ask.py` — intents, refusals, aliases, template, number validation, operation registry
- `packages/api/dfip_api/ask_service.py` — orchestration
- `packages/api/dfip_api/ask_llm.py` — `AskLlm` protocol, `HttpAskLlm`, `GeminiAskLlm`, `ScriptedAskLlm`, `build_ask_llm`
- `tests/test_d8_contextual_ask.py`, `tests/test_ask_llm.py`, `tests/test_generate_trend.py`
- `documentation/D8_CONTEXTUAL_ASK.md` (this file)

**Updated**

- `analytics_routes.py` (`POST /api/v1/analytics/ask`), `schemas.py` (`AskRequest` / `AskResponse`)
- `app.py` (`application.state.ask_llm`)
- `dfip_config/settings.py`, `.env.example`
- `dfip_analytics/__init__.py`
- web/API clients, `analytics-state.js`, `app.js`, `views.js`, `app.css`
- D5–D7 documentation pointers

Unchanged by design: `/admin` Dashboard, `/client` Reports, D1–D7 query contracts, metric/dimension registries, Excel.

## Query contract

`POST /api/v1/analytics/ask`

JSON body (`extra=forbid`):

| Field | Notes |
| --- | --- |
| `question` | Required. Max `DFIP_ASK_MAX_QUESTION_CHARS` (default 500). |
| `source` | `overview` \| `kpi` \| `trend` \| `explorer` \| `insight` \| `anomaly` \| `drill` |
| `filters` | D2 period/comparison/dimension state. Same fields as Overview. |
| `focus` | Allowlisted object: metric, dimension, insight/anomaly id, trend/explorer/drill keys |

Company scope is from the JWT. Body `filters.client_id` cannot widen a bound token (403). The browser **does not** send `client_id` (`askFiltersFromQuery` deletes it). Frontend company pickers are not the security boundary.

Response:

- `status`: `answered` \| `unsupported` \| `refused`
- `intent`, `operation`
- `answer`, `caveats`, `next_action`
- `evidence` (compact structured result)
- `applied` (echo of D2 state)
- `llm_used`
- `timings`: `intent_ms`, `query_ms`, `llm_ms`, `total_ms`

401 unauthenticated. 403 widen / unbound publisher. 422 unknown metric/dimension/source. 503 store failure. Refusals and unsupported questions are **200** with `status` set — they are controlled answers, not server errors.

## Intent registry

`classify_intent` in `ask.py` maps language to one of:

| Intent / operation | D1–D7 contract |
| --- | --- |
| `explain_metric_change` | Overview KPIs |
| `compare_periods` | Overview KPIs (largest movers) |
| `identify_driver` | Insights (`dominant_driver`) |
| `compare_dimensions` | Explorer (named groups, e.g. SMS vs WhatsApp) |
| `summarize_trend` | Trends (first/last/count, not every point) |
| `generate_trend` | Trends selection translator, then existing `GET /analytics/trends` |
| `explain_explorer_result` | Explorer top rows |
| `explain_insight` | Insights by `insight_id` or first card |
| `explain_anomaly` | Anomalies by `anomaly_id` or first card |
| `explain_drilldown` | Drilldown rows |
| `summarize_current_context` | Overview KPI snapshot |

`apply_operation_spec` then enforces the `OPERATIONS` registry as the execution allowlist: known operation name, allowed metrics, allowed dimensions, comparison requirement, and (for `compare_dimensions`) two named groups. `AskService._run` loads the same spec (`operation_spec`) and will not execute an operation that is not in the registry. `compare_dimensions` does not allow the `day` dimension. Extra dimensions on overview-only operations are stripped rather than executed. Unknown operation names become `unsupported_operation`.

Unknown language → `unsupported` / `unsupported_intent`. The model cannot invent operation names. `source` from “Ask about this” selects the matching operation when the text is generic (“Explain this anomaly.”).

KPI focus: “Ask about this” on a KPI sends `source=kpi` and `focus.metric`. A question that does not name the metric (for example “Did SMS cause the decline?”) still uses that validated focus metric. Completely unrelated text (haiku/poem) stays `unsupported`. A causal question with no focus and no metric name stays `unsupported`, with the causal caveat attached.

Metric aliases (revenue → `revenue_inr`, spend → `total_cost`, ROAS → `overall_roas`, …) are allowlisted. Unknown metrics/dimensions are 422 when sent in `focus`, or `unsupported_metric` / `unsupported_dimension` when they appear only in prose or fail the registry.

Group extraction for “Compare A with B” strips trailing metric phrases (`for revenue`) and, when both names are channel values, forces dimension `channel` even if explorer was on `campaign_id`. Explorer matching is exact key, then exact label, then case/whitespace-normalized equality — not substring matching.

## Context contract

The Ask payload contains **approved state only**:

- JWT company
- D2 period, comparison, channel/campaign/Filter Logic 1
- current metric/dimension
- selected insight/anomaly id
- trend grain/breakdown, explorer mode, drill parents

It does **not** send:

- raw `publication_history_grain` rows
- Overview `options` catalogs (campaign lists)
- other companies’ ids or KPIs
- secrets, SQL, or unpublished/working-set data

Evidence returned to the UI and (optionally) to the model is a compact subset: period labels, current/comparison/delta, a few group rows, insight/anomaly headline + driver share, active filters.

## Grounding and LLM role

1. The server calculates using the same engines as D1–D7.
2. `template_answer` always produces a grounded sentence from that evidence.
3. If a wording provider is configured and `DFIP_ASK_API_KEY` is set, the server requests short wording of the **already calculated** evidence (`temperature=0`, bounded output tokens). `openai` uses `HttpAskLlm` (chat completions). `gemini` uses `GeminiAskLlm` (native `generateContent` with enum-only JSON `responseSchema`; no numeric schema fields). DFIP renders Gemini JSON through `coerce_llm_wording` by inserting evidence numbers; Gemini does not classify intents, choose operations, or calculate analytics. Free-text wording is still accepted from other providers and still must cite evidence numbers with `is` rather than `was`.
4. `validate_llm_answer` checks length, forbids causal verbs and SQL, requires every number (except years 202x–203x) to appear in evidence, and rejects foreign UUIDs. Derived figures such as `120 − 25 = 95` are rejected. It does not require a specific four-decimal spelling (`120` and `120.0000` are the same evidence value). Wording providers must use only numbers that appear directly in evidence and must include the focused metric current value.
5. On validation failure, timeout, or HTTP error, the API returns the template (`llm_used=false`) plus a note when the model was unavailable.

Default local/demo configuration is `DFIP_ASK_PROVIDER=none`: **no model call**. That is intentional, not a missing feature. OpenAI and Gemini are optional wording providers only. DFIP remains the source of truth.

The API key stays on the API process. It is never written into frontend JS. Do not enable Gemini against real client data until the project privacy/data policy allows it. Free Gemini tier is for development / low-risk testing only.

## Security model

- Tenant = `principal.client_id`. Client users are bound at login. Publishers use the **selected** authorized company. Unbound publisher is 403.
- Changing URL `client_id`, body `filters.client_id`, prompt text, or operation parameters cannot read another company.
- Historical facts, insights, and anomalies are loaded only through the scoped `AnalyticsService` methods.
- D4/D5 selected keys are labels/ids from that tenant’s published history after D2 filters.

## Prompt-injection defenses

User text is untrusted. Classification and refusals run **before** any model call.

Refused (`status=refused`, no LLM):

| Reason | Examples |
| --- | --- |
| `prompt_injection` | Ignore previous rules; pretend I am an admin; bypass auth |
| `sql_request` | SELECT … FROM; execute SQL; UNION SELECT |
| `scope_widen` | another company; Company B; foreign UUID in the question |
| `unpublished` | working-set / unpublished revenue |
| `mutate` | delete/modify published facts |
| `secrets` | API key, password, DATABASE_URL |

The model cannot override system instructions, the operation allowlist, metric definitions, or database permissions because it never receives those controls as user-writable policy — only evidence to word.

## Numerical validation

Every number in a model answer must belong to the **entity it is attached to** in the evidence, not merely appear somewhere in the JSON.

Validation walks structured evidence into labeled entities (the primary metric, KPI cards, named compare groups, explorer/drill rows, drivers, insight/anomaly). For each number in the wording:

- If a known metric name appears that is **not** in this evidence, the wording is rejected (cross-metric swap).
- `is` / `current` / `to` bind to that entity’s current value; `was` / `from` / `comparison` / `baseline` bind to its comparison/baseline; `(current vs comparison)` keeps that order; `from X to Y` is comparison then current.
- Invented values such as `999.9%` discard the model text. Foreign company UUIDs in the wording are rejected the same way.

Years `202x–203x` and date fragments that only exist on the period/comparison labels are allowed without being treated as KPI values.

### Percentage wording

Internal `delta_pct` remains a signed ratio (for example `-0.4540`). Human-readable answers may use the absolute percent when direction is stated separately:

- valid: `declined 45.4%`, `fell 45.4%`, `decreased 45.4%`, or signed `-45.4%`
- invalid: `increased 45.4%` when the signed delta is negative

The deterministic template uses the unsigned percent with `declined` / `increased`. That template sentence must pass the same validator.

### Compare-dimension evidence

`compare_dimensions` does **not** write the second group into `metric.comparison`. That field remains a period comparison/baseline everywhere else. Compared groups are `evidence.groups` plus `group_a` / `group_b` (key, label, current, period comparison, delta). The Overview evidence panel labels those groups by name and never as “Comparison / baseline”.

## Causal-language policy

D1–D7 contracts measure contribution, association, and coinciding movement. They do not implement causal inference.

Ask therefore:

- prefers *contributed*, *accounted for*, *associated with*, *coincided with*
- treats *cause / caused / causes / causing / causal / because / responsible for / resulted in* as causal language
- attaches a caveat that share-of-change is not causal proof, and still uses the focused KPI when “Ask about this” supplied one
- rejects model text containing those causal constructions
- does not run a causal model; a causal question never silently becomes an ordinary metric explanation without the caveat

## Unsupported-request policy

| Request | Result |
| --- | --- |
| Unsupported metric/dimension | 422 or `unsupported` |
| Arbitrary SQL / database | `refused` / `sql_request` |
| Other company’s data | `refused` / `scope_widen` or 403 |
| Unpublished / working set | `refused` / `unpublished` |
| Missing comparison when required | `unsupported` / `missing_comparison` |
| Too little calculated evidence | `unsupported` / `insufficient_evidence` |
| Anomaly on a 2-month tenant | `unsupported` / `insufficient_history` (same as D7) |
| Haiku / unrelated text | `unsupported` / `unsupported_intent` |

The answer explains that Ask can analyze Overview KPIs, trends, explorer, drilldown, insights, and anomalies for **this** company from published history.

## UI

One Ask implementation on Overview:

- Panel `data-overview-ask` after Anomalies (before an open drill panel)
- “Ask about this” on KPI, trend, explorer, insight, anomaly, and drill (`data-ask`)
- Context line: company, period, comparison, active D2 filters
- States: empty, loading (`data-ask-loading`), HTTP error, 422, grounded / refused / unsupported article with evidence

Clicking “Ask about this” fills hidden source/focus fields and scrolls to the panel. Submit posts `POST /analytics/ask` with current URL filters. It does not change KPI/trend/explorer/insight/anomaly queries.

No saved prompts, no export, no extra agents.

## Performance

Timings are returned on every response.

| Stage | Typical (in-memory TestClient, no model) | Live Postgres demo (no model) |
| --- | --- | --- |
| Intent | sub-millisecond regex/allowlist | 0.1–1.1 ms |
| Analytical query | same order as the reused D1–D7 GET | ~200–670 ms (KPI/driver); ~0 ms on refusals |
| LLM | `0` when provider is `none`; otherwise bounded by `DFIP_ASK_TIMEOUT_SECONDS` (20) | `0.0` (`DFIP_ASK_PROVIDER=none`) |
| Total | intent + query + optional model | ~0.1 ms refuse; ~210–670 ms answered |

Ask does not add warehouse tables or extra SQL indexes. Slow analysis is the existing aggregate, not a dump of history to the browser or the model. The Overview panel shows a loading skeleton while the POST is in flight.

## Cost controls

- No model call for refusals, unsupported intent, 401/403/422, or `provider=none`
- Template is sufficient and is the local-demo default
- Evidence is compact (max 8 group rows, 20 filter values, no option catalogs, no fact rows)
- Question cap 500 characters; output cap 4096 tokens (Gemini 3.x thinking shares this budget; 300 truncates visible wording); temperature 0
- One completion per successful answered question; validation failure does not retry the model

**Required before enabling an external LLM provider in production:** per-principal rate limiting and a completion budget on `POST /analytics/ask` (review finding M4). That control is not implemented in D8 and is not deferred to D9 as a product feature — it is a production prerequisite for turning `DFIP_ASK_PROVIDER` away from `none`.

Expected usage: Overview users ask a handful of questions per session against already-loaded context. Cost scales with optional OpenAI or Gemini wording only, not with KPI calculation.

## Tests

`tests/test_d8_contextual_ask.py` and `tests/test_ask_llm.py` cover:

1. Supported intent helpers
2. Unsupported intent (haiku)
3. KPI explanation
4. Driver question
5. Comparison question
6. Dimension comparison (SMS vs WhatsApp)
7. Trend summary
8. Insight explanation (including `insight_id`)
9. Anomaly explanation (`_history_store`)
10. Drilldown explanation
11. Inherited D2 channel filter (SMS revenue `40.0000`)
12. Inherited D3 trend focus
13. Inherited D4 drill focus
14. Inherited D5 explorer focus
15. Tenant isolation (`999.0000` only on company B)
16. Body `client_id` widen → 403
17. Prompt injection
18. Arbitrary SQL
19. Unsupported metric
20. Unsupported dimension (422)
21. Causal wording not claimed
22. Unpublished-data request
23. Missing comparison (`compare=none`)
24. Insufficient evidence (unknown groups)
25. LLM failure → template fallback (including Gemini timeout / 4xx / 5xx / malformed payload)
26. Analytical query failure → 503
27. Malformed / too-short model output
28. Invented numbers and foreign UUID rejected
29–35. D1–D7 GETs unchanged after Ask; SPA Ask present; Dashboard/Reports nav unchanged; no saved workspace; no `DFIP_ASK_API_KEY` in frontend

## Known limitations

- Intent is keyword/allowlist based. Paraphrases outside the patterns return `unsupported_intent` rather than guessing.
- “Why did revenue fall?” against a period where revenue **rose** reports the calculated rise. Ask does not invent a decline to match the wording.
- Most local demo companies have two published months. Anomaly Ask on those tenants correctly returns `insufficient_history`.
- Default deploy uses template wording (`DFIP_ASK_PROVIDER=none`). OpenAI and Gemini are optional wording only; they do not classify intents or calculate analytics.
- Numerical validation is entity/role based. It cannot prove every qualitative adjective.
- No causal model exists; causal verbs are blocked rather than estimated.
- Ask does not search unpublished processing runs, Excel-only fields, or companies the token cannot see.
- Enabling a provider in production requires the M4 rate/completion budget described under Cost controls.

## D9 integration

D9 Advanced Analytics Workspace is complete. Ask remains the only AI layer.
Saved analysis stores Overview configuration, not Ask answers or fact rows.
See `documentation/D9_ADVANCED_ANALYTICS_WORKSPACE.md`.

## Verification (this implementation)

- Pytest: `tests/test_d8_contextual_ask.py` (6 tests) plus D1–D7 and `test_p6_web` regression
- Live `POST /api/v1/analytics/ask` after API restart; OpenAPI lists the route
- `demo-client` (company Testing): KPI explanation, SMS-filtered KPI (different current value), driver, SQL refuse, injection refuse, haiku unsupported, anomaly `insufficient_history`
- `demo-publisher`: unbound Ask is 403; after `select-client` to an authorized company, Ask returns that company’s KPIs
- Browser (`publisher@123`, company Pratham Patil): Overview Ask panel, “Ask about this” on a KPI (grounded Total Cost vs Sep-25), unsupported haiku, SMS filter changes context and the grounded Total Cost, Dashboard and Reports unchanged
- Client Overview click-path was not completed in-browser (new tab required sign-in; passwords are not placed in the browser tool). Live API covers that tenant.
- Default wording is the template (`llm_used=false`). OpenAI and Gemini are optional wording providers.

## AI-01b live Gemini wording (opt-in)

A real Gemini `generateContent` call is **not** part of default pytest. Repo default remains `DFIP_ASK_PROVIDER=none`.

To run the synthetic live acceptance (`tests/test_ask_llm_live.py`):

1. Set `DFIP_ASK_LIVE=1` in the process environment.
2. Set `DFIP_ASK_API_KEY` in the process environment or a gitignored local secret file you export yourself. Never commit the key.
3. Set `DFIP_ASK_MODEL` to a Gemini API model id (not a Cursor IDE model name). If unset, the live test uses `gemini-2.5-flash`.
4. Leave `DFIP_ASK_API_BASE` unset so the factory selects the native Gemini default.
5. Evidence is the in-memory D8 synthetic store only. Do not point this at production PublishedFacts or real client files.

The live harness retries transient Gemini HTTP 5xx (including 503) up to three attempts with short backoff. It does not retry 400/401/403/404. Persistent 5xx is reported as BLOCKED/UNAVAILABLE, not as a passing integration. Production `GeminiAskLlm` still fails closed to `template_answer` (`llm_used=false`) with no retry. A successful live answer must cite the focused metric current value in any evidence-allowed numeric form; the exact spelling `120.0000` is not required. Gemini 3.x thinking tokens share `DFIP_ASK_MAX_OUTPUT_TOKENS` (default 4096). A 300-token cap truncates visible wording before the current value is cited.

Free Gemini tier is for development / low-risk testing until the privacy/data policy approves sending real client data.
