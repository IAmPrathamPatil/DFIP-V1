"""D8 Contextual Ask: allowlisted intents and grounded explanation helpers.

The LLM never selects SQL, metrics, or tenants. Intent is classified with
deterministic rules first. When a provider is enabled, leftover unsupported
overview questions may receive a bounded operation suggestion that DFIP
validates against the existing registry. Operations call existing D1–D7
contracts. Explanations may be templated or LLM-worded from structured
evidence only.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any, Literal

from dfip_analytics.divide import as_decimal
from dfip_analytics.explorer import EXPLORER_DIMENSIONS_BY_KEY
from dfip_analytics.filters import FilterValidationError
from dfip_analytics.trends import (
    TREND_DIMENSIONS_BY_KEY,
    TREND_METRICS_BY_KEY,
    parse_generated_trend_selection,
    parse_trend_metric,
)

AskSource = Literal[
    "overview",
    "kpi",
    "trend",
    "explorer",
    "insight",
    "anomaly",
    "drill",
]
AskIntent = Literal[
    "explain_metric_change",
    "compare_periods",
    "identify_driver",
    "compare_dimensions",
    "summarize_trend",
    "generate_trend",
    "explain_explorer_result",
    "explain_insight",
    "explain_anomaly",
    "explain_drilldown",
    "summarize_current_context",
    "unsupported",
    "refused",
]
AskStatus = Literal["answered", "unsupported", "refused"]

ASK_SOURCES: tuple[AskSource, ...] = (
    "overview",
    "kpi",
    "trend",
    "explorer",
    "insight",
    "anomaly",
    "drill",
)
ASK_INTENTS: tuple[str, ...] = (
    "explain_metric_change",
    "compare_periods",
    "identify_driver",
    "compare_dimensions",
    "summarize_trend",
    "generate_trend",
    "explain_explorer_result",
    "explain_insight",
    "explain_anomaly",
    "explain_drilldown",
    "summarize_current_context",
)
MAX_QUESTION_CHARS = 500
MAX_EVIDENCE_ROWS = 8
MAX_FILTER_VALUES = 20

METRIC_ALIASES: tuple[tuple[str, str], ...] = (
    ("overall roas", "overall_roas"),
    ("delivery rate", "delivery_rate"),
    ("unique conversions", "unique_conversions"),
    ("unique clicks", "unique_clicks"),
    ("total cost", "total_cost"),
    ("revenue", "revenue_inr"),
    ("roas", "overall_roas"),
    ("clicks", "unique_clicks"),
    ("conversions", "unique_conversions"),
    ("delivered", "delivered"),
    ("ctr", "ctr_del_to_clicks"),
    ("cost", "total_cost"),
    ("spend", "total_cost"),
)
CHANNEL_HINTS = frozenset({"email", "push", "rcs", "sms", "whatsapp", "(blank)"})
CAUSAL_CAVEAT = (
    "Contribution and association are not causal proof. "
    "Ask reports observed movement and share of change only."
)
CAUSAL_FORBIDDEN = (
    " caused ",
    " causing ",
    " causes ",
    " cause the ",
    " caused the ",
    " causal ",
    " because ",
    " resulted in ",
    " was responsible for ",
    " responsible for the ",
)
CAUSAL_QUESTION_RE = re.compile(
    r"\b(cause[ds]?|causing|causal|because|responsible for|resulted in)\b",
    re.IGNORECASE,
)
UNRELATED_ASK_RE = re.compile(r"\b(haiku|poem|joke|lyrics|song|riddle)\b", re.IGNORECASE)
KPI_FOCUS_RE = re.compile(
    r"\b(why|how|did|does|do|is|was|were|explain|summarize|fall|fell|drop|decline|"
    r"change|cause|causing|causal|because|responsible|increase|improve|what|which|"
    r"this|it)\b",
    re.IGNORECASE,
)
CURRENT_CONTEXT_RE = re.compile(
    r"\b("
    r"summarize|"
    r"what am i looking at|"
    r"current context|"
    r"how are we doing|"
    r"(this|the|current) period|"
    r"what (is|was|are|were)|"
    r"how much (is|was|are|were)"
    r")\b",
    re.IGNORECASE,
)
GENERATE_TREND_RE = re.compile(
    r"\b((generate|build|make)\s+(a\s+)?trend|"
    r"(show|plot|chart|graph|display)\b.+\b(by\s+\w+|over time|time series|trend))\b",
    re.IGNORECASE | re.DOTALL,
)
UNSUPPORTED_TREND_METRIC_RE = re.compile(
    r"\b(ebitda|profit efficiency|acos|cpc|orders|custom formula)\b",
    re.IGNORECASE,
)
UNSUPPORTED_TREND_DIMENSION_RE = re.compile(
    r"\b(store|asin|brand|warehouse)\b",
    re.IGNORECASE,
)
UNSUPPORTED_TREND_GRAIN_RE = re.compile(
    r"\b(by hour|hourly|by quarter|quarterly|by year|yearly)\b",
    re.IGNORECASE,
)
TREND_DIMENSION_ALIASES: tuple[tuple[str, str], ...] = (
    ("filter logic 1 group", "filter_logic_1_group"),
    ("filter logic 1", "filter_logic_1"),
    ("campaign", "campaign_id"),
    ("channel", "channel"),
)
CURRENT_CONTEXT_CHANGE_RE = re.compile(
    r"\b(why|change|changed|fell|fall|drop|dropped|decline|declined|"
    r"increase|increased|improve|improved|versus|comparison|drove|driver|"
    r"driving|drives|highest|lowest|best|worst)\b",
    re.IGNORECASE,
)
DECLINE_WORD_RE = re.compile(
    r"\b(declined|fell|fall|dropped|decreased|down)\b",
    re.IGNORECASE,
)
INCREASE_WORD_RE = re.compile(
    r"\b(increased|rose|grew|improved)\b",
    re.IGNORECASE,
)
COMPARE_NON_DAY_DIMENSIONS = tuple(key for key in EXPLORER_DIMENSIONS_BY_KEY if key != "day")
INJECTION_RE = re.compile(
    r"(ignore (the )?(previous|prior|above|all) (rules|instructions|filters|security))"
    r"|(ignore the company filter)"
    r"|(pretend (that )?i (am|'m) (an? )?(admin|publisher|owner))"
    r"|(you are now )"
    r"|(bypass (auth|authorization|rls|scope|tenant))"
    r"|(developer mode)",
    re.IGNORECASE,
)
SQL_RE = re.compile(
    r"\b(select\b.+\bfrom\b|insert into|update\b.+\bset\b|delete from|drop table|"
    r"alter table|execute sql|run this sql|union select)\b",
    re.IGNORECASE | re.DOTALL,
)
SCOPE_RE = re.compile(
    r"(another company|other company|company b\b|all companies|every tenant|"
    r"other tenant|show me .* company)",
    re.IGNORECASE,
)
UNPUBLISHED_RE = re.compile(
    r"(unpublished|working[- ]set|staging rows?|processing run facts|"
    r"before (it was )?published|raw excel|uploaded excel)",
    re.IGNORECASE,
)
MUTATE_RE = re.compile(
    r"\b(delete|modify|update|overwrite|change) (the )?(published|facts|data|kpis)\b",
    re.IGNORECASE,
)
SECRETS_RE = re.compile(
    r"(api key|jwt secret|password|credentials|connection string|database url)",
    re.IGNORECASE,
)
UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
COMPARE_RE = re.compile(
    r"(?:compare\s+)?(?P<a>[\w .'|()+-]+?)\s+(?:with|vs\.?|versus)\s+(?P<b>[\w .'|()+-]+)",
    re.IGNORECASE,
)
COMPARE_SKIP_RE = re.compile(
    r"^(what|which|why|how|the comparison|this month|last month|"
    r"prior period|previous period|current period|the current period|"
    r"the previous period|the prior period|comparing the current period)\b",
    re.IGNORECASE,
)
PERIOD_COMPARE_GROUP_RE = re.compile(
    r"\b(current period|previous period|prior period|last month|this month|"
    r"comparison period|last two months)\b",
    re.IGNORECASE,
)
RANK_PERIOD_RE = re.compile(
    r"\b(best|worst|highest|lowest|top|most|least)\b.{0,48}\b(month|months|period|week|weeks)\b"
    r"|\b(month|months|period)\b.{0,48}\b(best|worst|highest|lowest|most|least)\b",
    re.IGNORECASE | re.DOTALL,
)
RANK_VALUE_RE = re.compile(
    r"\b(highest|lowest|most|least|best|worst|top|bottom|generated the most)\b",
    re.IGNORECASE,
)
UNUSUAL_RE = re.compile(
    r"\b(anomal\w*|unusual|outlier|unexpected|abnormal)\b",
    re.IGNORECASE,
)
CONTRIBUTION_TO_CHANGE_RE = re.compile(
    r"\b("
    r"contribut\w*.{0,48}\b(change|delta|movement|decline|increase|drop)\b|"
    r"(change|delta|movement|decline|increase).{0,48}\bcontribut\w*|"
    r"share of (the )?(observed )?change|"
    r"drivers? of (this |the )?(change|movement)"
    r")\b",
    re.IGNORECASE | re.DOTALL,
)
DRIVER_RE = re.compile(
    r"\b(drivers?|drove|driving|drives|accounted|"
    r"which campaign (drove|caused)|"
    r"what (is |was |are )?(driving|drove|changed most)|"
    r"biggest (negative )?mover)\b",
    re.IGNORECASE,
)
NAMED_METRIC_CHANGE_RE = re.compile(r"\bwhat changed\b", re.IGNORECASE)
CURRENT_PERFORMANCE_RE = re.compile(
    r"\b("
    r"explain (the )?(current|this period)|"
    r"current .{0,40}performance|"
    r"how (is|are|was|were) .{0,40}(doing|performing)|"
    r"this period.{0,40}performance"
    r")\b",
    re.IGNORECASE,
)
PERIOD_RANK_COPY = (
    "Ask cannot rank months or periods. There is no period-ranking operation. "
    "Use Generate Trend with a monthly grain to inspect the metric over time."
)
ASK_INTENT_KEYS = frozenset({"operation", "metric", "dimension"})
NUMBER_RE = re.compile(r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+\.\d+|-?\d+")

UNSUPPORTED_COPY = (
    "I can explain Overview KPIs, trends, explorer rankings, drilldown slices, "
    "deterministic insights, and anomalies for this company using published history. "
    "I cannot run SQL, change data, or analyze unsupported metrics or dimensions."
)
REFUSAL_COPY = {
    "prompt_injection": "The question cannot override security or analysis rules.",
    "sql_request": "Ask cannot execute SQL or query the database directly.",
    "scope_widen": "Ask can only use the authenticated company. Other companies are not available.",
    "unpublished": (
        "Ask uses published history only. Unpublished and working-set data are not available."
    ),
    "mutate": "Ask is read-only. It cannot modify published facts or other data.",
    "secrets": "Ask cannot provide credentials, secrets, or system internals.",
}

OPERATIONS: dict[str, dict[str, Any]] = {
    "explain_metric_change": {
        "intent": "explain_metric_change",
        "contract": "GET /analytics/overview",
        "requires_comparison": True,
        "requires_groups": False,
        "allowed_metrics": tuple(TREND_METRICS_BY_KEY),
        "allowed_dimensions": (),
    },
    "compare_periods": {
        "intent": "compare_periods",
        "contract": "GET /analytics/overview",
        "requires_comparison": True,
        "requires_groups": False,
        "allowed_metrics": tuple(TREND_METRICS_BY_KEY),
        "allowed_dimensions": (),
    },
    "identify_driver": {
        "intent": "identify_driver",
        "contract": "GET /analytics/insights",
        "requires_comparison": True,
        "requires_groups": False,
        "allowed_metrics": tuple(TREND_METRICS_BY_KEY),
        "allowed_dimensions": tuple(EXPLORER_DIMENSIONS_BY_KEY),
    },
    "compare_dimensions": {
        "intent": "compare_dimensions",
        "contract": "GET /analytics/explorer",
        "requires_comparison": False,
        "requires_groups": True,
        "allowed_metrics": tuple(TREND_METRICS_BY_KEY),
        "allowed_dimensions": COMPARE_NON_DAY_DIMENSIONS,
    },
    "summarize_trend": {
        "intent": "summarize_trend",
        "contract": "GET /analytics/trends",
        "requires_comparison": False,
        "requires_groups": False,
        "allowed_metrics": tuple(TREND_METRICS_BY_KEY),
        "allowed_dimensions": tuple(EXPLORER_DIMENSIONS_BY_KEY),
    },
    "generate_trend": {
        "intent": "generate_trend",
        "contract": "GET /analytics/trends",
        "requires_comparison": False,
        "requires_groups": False,
        "allowed_metrics": tuple(TREND_METRICS_BY_KEY),
        "allowed_dimensions": tuple(TREND_DIMENSIONS_BY_KEY),
    },
    "explain_explorer_result": {
        "intent": "explain_explorer_result",
        "contract": "GET /analytics/explorer",
        "requires_comparison": False,
        "requires_groups": False,
        "allowed_metrics": tuple(TREND_METRICS_BY_KEY),
        "allowed_dimensions": tuple(EXPLORER_DIMENSIONS_BY_KEY),
    },
    "explain_insight": {
        "intent": "explain_insight",
        "contract": "GET /analytics/insights",
        "requires_comparison": True,
        "requires_groups": False,
        "allowed_metrics": tuple(TREND_METRICS_BY_KEY),
        "allowed_dimensions": tuple(EXPLORER_DIMENSIONS_BY_KEY),
    },
    "explain_anomaly": {
        "intent": "explain_anomaly",
        "contract": "GET /analytics/anomalies",
        "requires_comparison": False,
        "requires_groups": False,
        "allowed_metrics": tuple(TREND_METRICS_BY_KEY),
        "allowed_dimensions": tuple(EXPLORER_DIMENSIONS_BY_KEY),
    },
    "explain_drilldown": {
        "intent": "explain_drilldown",
        "contract": "GET /analytics/drilldown",
        "requires_comparison": False,
        "requires_groups": False,
        "allowed_metrics": tuple(TREND_METRICS_BY_KEY),
        "allowed_dimensions": tuple(EXPLORER_DIMENSIONS_BY_KEY),
    },
    "summarize_current_context": {
        "intent": "summarize_current_context",
        "contract": "GET /analytics/overview",
        "requires_comparison": False,
        "requires_groups": False,
        "allowed_metrics": tuple(TREND_METRICS_BY_KEY),
        "allowed_dimensions": (),
    },
}


@dataclass
class IntentDecision:
    intent: str
    operation: str | None
    reason: str | None = None
    metric: str | None = None
    dimension: str | None = None
    groups: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)


def parse_ask_source(raw: str | None) -> AskSource:
    key = (raw or "overview").strip().lower()
    if key not in ASK_SOURCES:
        raise FilterValidationError("Unknown Ask source.")
    return key  # type: ignore[return-value]


def parse_ask_question(raw: str | None, *, max_chars: int = MAX_QUESTION_CHARS) -> str:
    text = " ".join(str(raw or "").split())
    if not text:
        raise FilterValidationError("Ask question is required.")
    if len(text) > max_chars:
        raise FilterValidationError(f"Ask question must be at most {max_chars} characters.")
    return text


def parse_ask_metric(raw: str | None) -> str | None:
    if raw is None or str(raw).strip() == "":
        return None
    return parse_trend_metric(raw, role="primary").key


def parse_ask_dimension(raw: str | None) -> str | None:
    if raw is None or str(raw).strip() == "":
        return None
    key = str(raw).strip()
    if key not in EXPLORER_DIMENSIONS_BY_KEY:
        raise FilterValidationError("Unknown Ask dimension.")
    return key


def parse_trend_request_text(question: str) -> dict[str, Any]:
    """Deterministic allowlisted mapping used when Gemini is off or unavailable."""
    if UNSUPPORTED_TREND_METRIC_RE.search(question):
        raise FilterValidationError("Unknown primary metric.")
    if UNSUPPORTED_TREND_DIMENSION_RE.search(question):
        raise FilterValidationError("Unknown breakdown dimension.")
    if UNSUPPORTED_TREND_GRAIN_RE.search(question):
        raise FilterValidationError("Time grain must be day, week, or month.")
    lowered = question.lower()
    metrics: list[str] = []
    for alias, key in METRIC_ALIASES:
        if alias in lowered and key not in metrics:
            metrics.append(key)
    if not metrics:
        raise FilterValidationError("Unknown primary metric.")
    grain = None
    if re.search(r"\b(by day|daily)\b", lowered):
        grain = "day"
    elif re.search(r"\b(by week|weekly)\b", lowered):
        grain = "week"
    elif re.search(r"\b(by month|monthly)\b", lowered):
        grain = "month"
    breakdown = None
    for alias, key in TREND_DIMENSION_ALIASES:
        if re.search(rf"\bby {re.escape(alias)}\b", lowered):
            breakdown = key
            break
    compare = (
        "none"
        if re.search(r"\b(compare\s*=\s*none|without comparison|no comparison)\b", lowered)
        else "omit"
    )
    payload = {
        "metric": metrics[0],
        "secondary": metrics[1] if len(metrics) > 1 else "none",
        "grain": grain or "day",
        "breakdown": breakdown or "none",
        "compare": compare,
    }
    return parse_generated_trend_selection(payload)


def extract_metric_from_text(question: str) -> str | None:
    lowered = question.lower()
    for alias, key in METRIC_ALIASES:
        if alias in lowered:
            return key
    return None


def _ranking_dimension(question: str) -> str | None:
    lowered = question.lower()
    for alias, key in TREND_DIMENSION_ALIASES:
        if re.search(rf"\b{re.escape(alias)}s?\b", lowered):
            return key
    return None


def parse_ask_intent_selection(raw: dict[str, Any], *, question: str) -> IntentDecision | None:
    """Validate a bounded NL Ask operation against the existing D8 registry."""
    if not isinstance(raw, dict):
        return None
    extra = set(raw) - ASK_INTENT_KEYS
    if extra:
        return None
    operation = str(raw.get("operation") or "").strip()
    if operation == "unsupported":
        return IntentDecision("unsupported", None, "unsupported_intent")
    if operation not in OPERATIONS:
        return None
    metric = None
    metric_raw = raw.get("metric")
    if metric_raw is not None and str(metric_raw).strip().lower() not in {"", "none", "null"}:
        try:
            metric = parse_ask_metric(str(metric_raw))
        except FilterValidationError:
            return None
    if metric is None:
        metric = extract_metric_from_text(question)
    dimension = None
    dimension_raw = raw.get("dimension")
    if dimension_raw is not None and str(dimension_raw).strip().lower() not in {"", "none", "null"}:
        try:
            dimension = parse_ask_dimension(str(dimension_raw))
        except FilterValidationError:
            return None
    if dimension is None:
        dimension = _ranking_dimension(question)
    groups = extract_compare_groups(question) if operation == "compare_dimensions" else []
    if not metric and operation == "identify_driver":
        metric = "revenue_inr"
    if operation in {"explain_metric_change", "compare_periods", "identify_driver"} and not metric:
        return None
    return IntentDecision(
        operation,
        operation,
        metric=metric,
        dimension=dimension,
        groups=groups,
    )


def extract_compare_groups(question: str) -> list[str]:
    match = COMPARE_RE.search(question.rstrip(" ?!."))
    if not match:
        return []
    left = _strip_group_token(match.group("a"))
    right = _strip_group_token(match.group("b"))
    if left.lower().startswith("compare "):
        left = _strip_group_token(left[8:])
    if not left or not right or left.lower() == right.lower():
        return []
    lowered = f"{left} {right}".lower()
    if (
        "comparison period" in lowered
        or COMPARE_SKIP_RE.search(left)
        or PERIOD_COMPARE_GROUP_RE.search(left)
        or PERIOD_COMPARE_GROUP_RE.search(right)
    ):
        return []
    return [left, right]


def _strip_group_token(raw: str) -> str:
    text = str(raw or "").strip(" .")
    aliases = "|".join(re.escape(alias) for alias, _key in METRIC_ALIASES)
    text = re.sub(
        rf"\s+(?:for|on|by|using)\s+(?:the\s+)?(?:{aliases})\b.*$",
        "",
        text,
        flags=re.IGNORECASE,
    )
    return text.strip(" .")


def normalize_group_name(value: str | None) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def group_token_matches(token: str, *, key: str, label: str | None) -> bool:
    want = normalize_group_name(token)
    if not want:
        return False
    return want == normalize_group_name(key) or want == normalize_group_name(label)


def refuse_question(question: str, *, bound_client_id: str | None = None) -> IntentDecision | None:
    if INJECTION_RE.search(question):
        return IntentDecision("refused", None, "prompt_injection")
    if SQL_RE.search(question):
        return IntentDecision("refused", None, "sql_request")
    if SCOPE_RE.search(question):
        return IntentDecision("refused", None, "scope_widen")
    if UNPUBLISHED_RE.search(question):
        return IntentDecision("refused", None, "unpublished")
    if MUTATE_RE.search(question):
        return IntentDecision("refused", None, "mutate")
    if SECRETS_RE.search(question):
        return IntentDecision("refused", None, "secrets")
    if bound_client_id:
        for match in UUID_RE.findall(question):
            if match.lower() != bound_client_id.lower():
                return IntentDecision("refused", None, "scope_widen")
    return None


def _is_current_context_question(text: str) -> bool:
    """True for current-period / summarization wording, not change explanations."""
    if CURRENT_CONTEXT_RE.search(text) is None:
        return False
    return CURRENT_CONTEXT_CHANGE_RE.search(text) is None


def classify_intent(
    question: str,
    *,
    source: str,
    metric: str | None = None,
    dimension: str | None = None,
    insight_id: str | None = None,
    anomaly_id: str | None = None,
) -> IntentDecision:
    return apply_operation_spec(
        _classify_intent(
            question,
            source=source,
            metric=metric,
            dimension=dimension,
            insight_id=insight_id,
            anomaly_id=anomaly_id,
        )
    )


def _classify_intent(
    question: str,
    *,
    source: str,
    metric: str | None = None,
    dimension: str | None = None,
    insight_id: str | None = None,
    anomaly_id: str | None = None,
) -> IntentDecision:
    text = question.lower()
    groups = extract_compare_groups(question)
    found_metric = extract_metric_from_text(question)
    caveats: list[str] = []
    if CAUSAL_QUESTION_RE.search(text):
        caveats.append(CAUSAL_CAVEAT)

    if GENERATE_TREND_RE.search(text):
        return IntentDecision(
            "generate_trend",
            "generate_trend",
            metric=found_metric or metric,
            caveats=caveats,
        )
    if groups and re.search(r"\b(compare|vs\.?|versus|with)\b", text):
        dim = dimension
        if all(normalize_group_name(item) in CHANNEL_HINTS for item in groups):
            dim = "channel"
        return IntentDecision(
            "compare_dimensions",
            "compare_dimensions",
            metric=found_metric or metric,
            dimension=dim or "channel",
            groups=groups,
            caveats=caveats,
        )
    if source == "insight" or insight_id or re.search(r"\binsight\b", text):
        return IntentDecision(
            "explain_insight",
            "explain_insight",
            metric=found_metric or metric,
            dimension=dimension,
            caveats=caveats,
        )
    if source == "anomaly" or anomaly_id or UNUSUAL_RE.search(text):
        return IntentDecision(
            "explain_anomaly",
            "explain_anomaly",
            metric=found_metric or metric,
            dimension=dimension,
            caveats=caveats,
        )
    if source == "drill" or re.search(r"\bdrill", text):
        return IntentDecision(
            "explain_drilldown",
            "explain_drilldown",
            metric=found_metric or metric,
            dimension=dimension,
            caveats=caveats,
        )
    if source == "explorer" or re.search(r"\b(explorer|ranking|movers?|top n)\b", text):
        return IntentDecision(
            "explain_explorer_result",
            "explain_explorer_result",
            metric=found_metric or metric,
            dimension=dimension,
            caveats=caveats,
        )
    if RANK_PERIOD_RE.search(text) and _ranking_dimension(text) is None:
        return IntentDecision("unsupported", None, "period_rank_unsupported", caveats=caveats)
    if CONTRIBUTION_TO_CHANGE_RE.search(text):
        return IntentDecision(
            "identify_driver",
            "identify_driver",
            metric=found_metric or metric,
            dimension=dimension or _ranking_dimension(text),
            caveats=caveats,
        )
    rank_dimension = _ranking_dimension(text)
    if rank_dimension and RANK_VALUE_RE.search(text):
        return IntentDecision(
            "explain_explorer_result",
            "explain_explorer_result",
            metric=found_metric or metric,
            dimension=rank_dimension,
            caveats=caveats,
        )
    if source == "trend" or re.search(r"\b(trend|over time|time series)\b", text):
        return IntentDecision(
            "summarize_trend",
            "summarize_trend",
            metric=found_metric or metric,
            dimension=dimension,
            caveats=caveats,
        )
    if DRIVER_RE.search(text):
        return IntentDecision(
            "identify_driver",
            "identify_driver",
            metric=found_metric or metric,
            dimension=dimension,
            caveats=caveats,
        )
    if re.search(
        r"\b(why did|why has|why is|explain .+ "
        r"(fall|fell|drop|decline|improve|increase|change|low|performance))\b",
        text,
    ):
        metric_key = found_metric or metric
        if metric_key is None:
            return IntentDecision("unsupported", None, "unsupported_metric", caveats=caveats)
        return IntentDecision(
            "explain_metric_change",
            "explain_metric_change",
            metric=metric_key,
            caveats=caveats,
        )
    if NAMED_METRIC_CHANGE_RE.search(text) and (found_metric or metric):
        return IntentDecision(
            "explain_metric_change",
            "explain_metric_change",
            metric=found_metric or metric,
            caveats=caveats,
        )
    if re.search(
        r"\b(what changed|compare (this|the) (month|period)|versus (the )?comparison|"
        r"last two months|between the last two)\b",
        text,
    ):
        return IntentDecision(
            "compare_periods",
            "compare_periods",
            metric=found_metric or metric,
            caveats=caveats,
        )
    if _is_current_context_question(text):
        return IntentDecision(
            "summarize_current_context",
            "summarize_current_context",
            metric=found_metric or metric,
            caveats=caveats,
        )
    if source == "kpi" and (found_metric or metric):
        if UNRELATED_ASK_RE.search(text):
            return IntentDecision("unsupported", None, "unsupported_intent", caveats=caveats)
        if found_metric or KPI_FOCUS_RE.search(text) or caveats:
            return IntentDecision(
                "explain_metric_change",
                "explain_metric_change",
                metric=found_metric or metric,
                caveats=caveats,
            )
    if found_metric:
        return IntentDecision(
            "explain_metric_change",
            "explain_metric_change",
            metric=found_metric,
            caveats=caveats,
        )
    return IntentDecision("unsupported", None, "unsupported_intent", caveats=caveats)


def operation_spec(operation: str | None) -> dict[str, Any]:
    if not operation or operation not in OPERATIONS:
        raise FilterValidationError("Unknown Ask operation.")
    return OPERATIONS[operation]


def apply_operation_spec(decision: IntentDecision) -> IntentDecision:
    if decision.intent in {"unsupported", "refused"} or not decision.operation:
        return decision
    if decision.operation not in OPERATIONS:
        return IntentDecision(
            "unsupported", None, "unsupported_operation", caveats=list(decision.caveats)
        )
    spec = OPERATIONS[decision.operation]
    if decision.metric and decision.metric not in spec["allowed_metrics"]:
        return IntentDecision(
            "unsupported", None, "unsupported_metric", caveats=list(decision.caveats)
        )
    dimension = decision.dimension
    allowed_dims = spec["allowed_dimensions"]
    if dimension:
        if not allowed_dims:
            decision = replace(decision, dimension=None)
        elif dimension not in allowed_dims:
            return IntentDecision(
                "unsupported", None, "unsupported_dimension", caveats=list(decision.caveats)
            )
    if spec.get("requires_groups") and len(decision.groups) < 2:
        return IntentDecision(
            "unsupported", None, "insufficient_evidence", caveats=list(decision.caveats)
        )
    return decision


def compact_kpi(card: Any) -> dict[str, Any]:
    return {
        "metric": getattr(card, "id", None),
        "label": getattr(card, "label", None),
        "kind": getattr(card, "kind", None),
        "current": getattr(card, "value", None),
        "comparison": getattr(card, "prior_value", None),
        "delta": getattr(card, "delta", None),
        "delta_pct": getattr(card, "delta_pct", None),
    }


def pct_label(raw: object) -> str | None:
    number = as_decimal(raw)
    if number is None:
        return None
    return f"{(number * Decimal('100')):.1f}%"


def question_frame(question: str | None) -> str:
    """Coarse question shape for template wording. Not an extra operation."""
    text = str(question or "").lower()
    if UNUSUAL_RE.search(text):
        return "anomaly"
    if CONTRIBUTION_TO_CHANGE_RE.search(text) or DRIVER_RE.search(text):
        return "driver"
    if re.search(r"\b(why|how come)\b", text):
        return "why"
    if NAMED_METRIC_CHANGE_RE.search(text):
        return "what_changed"
    if CURRENT_PERFORMANCE_RE.search(text):
        return "current"
    if RANK_VALUE_RE.search(text):
        return "ranking"
    return "default"


def _metric_change_parts(
    *,
    frame: str,
    label: str,
    current: object,
    prior: object,
    direction: str,
    shown_pct: str | None,
    period_bit: str,
    compare_bit: str,
) -> list[str]:
    parts: list[str] = []
    if current is None:
        parts.append(f"{label} has no calculated value for {period_bit}.")
        return parts
    if frame == "current":
        parts.append(f"{label} is {current} in {period_bit}.")
        if prior is not None and shown_pct:
            parts.append(
                f"Compared with {compare_bit}, it {direction} {shown_pct} ({current} vs {prior})."
            )
        elif prior is not None:
            parts.append(
                f"Compared with {compare_bit}, the calculated values are {current} vs {prior}."
            )
        return parts
    if frame == "what_changed":
        if prior is not None:
            parts.append(
                f"{label} moved from {prior} in {compare_bit} to {current} in {period_bit}."
            )
            if shown_pct and direction in {"declined", "increased"}:
                noun = "decline" if direction == "declined" else "increase"
                parts.append(f"That is a {shown_pct} {noun}.")
        else:
            parts.append(
                f"{label} is {current} in {period_bit}. No comparison value is available."
            )
        return parts
    if shown_pct and prior is not None:
        parts.append(
            f"{label} {direction} {shown_pct} from {compare_bit} to {period_bit} "
            f"({current} vs {prior})."
        )
    else:
        parts.append(
            f"{label} is {current} in {period_bit}. No comparison value is available."
        )
    return parts


def template_answer(
    evidence: dict[str, Any], *, caveats: list[str], question: str | None = None
) -> str:
    operation = evidence.get("operation")
    frame = question_frame(question)
    period = evidence.get("period") or {}
    comparison = evidence.get("comparison") or {}
    period_bit = period.get("month_label") or period.get("month_start") or "the selected period"
    compare_bit = (
        comparison.get("month_label") or comparison.get("month_start") or "the comparison period"
    )
    metric = evidence.get("metric") or {}
    label = metric.get("label") or metric.get("metric") or "The metric"
    current = metric.get("current")
    prior = metric.get("comparison")
    delta_pct = pct_label(metric.get("delta_pct"))
    parts: list[str] = []
    if operation == "compare_dimensions":
        rows = evidence.get("groups") or []
        if len(rows) < 2:
            extra = [
                item
                for item in (evidence.get("group_a"), evidence.get("group_b"))
                if isinstance(item, dict)
            ]
            if extra:
                rows = extra
        names = (
            " vs ".join(str(row.get("label") or row.get("key")) for row in rows[:2])
            or "the selected groups"
        )
        parts.append(f"{label} for {names} in {period_bit} is shown from the explorer ranking.")
        for row in rows[:2]:
            parts.append(
                f"{row.get('label') or row.get('key')}: {row.get('current')} "
                f"(period comparison {row.get('comparison')}, "
                f"change {pct_label(row.get('delta_pct')) or 'n/a'})."
            )
    elif operation == "summarize_trend":
        summary = evidence.get("trend") or {}
        parts.append(
            f"{label} over {summary.get('grain') or 'the selected grain'} has "
            f"{summary.get('point_count') or 0} points from "
            f"{summary.get('first')} to {summary.get('last')}."
        )
    elif operation == "generate_trend":
        selection = evidence.get("selection") or {}
        grain = selection.get("grain") or "day"
        secondary = selection.get("secondary")
        breakdown = selection.get("breakdown")
        bits = [f"{label} by {grain}"]
        if secondary:
            bits.append(f"with {selection.get('secondary_label') or secondary}")
        if breakdown:
            bits.append(f"broken down by {selection.get('breakdown_label') or breakdown}")
        if selection.get("compare") == "none":
            bits.append("with comparison off")
        parts.append("Trends will show " + " ".join(bits) + " using the current Overview period.")
        parts.append(
            "Values are taken from the existing D3 trend contract; "
            "the model did not calculate metrics."
        )
    elif operation == "explain_anomaly":
        item = evidence.get("anomaly") or {}
        explanation = (
            item.get("explanation")
            or item.get("headline")
            or "No anomaly matched the current context."
        )
        if frame == "anomaly" and re.search(
            r"\b(is there|anything unusual|any unusual)\b", str(question or ""), re.IGNORECASE
        ):
            parts.append(f"Yes. {explanation}")
        else:
            parts.append(explanation)
    elif operation == "explain_insight":
        item = evidence.get("insight") or {}
        parts.append(
            item.get("explanation")
            or item.get("headline")
            or "No insight matched the current context."
        )
    elif operation == "identify_driver":
        item = evidence.get("insight") or {}
        explanation = (
            item.get("explanation") or "No dominant driver cleared the deterministic threshold."
        )
        if frame == "driver" and re.search(
            r"\bwhat (is|are) driving\b", str(question or ""), re.IGNORECASE
        ):
            parts.append(f"The current published-history driver is: {explanation}")
        else:
            parts.append(explanation)
    elif operation == "explain_explorer_result":
        rows = evidence.get("rows") or []
        if rows:
            top = rows[0]
            parts.append(
                f"{label} ranking is led by "
                f"{top.get('label') or top.get('key')} at {top.get('current')} "
                f"in {period_bit}."
            )
        else:
            parts.append(f"No explorer groups matched {label} in {period_bit}.")
    elif operation == "explain_drilldown":
        rows = evidence.get("rows") or []
        if rows:
            top = rows[0]
            parts.append(
                f"In this drilldown slice, "
                f"{top.get('label') or top.get('key')} is {top.get('current')} "
                f"for {label} in {period_bit}."
            )
        else:
            parts.append("This drilldown slice has no groups to summarize.")
    elif operation == "summarize_current_context":
        kpis = evidence.get("kpis") or []
        if kpis:
            bits = [
                f"{row.get('label')}: {row.get('current')}"
                + (
                    f" ({pct_label(row.get('delta_pct'))})"
                    if row.get("delta_pct") is not None
                    else ""
                )
                for row in kpis[:4]
            ]
            parts.append("Current published-history KPIs: " + "; ".join(bits) + ".")
        else:
            parts.append("No published-history KPIs are available for this context.")
    else:
        direction = "changed"
        try:
            if (
                as_decimal(metric.get("delta_pct")) is not None
                and as_decimal(metric.get("delta_pct")) < 0
            ):
                direction = "declined"
            elif (
                as_decimal(metric.get("delta_pct")) is not None
                and as_decimal(metric.get("delta_pct")) > 0
            ):
                direction = "increased"
        except Exception:
            direction = "changed"
        shown_pct = (
            delta_pct.lstrip("-")
            if delta_pct and direction in {"declined", "increased"}
            else delta_pct
        )
        parts.extend(
            _metric_change_parts(
                frame=frame,
                label=str(label),
                current=current,
                prior=prior,
                direction=direction,
                shown_pct=shown_pct,
                period_bit=str(period_bit),
                compare_bit=str(compare_bit),
            )
        )
        drivers = evidence.get("drivers") or []
        if drivers:
            top = drivers[0]
            share = pct_label(top.get("contribution_pct"))
            if share:
                parts.append(
                    f"{top.get('dimension_label') or 'Group'} "
                    f"{top.get('label')} accounted for {share} "
                    "of the observed change."
                )
    if comparison.get("available") is False and operation_spec_requires_comparison(operation):
        parts.append("A comparison period is not available, so change cannot be explained.")
    for caveat in caveats:
        parts.append(caveat)
    parts.append(
        "Values are calculated from published history; this wording does not recompute KPIs."
    )
    return " ".join(part.strip() for part in parts if part)


STRUCTURED_DIRECTIONS = frozenset({"rose", "fell", "unchanged", "unclear"})
STRUCTURED_CLAIMS = frozenset({"fall", "rise", "none"})


def _cite_evidence_number(raw: object) -> str | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if "." in text:
        compact = text.rstrip("0").rstrip(".")
        if compact:
            return compact
    return text


def _metric_change_direction(metric: dict[str, Any]) -> str | None:
    current = as_decimal(metric.get("current"))
    prior = as_decimal(metric.get("comparison"))
    if current is None or prior is None:
        return None
    if current > prior:
        return "rose"
    if current < prior:
        return "fell"
    return "unchanged"


def parse_structured_ask_wording(raw: str) -> dict[str, Any] | None:
    """Return bounded wording fields, or None when the model returned prose."""
    text = str(raw or "").strip()
    if not text:
        return None
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        payload = json.loads(text)
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    direction = payload.get("direction")
    if direction not in STRUCTURED_DIRECTIONS:
        return None
    claim = payload.get("user_claim", "none")
    if claim not in STRUCTURED_CLAIMS:
        claim = "none"
    return {
        "user_claim": claim,
        "direction": direction,
        "evidence_incomplete": payload.get("evidence_incomplete") is True,
        "suggest_breakdown": payload.get("suggest_breakdown") is True,
    }


def render_structured_ask_wording(payload: dict[str, Any], evidence: dict[str, Any]) -> str | None:
    """Build the visible answer from evidence numbers plus bounded model flags."""
    metric = evidence.get("metric") if isinstance(evidence.get("metric"), dict) else {}
    label = str(metric.get("label") or metric.get("metric") or "").strip() or "The metric"
    current_disp = _cite_evidence_number(metric.get("current"))
    if current_disp is None:
        return None
    prior_disp = _cite_evidence_number(metric.get("comparison"))
    period = evidence.get("period") if isinstance(evidence.get("period"), dict) else {}
    comparison = evidence.get("comparison") if isinstance(evidence.get("comparison"), dict) else {}
    period_label = period.get("month_label")
    compare_label = comparison.get("month_label") if isinstance(comparison, dict) else None
    direction = _metric_change_direction(metric) or payload.get("direction")
    claim = payload.get("user_claim") or "none"
    if direction == "rose" and claim == "fall":
        head = f"{label} did not fall"
    elif direction == "fell" and claim == "rise":
        head = f"{label} did not rise"
    elif direction == "rose":
        head = f"{label} increased"
    elif direction == "fell":
        head = f"{label} declined"
    elif direction == "unchanged":
        head = f"{label} did not change"
    else:
        head = f"{label} is shown in the current evidence"
    current_bit = f"current {label} is {current_disp}"
    if period_label:
        current_bit += f" in {period_label}"
    if prior_disp is not None:
        current_bit += f" compared with {prior_disp}"
        if compare_label:
            current_bit += f" in {compare_label}"
    parts = [f"{head}; {current_bit}."]
    if payload.get("evidence_incomplete"):
        parts.append(
            "The evidence is incomplete to determine what factors coincided with this change."
        )
    if payload.get("suggest_breakdown"):
        parts.append("You may request a breakdown by channel or campaign to analyze the shift.")
    return " ".join(parts)


def coerce_llm_wording(raw: str, evidence: dict[str, Any]) -> str:
    """Render bounded JSON through DFIP; leave ordinary prose unchanged."""
    payload = parse_structured_ask_wording(raw)
    if payload is None:
        return " ".join(str(raw or "").split())
    rendered = render_structured_ask_wording(payload, evidence)
    if not rendered:
        return " ".join(str(raw or "").split())
    return rendered


def operation_spec_requires_comparison(operation: str | None) -> bool:
    if not operation or operation not in OPERATIONS:
        return False
    return bool(OPERATIONS[operation].get("requires_comparison"))


def _walk_numbers(value: Any, into: set[str], *, as_rate: bool = False) -> None:
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, dict):
        for key, item in value.items():
            rate = as_rate or str(key).endswith("_pct") or key in {"delta_pct", "contribution_pct"}
            _walk_numbers(item, into, as_rate=rate)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _walk_numbers(item, into, as_rate=as_rate)
        return
    if isinstance(value, int) and not isinstance(value, bool):
        into.add(str(value))
        into.add(f"{value:,}")
        if as_rate:
            percent = Decimal(value) * Decimal("100")
            into.update(_number_forms(f"{percent:.4f}"))
            into.update(_number_forms(f"{percent:.1f}"))
            into.update(_number_forms(str(int(percent))))
        return
    text = str(value)
    for match in NUMBER_RE.findall(text):
        into.update(_number_forms(match))
        if not as_rate:
            continue
        try:
            number = Decimal(match.replace(",", ""))
        except Exception:
            continue
        percent = number * Decimal("100")
        into.update(_number_forms(f"{percent:.4f}"))
        into.update(_number_forms(f"{percent:.1f}"))
        into.update(_number_forms(str(int(percent))))


def _number_forms(raw: str) -> set[str]:
    compact = raw.replace(",", "")
    forms = {raw, compact}
    if "." in compact:
        forms.add(compact.rstrip("0").rstrip("."))
        try:
            forms.add(f"{Decimal(compact):.1f}")
            forms.add(f"{Decimal(compact):.4f}")
        except Exception:
            pass
    return {item for item in forms if item}


def allowed_number_forms(evidence: dict[str, Any]) -> set[str]:
    allowed: set[str] = set()
    _walk_numbers(evidence, allowed)
    return allowed


@dataclass
class _NumberEntity:
    labels: set[str]
    metric_key: str | None
    current: set[str] = field(default_factory=set)
    comparison: set[str] = field(default_factory=set)
    delta: set[str] = field(default_factory=set)
    pct_signed: set[str] = field(default_factory=set)
    pct_abs: set[str] = field(default_factory=set)
    pct_negative: bool | None = None

    def all_values(self) -> set[str]:
        return set().union(self.current, self.comparison, self.delta, self.pct_signed, self.pct_abs)


def _metric_labels(key: str | None, label: str | None) -> set[str]:
    labels: set[str] = set()
    if key:
        lowered = key.strip().lower()
        labels.add(lowered)
        labels.add(lowered.replace("_", " "))
        for alias, dest in METRIC_ALIASES:
            if dest == key:
                labels.add(alias)
    if label:
        labels.add(str(label).strip().lower())
    return {item for item in labels if item}


def _add_pct(entity: _NumberEntity, raw: object, *, sets_sign: bool) -> None:
    _walk_numbers(raw, entity.pct_signed, as_rate=True)
    number = as_decimal(raw)
    if number is None:
        return
    if sets_sign:
        entity.pct_negative = number < 0
    percent = number * Decimal("100")
    entity.pct_abs.update(_number_forms(f"{abs(percent):.4f}"))
    entity.pct_abs.update(_number_forms(f"{abs(percent):.1f}"))
    entity.pct_abs.update(_number_forms(str(int(abs(percent)))))
    try:
        entity.pct_abs.update(_number_forms(str(abs(percent))))
    except Exception:
        pass


def _fill_entity_values(entity: _NumberEntity, row: dict[str, Any]) -> None:
    current_keys = ("current", "value")
    comparison_keys = ("comparison", "prior_value", "baseline")
    for key in current_keys:
        if row.get(key) is not None:
            _walk_numbers(row[key], entity.current)
    for key in comparison_keys:
        if row.get(key) is not None:
            _walk_numbers(row[key], entity.comparison)
    if row.get("delta") is not None:
        _walk_numbers(row["delta"], entity.delta)
    if row.get("delta_pct") is not None:
        _add_pct(entity, row["delta_pct"], sets_sign=True)
    if row.get("contribution_pct") is not None:
        _add_pct(entity, row["contribution_pct"], sets_sign=False)


def _entities_from_evidence(
    evidence: dict[str, Any],
) -> tuple[list[_NumberEntity], set[str], set[str]]:
    entities: list[_NumberEntity] = []
    context: set[str] = set()
    metric_keys: set[str] = set()

    def add_row(row: Any, *, group: bool = False) -> None:
        if not isinstance(row, dict):
            return
        key = row.get("metric") if isinstance(row.get("metric"), str) else None
        entity = _NumberEntity(
            labels=_metric_labels(None if group else key, row.get("label")),
            metric_key=None if group else key,
        )
        if group:
            for raw in (row.get("key"), row.get("label")):
                name = normalize_group_name(str(raw) if raw is not None else "")
                if name:
                    entity.labels.add(name)
        _fill_entity_values(entity, row)
        if entity.metric_key:
            metric_keys.add(entity.metric_key)
        if entity.labels or entity.all_values():
            entities.append(entity)

    if isinstance(evidence.get("metric"), dict):
        add_row(evidence["metric"])
    for item in evidence.get("kpis") or []:
        add_row(item)
    for item in evidence.get("groups") or []:
        add_row(item, group=True)
    add_row(evidence.get("group_a"), group=True)
    add_row(evidence.get("group_b"), group=True)
    for item in evidence.get("rows") or []:
        add_row(item, group=True)
        if isinstance(item, dict) and item.get("rank") is not None:
            _walk_numbers(item.get("rank"), context)
    for item in evidence.get("drivers") or []:
        add_row(item, group=True)
    add_row(evidence.get("insight"))
    add_row(evidence.get("anomaly"))
    trend = evidence.get("trend") or {}
    if isinstance(trend, dict):
        _walk_numbers(trend.get("point_count"), context)
        _walk_numbers(trend.get("first_bucket"), context)
        _walk_numbers(trend.get("last_bucket"), context)
        if entities:
            if trend.get("last") is not None:
                _walk_numbers(trend.get("last"), entities[0].current)
            if trend.get("first") is not None:
                _walk_numbers(trend.get("first"), entities[0].comparison)
    for blob in (evidence.get("period"), evidence.get("comparison")):
        if isinstance(blob, dict):
            for skip_key, item in blob.items():
                if skip_key == "available":
                    continue
                _walk_numbers(item, context)
    return entities, context, metric_keys


def _metrics_mentioned(text: str) -> set[str]:
    lowered = text.lower()
    keys: set[str] = set()
    for alias, key in METRIC_ALIASES:
        if re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", lowered):
            keys.add(key)
    return keys


def _label_present(text: str, label: str) -> bool:
    if not label:
        return False
    return re.search(rf"(?<![a-z0-9]){re.escape(label)}(?![a-z0-9])", text) is not None


def _nearest_entity(
    clause: str, position: int, entities: list[_NumberEntity]
) -> _NumberEntity | None:
    lowered = clause.lower()
    best: _NumberEntity | None = None
    best_dist = 10**9
    best_len = 0
    for entity in entities:
        for label in entity.labels:
            for match in re.finditer(rf"(?<![a-z0-9]){re.escape(label)}(?![a-z0-9])", lowered):
                if match.end() > position:
                    continue
                dist = position - match.end()
                if dist < best_dist or (dist == best_dist and len(label) > best_len):
                    best = entity
                    best_dist = dist
                    best_len = len(label)
    if best is not None:
        return best
    for entity in entities:
        if any(_label_present(lowered, label) for label in entity.labels):
            return entity
    return None


def _pair_roles(clause: str) -> dict[tuple[int, str], str]:
    roles: dict[tuple[int, str], str] = {}
    from_to = re.compile(
        rf"from\s+(?P<a>{NUMBER_RE.pattern})\s+to\s+(?P<b>{NUMBER_RE.pattern})",
        re.IGNORECASE,
    )
    versus = re.compile(
        rf"(?P<a>{NUMBER_RE.pattern})\s+(?:vs\.?|versus)\s+(?P<b>{NUMBER_RE.pattern})",
        re.IGNORECASE,
    )
    for match in from_to.finditer(clause):
        roles[(match.start("a"), match.group("a"))] = "comparison"
        roles[(match.start("b"), match.group("b"))] = "current"
    for match in versus.finditer(clause):
        roles[(match.start("a"), match.group("a"))] = "current"
        roles[(match.start("b"), match.group("b"))] = "comparison"
    return roles


def _role_for_number(clause: str, match: re.Match[str], paired: dict[tuple[int, str], str]) -> str:
    token = match.group(0)
    paired_role = paired.get((match.start(), token))
    if paired_role:
        return paired_role
    after = clause[match.end() : match.end() + 8]
    if after.lstrip().startswith("%") or after.lower().lstrip().startswith(" percent"):
        return "percent"
    tail = clause[: match.start()][-48:]
    if re.search(r"\b(is|are|now|current|currently)\s+$", tail, re.IGNORECASE):
        return "current"
    if re.search(r"\bfrom\s+$", tail, re.IGNORECASE):
        return "comparison"
    if re.search(r"\bto\s+$", tail, re.IGNORECASE):
        return "current"
    if re.search(
        r"\b(was|were|prior|previous|baseline|comparison|compared)\s+$",
        tail,
        re.IGNORECASE,
    ):
        return "comparison"
    if re.search(r"\b(delta|difference|changed by)\s+$", tail, re.IGNORECASE):
        return "delta"
    return "any"


def _number_allowed_for_role(
    forms: set[str], role: str, entity: _NumberEntity, clause: str
) -> bool:
    if role == "current":
        return not entity.current.isdisjoint(forms)
    if role == "comparison":
        return not entity.comparison.isdisjoint(forms)
    if role == "delta":
        return not entity.delta.isdisjoint(forms)
    if role == "percent":
        declined = DECLINE_WORD_RE.search(clause) is not None
        increased = INCREASE_WORD_RE.search(clause) is not None
        if declined and not increased:
            if entity.pct_negative is False:
                return False
            return not entity.pct_abs.isdisjoint(forms)
        if increased and not declined:
            if entity.pct_negative is True:
                return False
            return not entity.pct_abs.isdisjoint(forms)
        return not entity.pct_signed.isdisjoint(forms) or not entity.pct_abs.isdisjoint(forms)
    return not entity.all_values().isdisjoint(forms)


def validate_llm_answer(text: str, evidence: dict[str, Any]) -> str | None:
    answer, _reason = explain_llm_answer(text, evidence)
    return answer


def explain_llm_answer(text: str, evidence: dict[str, Any]) -> tuple[str | None, str | None]:
    """Return (normalized answer, None) or (None, rule) without changing D8 rules."""
    answer = " ".join(str(text or "").split())
    if not answer or len(answer) < 12:
        return None, "too_short"
    if len(answer) > 1600:
        return None, "too_long"
    padded = f" {answer.lower()} "
    if any(token in padded for token in CAUSAL_FORBIDDEN):
        return None, "causal_language"
    if SQL_RE.search(answer):
        return None, "sql"
    entities, context, metric_keys = _entities_from_evidence(evidence)
    mentioned = _metrics_mentioned(answer)
    if mentioned and metric_keys and not mentioned.issubset(metric_keys):
        return None, "unknown_metric"
    evidence_text = json.dumps(evidence, default=str).lower()
    for match in UUID_RE.findall(answer):
        if match.lower() not in evidence_text:
            return None, "foreign_uuid"
    clauses = [
        part.strip() for part in re.split(r"(?:\s+and\s+)|(?:\.\s+)|;", answer) if part.strip()
    ]
    last_entity: _NumberEntity | None = None
    metric_entities = [item for item in entities if item.metric_key]
    for clause in clauses:
        paired = _pair_roles(clause)
        clause_entity = _nearest_entity(clause, len(clause), entities)
        if clause_entity is not None:
            last_entity = clause_entity
        for match in NUMBER_RE.finditer(clause):
            compact = match.group(0).replace(",", "")
            if re.fullmatch(r"20[2-3]\d", compact):
                continue
            forms = _number_forms(match.group(0))
            entity = _nearest_entity(clause, match.start(), entities) or last_entity
            if entity is None and len(metric_entities) == 1:
                entity = metric_entities[0]
            if entity is None and len(entities) == 1:
                entity = entities[0]
            role = _role_for_number(clause, match, paired)
            if role == "any" and not context.isdisjoint(forms):
                continue
            if entity is None:
                return None, "unattached_number"
            if not _number_allowed_for_role(forms, role, entity, clause):
                return None, "number_not_in_evidence"
    return answer, None
