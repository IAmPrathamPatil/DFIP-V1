"""D8 Ask orchestration: intent → D1–D7 contract → grounded explanation."""

from __future__ import annotations

import json
import time
from typing import Any

from dfip_analytics.ask import (
    MAX_EVIDENCE_ROWS,
    MAX_FILTER_VALUES,
    MAX_QUESTION_CHARS,
    PERIOD_RANK_COPY,
    REFUSAL_COPY,
    UNSUPPORTED_COPY,
    apply_operation_spec,
    classify_intent,
    coerce_llm_wording,
    compact_kpi,
    group_token_matches,
    operation_spec,
    parse_ask_dimension,
    parse_ask_intent_selection,
    parse_ask_metric,
    parse_ask_question,
    parse_ask_source,
    parse_trend_request_text,
    refuse_question,
    template_answer,
    validate_llm_answer,
)
from dfip_analytics.filters import FilterValidationError
from dfip_analytics.trends import parse_generated_trend_selection

from dfip_api.analytics_service import AnalyticsService
from dfip_api.ask_llm import (
    GEMINI_INTENT_MAX_OUTPUT_TOKENS,
    GEMINI_INTENT_THINKING_LEVEL,
    AskLlm,
    AskLlmError,
    ask_provider_is_enabled,
    gemini_ask_intent_schema,
    gemini_trend_selection_schema,
)
from dfip_api.auth import Principal
from dfip_api.errors import ValidationFailed
from dfip_api.schemas import (
    AskFilters,
    AskFocus,
    AskRequest,
    AskResponse,
    AskTimings,
    OverviewAppliedState,
    OverviewKpiCard,
)

SYSTEM_PROMPT = (
    "You explain DFIP published-history analytics for one authenticated company. "
    "Prefer JSON with only these keys: user_claim (fall, rise, or none), "
    "direction (rose, fell, unchanged, or unclear), evidence_incomplete "
    "(boolean), suggest_breakdown (boolean). JSON values must not include "
    "numeric literals. DFIP will insert evidence numbers. "
    "If you reply in sentences instead: use only numeric values that appear "
    "directly in EVIDENCE. Do not calculate, derive, estimate, extrapolate, or "
    "introduce new numbers. Do not compute differences, percentages, deltas, "
    "rates, totals, or changes unless that exact numeric result already appears "
    "in EVIDENCE. Do not write an increase or decrease of a number unless that "
    "exact number already appears in EVIDENCE. You may compare evidence values "
    "qualitatively without introducing a derived number. Include the focused "
    "metric current value from EVIDENCE using is or current ... is, not was; "
    "trailing zeros may be omitted, but do not round to a different figure. "
    "Cite a comparison evidence value with compared with, versus, or from ... "
    "to ... . Do not invent metrics, dimensions, companies, periods, or SQL. "
    "Do not claim causation; use contributed, accounted for, associated with, "
    "or coincided with. Never use because, caused, causing, causal, or "
    "responsible for. Two to four complete short sentences. Do not stop "
    "mid-sentence. Reply with the final answer only, not chain-of-thought. If "
    "evidence is incomplete, say so. You may add one optional next action."
)

TREND_SELECTION_PROMPT = (
    "Translate the question into a D3 trend selection. Use only the allowed "
    "enum values. Do not calculate metrics, return numbers, SQL, company ids, "
    "or extra keys. Dates stay on the existing request filters. compare=none "
    "disables comparison; omit keeps the request comparison. Reply with JSON "
    "only, not chain-of-thought."
)
ASK_INTENT_PROMPT = (
    "Select one existing DFIP Ask operation. Use only the allowed enum values. "
    "Do not calculate metrics, invent operations, SQL, company ids, or extra keys. "
    "Use generate_trend only for show/plot/chart requests. "
    "Use identify_driver for what drove, is driving, performance driver, or "
    "contribution-to-change questions, including which campaign contributed most "
    "to a metric change. "
    "Use explain_explorer_result for highest/most/top campaign or channel ranking "
    "of current values, not contribution to a change. "
    "Use explain_anomaly for unusual, outlier, unexpected, or anomaly questions. "
    "Use explain_metric_change when a named metric changed or for current "
    "performance of a named metric. "
    "Use compare_periods for unlabeled what-changed-across-KPIs questions. "
    "Use unsupported for best/highest month or period ranking, poems, and unknown asks. "
    "If the question does not name a metric, use revenue_inr. "
    "If the question does not name a dimension, use none. "
    "Reply with JSON only, not chain-of-thought."
)


def _clip(value: object, limit: int = 120) -> str | None:
    if value is None:
        return None
    text = str(value)
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _limit_list(values: list[str] | None) -> list[str]:
    items = [str(item) for item in (values or []) if item is not None]
    return items[:MAX_FILTER_VALUES]


class AskService:
    def __init__(
        self,
        analytics: AnalyticsService,
        llm: AskLlm | None,
        *,
        max_question_chars: int = MAX_QUESTION_CHARS,
        provider: str = "none",
    ) -> None:
        self._analytics = analytics
        self._llm = llm if ask_provider_is_enabled(provider) else None
        self._max_question_chars = max_question_chars

    def ask(self, principal: Principal, payload: AskRequest) -> AskResponse:
        started = time.perf_counter()
        try:
            question = parse_ask_question(payload.question, max_chars=self._max_question_chars)
            source = parse_ask_source(payload.source)
            focus = payload.focus or AskFocus()
            parse_ask_metric(focus.metric)
            parse_ask_dimension(focus.dimension)
            parse_ask_metric(focus.trend_metric)
            parse_ask_metric(focus.explorer_metric)
            parse_ask_metric(focus.drill_metric)
            parse_ask_dimension(focus.explorer_dimension)
            parse_ask_dimension(focus.drill_dimension)
            if focus.trend_breakdown:
                parse_ask_dimension(focus.trend_breakdown)
        except FilterValidationError as exc:
            raise ValidationFailed(str(exc)) from exc

        bound = principal.client_id
        refusal = refuse_question(question, bound_client_id=bound)
        intent_ms = (time.perf_counter() - started) * 1000
        filters = payload.filters or AskFilters()
        if refusal is not None:
            return self._terminal(
                principal,
                filters,
                status="refused",
                intent="refused",
                source=source,
                question=question,
                answer=REFUSAL_COPY[refusal.reason or "prompt_injection"],
                empty_reason=refusal.reason,
                intent_ms=intent_ms,
                started=started,
            )

        decision = apply_operation_spec(
            classify_intent(
                question,
                source=source,
                metric=focus.metric
                or focus.trend_metric
                or focus.explorer_metric
                or focus.drill_metric,
                dimension=focus.dimension or focus.explorer_dimension or focus.drill_dimension,
                insight_id=focus.insight_id,
                anomaly_id=focus.anomaly_id,
            )
        )
        leftover_intent = (
            decision.intent == "unsupported" and decision.reason == "unsupported_intent"
        )
        intent_llm_called = False
        if self._llm is not None and leftover_intent:
            decision = self._resolve_ask_intent(question, decision)
            intent_llm_called = True
        intent_ms = (time.perf_counter() - started) * 1000
        if decision.intent == "unsupported" or not decision.operation:
            answer = (
                PERIOD_RANK_COPY
                if decision.reason == "period_rank_unsupported"
                else UNSUPPORTED_COPY
            )
            return self._terminal(
                principal,
                filters,
                status="unsupported",
                intent="unsupported",
                source=source,
                question=question,
                answer=answer,
                empty_reason=decision.reason or "unsupported_intent",
                intent_ms=intent_ms,
                started=started,
                caveats=decision.caveats,
            )

        if decision.operation == "generate_trend":
            return self._ask_generate_trend(
                principal,
                filters,
                decision,
                source=source,
                question=question,
                intent_ms=intent_ms,
                started=started,
            )

        query_started = time.perf_counter()
        try:
            status, reason, evidence, applied, client_id, company = self._run(
                principal, filters, focus, decision
            )
        except ValidationFailed:
            raise
        query_ms = (time.perf_counter() - query_started) * 1000
        if status != "answered":
            answer = {
                "missing_comparison": (
                    "A comparison period is required for this question. "
                    "Choose a published month with a prior month, or ask to summarize "
                    "the current period."
                ),
                "insufficient_evidence": (
                    "There is not enough calculated evidence in the current filters "
                    "to answer that question."
                ),
                "empty_period": "No published history matches the current filters.",
                "period_not_month": "Anomalies require a single published month.",
                "insufficient_history": (
                    "There are not enough prior published months for that anomaly question."
                ),
                "no_anomalies": (
                    "No unusual published-history movements were found for the current filters."
                ),
            }.get(reason or "", UNSUPPORTED_COPY)
            return self._terminal(
                principal,
                filters,
                status="unsupported",
                intent=decision.intent,
                source=source,
                question=question,
                answer=answer,
                empty_reason=reason,
                operation=decision.operation,
                evidence=evidence,
                applied=applied,
                intent_ms=intent_ms,
                query_ms=query_ms,
                started=started,
                client_id=client_id,
                company_name=company,
                caveats=decision.caveats,
            )

        llm_started = time.perf_counter()
        answer, llm_used = self._explain(
            question, evidence, decision.caveats, allow_llm=not intent_llm_called
        )
        llm_ms = (time.perf_counter() - llm_started) * 1000
        next_action = None
        if decision.operation == "explain_metric_change":
            next_action = (
                "Open Performance Explorer or Insights to inspect drivers under the same filters."
            )
        elif decision.operation == "explain_anomaly":
            next_action = (
                "Keep using Anomalies for unusualness versus the multi-month median; "
                "Insights remain the period-vs-comparison view."
            )
        return AskResponse(
            client_id=client_id or "",
            company_name=company,
            status="answered",
            intent=decision.intent,
            operation=decision.operation,
            source=source,
            question=question,
            answer=answer,
            caveats=decision.caveats,
            next_action=next_action,
            empty_reason=None,
            evidence=evidence,
            applied=applied,
            llm_used=llm_used,
            timings=AskTimings(
                intent_ms=_ms(intent_ms),
                query_ms=_ms(query_ms),
                llm_ms=_ms(llm_ms),
                total_ms=_ms((time.perf_counter() - started) * 1000),
            ),
        )

    def _explain(
        self,
        question: str,
        evidence: dict[str, Any],
        caveats: list[str],
        *,
        allow_llm: bool = True,
    ) -> tuple[str, bool]:
        templated = template_answer(evidence, caveats=caveats, question=question)
        if self._llm is None or not allow_llm:
            return templated, False
        blob = json.dumps(evidence, default=str, separators=(",", ":"))
        if len(blob) > 8000:
            blob = blob[:8000]
        user = f"QUESTION: {question[:MAX_QUESTION_CHARS]}\nEVIDENCE: {blob}"
        try:
            raw = self._llm.complete(system=SYSTEM_PROMPT, user=user, purpose="wording")
        except AskLlmError:
            return (
                templated + " The language model was unavailable, so this answer uses the "
                "calculated evidence only.",
                False,
            )
        validated = validate_llm_answer(coerce_llm_wording(raw, evidence), evidence)
        if validated is None:
            return templated, False
        return validated, True

    def _ask_generate_trend(
        self,
        principal: Principal,
        filters: AskFilters,
        decision,
        *,
        source: str,
        question: str,
        intent_ms: float,
        started: float,
    ) -> AskResponse:
        query_started = time.perf_counter()
        try:
            selection, llm_used = self._resolve_generated_trend_selection(question)
        except FilterValidationError as exc:
            return self._terminal(
                principal,
                filters,
                status="unsupported",
                intent="generate_trend",
                source=source,
                question=question,
                answer=str(exc),
                empty_reason="unsupported_selection",
                intent_ms=intent_ms,
                started=started,
                caveats=decision.caveats,
            )
        kwargs = self._scope_kwargs(principal, filters)
        if selection.get("compare") == "none":
            kwargs["compare"] = "none"
            kwargs["compare_month_start"] = None
            kwargs["compare_from"] = None
            kwargs["compare_to"] = None
        try:
            trends = self._analytics.trends(
                **kwargs,
                metric=selection["metric"],
                secondary=selection.get("secondary"),
                grain=selection["grain"],
                breakdown=selection.get("breakdown"),
            )
        except ValidationFailed as exc:
            return self._terminal(
                principal,
                filters,
                status="unsupported",
                intent="generate_trend",
                source=source,
                question=question,
                answer=str(exc),
                empty_reason="unsupported_selection",
                intent_ms=intent_ms,
                query_ms=(time.perf_counter() - query_started) * 1000,
                started=started,
                caveats=decision.caveats,
            )
        query_ms = (time.perf_counter() - query_started) * 1000
        evidence = self._base_evidence(trends, "generate_trend", decision.caveats)
        evidence["selection"] = {
            "metric": selection["metric"],
            "metric_label": trends.metric.label if trends.metric else selection["metric"],
            "secondary": selection.get("secondary"),
            "secondary_label": trends.secondary.label if trends.secondary else None,
            "grain": selection["grain"],
            "breakdown": selection.get("breakdown"),
            "breakdown_label": next(
                (
                    item.label
                    for item in (trends.dimensions or [])
                    if item.value == selection.get("breakdown")
                ),
                selection.get("breakdown"),
            ),
            "compare": selection.get("compare"),
        }
        evidence["metric"] = {
            "metric": selection["metric"],
            "label": trends.metric.label if trends.metric else selection["metric"],
            "current": None,
        }
        if trends.empty or not trends.series:
            return self._terminal(
                principal,
                filters,
                status="unsupported",
                intent="generate_trend",
                source=source,
                question=question,
                answer=(
                    "There is not enough calculated evidence in the current filters "
                    "to answer that question."
                ),
                empty_reason="insufficient_evidence",
                intent_ms=intent_ms,
                query_ms=query_ms,
                started=started,
                client_id=trends.client_id,
                company_name=trends.company_name,
                evidence=evidence,
                applied=trends.applied,
                caveats=decision.caveats,
            )
        series = next((item for item in trends.series if item.key == "total"), trends.series[0])
        points = [point for point in series.points if point.value is not None]
        first = points[0] if points else None
        last = points[-1] if points else None
        evidence["metric"]["current"] = last.value if last else None
        evidence["trend"] = {
            "grain": selection["grain"],
            "point_count": len(series.points),
            "first": first.value if first else None,
            "last": last.value if last else None,
            "first_bucket": str(first.bucket) if first else None,
            "last_bucket": str(last.bucket) if last else None,
        }
        return AskResponse(
            client_id=trends.client_id or "",
            company_name=trends.company_name,
            status="answered",
            intent="generate_trend",
            operation="generate_trend",
            source=source,
            question=question,
            answer=template_answer(evidence, caveats=decision.caveats, question=question),
            caveats=decision.caveats,
            next_action="The Trends panel uses this validated D3 selection.",
            empty_reason=None,
            evidence=evidence,
            applied=trends.applied,
            llm_used=llm_used,
            timings=AskTimings(
                intent_ms=_ms(intent_ms),
                query_ms=_ms(query_ms),
                llm_ms=_ms(0.0),
                total_ms=_ms((time.perf_counter() - started) * 1000),
            ),
        )

    def _resolve_generated_trend_selection(self, question: str) -> tuple[dict[str, Any], bool]:
        if self._llm is not None:
            user = f"QUESTION: {question[:MAX_QUESTION_CHARS]}"
            try:
                raw = self._llm.complete(
                    system=TREND_SELECTION_PROMPT,
                    user=user,
                    response_schema=gemini_trend_selection_schema(),
                    purpose="trend_selection",
                )
            except AskLlmError:
                raw = None
            if raw:
                try:
                    payload = json.loads(raw)
                    return parse_generated_trend_selection(payload), True
                except (ValueError, FilterValidationError, TypeError):
                    pass
        return parse_trend_request_text(question), False

    def _resolve_ask_intent(self, question: str, fallback):
        user = f"QUESTION: {question[:MAX_QUESTION_CHARS]}"
        try:
            raw = self._llm.complete(
                system=ASK_INTENT_PROMPT,
                user=user,
                response_schema=gemini_ask_intent_schema(),
                thinking_level=GEMINI_INTENT_THINKING_LEVEL,
                max_output_tokens=GEMINI_INTENT_MAX_OUTPUT_TOKENS,
                purpose="intent",
            )
        except AskLlmError:
            return fallback
        if not raw:
            return fallback
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError):
            return fallback
        parsed = parse_ask_intent_selection(payload, question=question)
        if parsed is None:
            return fallback
        return apply_operation_spec(parsed)

    def _run(
        self,
        principal: Principal,
        filters: AskFilters,
        focus: AskFocus,
        decision,
    ) -> tuple[
        str, str | None, dict[str, Any], OverviewAppliedState | None, str | None, str | None
    ]:
        kwargs = self._scope_kwargs(principal, filters)
        try:
            spec = operation_spec(decision.operation)
        except FilterValidationError as exc:
            raise ValidationFailed(str(exc)) from exc
        operation = decision.operation
        if decision.metric and decision.metric not in spec["allowed_metrics"]:
            raise ValidationFailed("Unknown Ask metric.")
        metric = (
            decision.metric
            or focus.metric
            or focus.trend_metric
            or focus.explorer_metric
            or "total_cost"
        )
        if metric not in spec["allowed_metrics"]:
            raise ValidationFailed("Unknown Ask metric.")
        allowed_dims = spec["allowed_dimensions"]
        dimension = decision.dimension
        if not dimension and allowed_dims:
            dimension = focus.dimension or focus.explorer_dimension or focus.drill_dimension
        if dimension and allowed_dims and dimension not in allowed_dims:
            raise ValidationFailed("Unknown Ask dimension.")
        if not dimension and allowed_dims:
            dimension = "channel" if "channel" in allowed_dims else allowed_dims[0]

        if spec["contract"] == "GET /analytics/overview":
            overview = self._analytics.overview(**kwargs)
            evidence = self._base_evidence(overview, operation, decision.caveats)
            if not overview.has_published_history or not overview.kpis:
                return (
                    "unsupported",
                    "empty_period",
                    evidence,
                    overview.applied,
                    overview.client_id,
                    overview.company_name,
                )
            if spec.get("requires_comparison") and not overview.comparison.available:
                return (
                    "unsupported",
                    "missing_comparison",
                    evidence,
                    overview.applied,
                    overview.client_id,
                    overview.company_name,
                )
            cards = list(overview.kpis)
            if operation == "summarize_current_context":
                evidence["kpis"] = [compact_kpi(item) for item in cards]
                evidence["metric"] = compact_kpi(cards[0])
            elif operation == "compare_periods":
                ranked = sorted(
                    [item for item in cards if item.delta_pct is not None],
                    key=lambda item: abs(_float(item.delta_pct)),
                    reverse=True,
                )
                evidence["kpis"] = [compact_kpi(item) for item in ranked[:4]]
                chosen = next(
                    (item for item in ranked if item.id == metric),
                    ranked[0] if ranked else cards[0],
                )
                evidence["metric"] = compact_kpi(chosen)
            else:
                chosen = _kpi(cards, metric)
                if chosen is None or chosen.value is None:
                    return (
                        "unsupported",
                        "insufficient_evidence",
                        evidence,
                        overview.applied,
                        overview.client_id,
                        overview.company_name,
                    )
                evidence["metric"] = compact_kpi(chosen)
            return (
                "answered",
                None,
                evidence,
                overview.applied,
                overview.client_id,
                overview.company_name,
            )

        if operation in {"identify_driver", "explain_insight"}:
            named_metric = decision.metric or focus.metric
            insights = self._analytics.insights(
                **kwargs,
                metric=named_metric if named_metric in spec["allowed_metrics"] else None,
                dimension=focus.dimension,
            )
            evidence = self._base_evidence(insights, operation, decision.caveats)
            if insights.empty:
                reason = insights.empty_reason or "insufficient_evidence"
                if reason == "insufficient_comparison":
                    reason = "missing_comparison"
                elif reason == "no_material_insights":
                    reason = "insufficient_evidence"
                return (
                    "unsupported",
                    reason,
                    evidence,
                    insights.applied,
                    insights.client_id,
                    insights.company_name,
                )
            item = None
            if focus.insight_id:
                item = next(
                    (row for row in insights.insights if row.insight_id == focus.insight_id), None
                )
            if item is None and operation == "identify_driver":
                item = next(
                    (row for row in insights.insights if row.category == "dominant_driver"), None
                )
            if item is None and insights.insights:
                item = insights.insights[0]
            if item is None:
                return (
                    "unsupported",
                    "insufficient_evidence",
                    evidence,
                    insights.applied,
                    insights.client_id,
                    insights.company_name,
                )
            evidence["insight"] = {
                "insight_id": item.insight_id,
                "category": item.category,
                "headline": item.headline,
                "explanation": item.explanation,
                "metric": item.metric,
                "label": item.metric_label,
                "current": item.current_value,
                "comparison": item.prior_value,
                "delta": item.delta,
                "delta_pct": item.delta_pct,
                "threshold": item.threshold,
            }
            evidence["metric"] = {
                "metric": item.metric,
                "label": item.metric_label,
                "current": item.current_value,
                "comparison": item.prior_value,
                "delta": item.delta,
                "delta_pct": item.delta_pct,
            }
            evidence["drivers"] = [
                {
                    "dimension": driver.dimension,
                    "dimension_label": driver.dimension_label,
                    "key": driver.key,
                    "label": _clip(driver.label),
                    "current": driver.current_value,
                    "comparison": driver.prior_value,
                    "delta": driver.delta,
                    "delta_pct": driver.delta_pct,
                    "contribution_pct": driver.contribution_pct,
                }
                for driver in (item.drivers or [])[:3]
            ]
            return (
                "answered",
                None,
                evidence,
                insights.applied,
                insights.client_id,
                insights.company_name,
            )

        if operation == "explain_anomaly":
            named_metric = decision.metric or focus.metric
            anomalies = self._analytics.anomalies(
                **kwargs,
                metric=named_metric if named_metric in spec["allowed_metrics"] else None,
                dimension=None
                if (focus.dimension == "day")
                else (decision.dimension or focus.dimension),
            )
            evidence = self._base_evidence(anomalies, operation, decision.caveats)
            if anomalies.empty:
                reason = anomalies.empty_reason or "insufficient_evidence"
                return (
                    "unsupported",
                    reason,
                    evidence,
                    anomalies.applied,
                    anomalies.client_id,
                    anomalies.company_name,
                )
            item = None
            if focus.anomaly_id:
                item = next(
                    (row for row in anomalies.anomalies if row.anomaly_id == focus.anomaly_id), None
                )
            if item is None and anomalies.anomalies:
                item = anomalies.anomalies[0]
            if item is None:
                return (
                    "unsupported",
                    "insufficient_evidence",
                    evidence,
                    anomalies.applied,
                    anomalies.client_id,
                    anomalies.company_name,
                )
            evidence["anomaly"] = {
                "anomaly_id": item.anomaly_id,
                "kind": item.kind,
                "direction": item.direction,
                "headline": item.headline,
                "explanation": item.explanation,
                "metric": item.metric,
                "label": item.metric_label,
                "current": item.current_value,
                "baseline": item.baseline_value,
                "delta": item.delta,
                "delta_pct": item.delta_pct,
                "severity": item.severity,
                "threshold": item.threshold,
                "affected_label": _clip(item.affected_label),
            }
            evidence["metric"] = {
                "metric": item.metric,
                "label": item.metric_label,
                "current": item.current_value,
                "comparison": item.baseline_value,
                "delta": item.delta,
                "delta_pct": item.delta_pct,
            }
            evidence["drivers"] = [
                {
                    "dimension": driver.dimension,
                    "label": _clip(driver.label),
                    "key": driver.key,
                    "contribution_pct": driver.contribution_pct,
                }
                for driver in (item.drivers or [])[:3]
            ]
            return (
                "answered",
                None,
                evidence,
                anomalies.applied,
                anomalies.client_id,
                anomalies.company_name,
            )

        if operation in {"compare_dimensions", "explain_explorer_result"}:
            explorer = self._analytics.explorer(
                **kwargs,
                metric=metric,
                dimension=dimension,
                mode=focus.explorer_mode,
                limit=25,
            )
            evidence = self._base_evidence(explorer, operation, decision.caveats)
            rows = [
                {
                    "rank": row.rank,
                    "key": row.key,
                    "label": _clip(row.label),
                    "current": row.value,
                    "comparison": row.prior_value,
                    "delta": row.delta,
                    "delta_pct": row.delta_pct,
                    "contribution_pct": row.contribution_pct,
                }
                for row in explorer.rows[:MAX_EVIDENCE_ROWS]
            ]
            evidence["metric"] = {
                "metric": explorer.selection.metric if explorer.selection else metric,
                "label": explorer.metric.label if explorer.metric else metric,
            }
            if explorer.selection:
                evidence["explorer"] = {
                    "dimension": explorer.selection.dimension,
                    "mode": explorer.selection.mode,
                    "truncated": explorer.selection.truncated,
                }
            if operation == "compare_dimensions":
                ordered = []
                seen: set[str] = set()
                for token in decision.groups:
                    for row in explorer.rows:
                        if row.key in seen:
                            continue
                        if group_token_matches(token, key=row.key, label=row.label):
                            ordered.append(row)
                            seen.add(row.key)
                            break
                if len(ordered) < 2:
                    evidence["groups"] = rows[:2]
                    return (
                        "unsupported",
                        "insufficient_evidence",
                        evidence,
                        explorer.applied,
                        explorer.client_id,
                        explorer.company_name,
                    )
                group_rows = [
                    {
                        "key": row.key,
                        "label": _clip(row.label),
                        "current": row.value,
                        "comparison": row.prior_value,
                        "delta": row.delta,
                        "delta_pct": row.delta_pct,
                    }
                    for row in ordered[:2]
                ]
                evidence["groups"] = group_rows
                evidence["group_a"] = group_rows[0]
                evidence["group_b"] = group_rows[1]
            else:
                if explorer.empty or not rows:
                    return (
                        "unsupported",
                        "insufficient_evidence",
                        evidence,
                        explorer.applied,
                        explorer.client_id,
                        explorer.company_name,
                    )
                evidence["rows"] = rows
                evidence["metric"]["current"] = rows[0]["current"]
                evidence["metric"]["comparison"] = rows[0]["comparison"]
                evidence["metric"]["delta"] = rows[0]["delta"]
                evidence["metric"]["delta_pct"] = rows[0]["delta_pct"]
            return (
                "answered",
                None,
                evidence,
                explorer.applied,
                explorer.client_id,
                explorer.company_name,
            )

        if operation == "summarize_trend":
            trends = self._analytics.trends(
                **kwargs,
                metric=focus.trend_metric or metric,
                secondary=focus.trend_secondary,
                grain=focus.trend_grain,
                breakdown=focus.trend_breakdown,
            )
            evidence = self._base_evidence(trends, operation, decision.caveats)
            if trends.empty or not trends.series:
                return (
                    "unsupported",
                    "insufficient_evidence",
                    evidence,
                    trends.applied,
                    trends.client_id,
                    trends.company_name,
                )
            series = next((item for item in trends.series if item.key == "total"), trends.series[0])
            points = [point for point in series.points if point.value is not None]
            first = points[0] if points else None
            last = points[-1] if points else None
            evidence["metric"] = {
                "metric": trends.selection.metric if trends.selection else metric,
                "label": trends.metric.label if trends.metric else metric,
                "current": last.value if last else None,
                "comparison": last.comparison_value if last else None,
            }
            evidence["trend"] = {
                "grain": trends.selection.grain if trends.selection else focus.trend_grain,
                "point_count": len(series.points),
                "first": first.value if first else None,
                "last": last.value if last else None,
                "first_bucket": str(first.bucket) if first else None,
                "last_bucket": str(last.bucket) if last else None,
            }
            return "answered", None, evidence, trends.applied, trends.client_id, trends.company_name

        if operation == "explain_drilldown":
            drill = self._analytics.drilldown(
                **kwargs,
                metric=focus.drill_metric or metric,
                dimension=focus.drill_dimension or dimension,
                origin=focus.drill_origin or "kpi",
                parent=list(focus.drill_parents or []),
            )
            evidence = self._base_evidence(drill, operation, decision.caveats)
            if drill.empty or not drill.rows:
                return (
                    "unsupported",
                    "insufficient_evidence",
                    evidence,
                    drill.applied,
                    drill.client_id,
                    drill.company_name,
                )
            rows = [
                {
                    "rank": row.rank,
                    "key": row.key,
                    "label": _clip(row.label),
                    "current": row.value,
                    "comparison": row.prior_value,
                    "delta": row.delta,
                    "delta_pct": row.delta_pct,
                    "contribution_pct": row.contribution_pct,
                }
                for row in drill.rows[:MAX_EVIDENCE_ROWS]
            ]
            evidence["rows"] = rows
            evidence["metric"] = {
                "metric": drill.selection.metric if drill.selection else metric,
                "label": drill.metric.label if drill.metric else metric,
                "current": rows[0]["current"],
                "comparison": rows[0]["comparison"],
                "delta": rows[0]["delta"],
                "delta_pct": rows[0]["delta_pct"],
            }
            evidence["drill"] = {
                "dimension": drill.selection.dimension if drill.selection else dimension,
                "depth": drill.selection.depth if drill.selection else None,
            }
            return "answered", None, evidence, drill.applied, drill.client_id, drill.company_name

        raise ValidationFailed("Unknown Ask operation.")

    def _scope_kwargs(self, principal: Principal, filters: AskFilters) -> dict[str, Any]:
        return {
            "principal": principal,
            "requested_client_id": filters.client_id,
            "period": filters.period,
            "month_start": filters.month_start,
            "day_from": filters.day_from,
            "day_to": filters.day_to,
            "compare": filters.compare,
            "compare_month_start": filters.compare_month_start,
            "compare_from": filters.compare_from,
            "compare_to": filters.compare_to,
            "campaign_ids": filters.campaign_id,
            "channels": filters.channel,
            "filter_logic_1": filters.filter_logic_1,
            "filter_logic_1_group": filters.filter_logic_1_group,
        }

    def _base_evidence(self, response: Any, operation: str, caveats: list[str]) -> dict[str, Any]:
        period = getattr(response, "period", None)
        comparison = getattr(response, "comparison", None)
        applied = getattr(response, "applied", None)
        evidence: dict[str, Any] = {
            "operation": operation,
            "company": getattr(response, "company_name", None),
            "caveats": list(caveats),
        }
        if period is not None:
            evidence["period"] = {
                "grain": getattr(period, "grain", None),
                "month_start": str(period.month_start)
                if getattr(period, "month_start", None)
                else None,
                "month_label": getattr(period, "month_label", None),
                "day_min": str(period.day_min) if getattr(period, "day_min", None) else None,
                "day_max": str(period.day_max) if getattr(period, "day_max", None) else None,
            }
        if comparison is not None:
            evidence["comparison"] = {
                "available": comparison.available,
                "month_start": str(comparison.month_start)
                if getattr(comparison, "month_start", None)
                else None,
                "month_label": getattr(comparison, "month_label", None),
                "reason": getattr(comparison, "reason", None),
            }
        if applied is not None:
            evidence["filters"] = {
                "period": applied.period,
                "compare": applied.compare,
                "campaign_ids": _limit_list(applied.campaign_ids),
                "channels": _limit_list(applied.channels),
                "filter_logic_1": _limit_list(applied.filter_logic_1),
                "filter_logic_1_group": _limit_list(applied.filter_logic_1_group),
            }
        return evidence

    def _terminal(
        self,
        principal: Principal,
        filters: AskFilters,
        *,
        status: str,
        intent: str,
        source: str,
        question: str,
        answer: str,
        empty_reason: str | None,
        intent_ms: float,
        started: float,
        query_ms: float = 0.0,
        operation: str | None = None,
        evidence: dict[str, Any] | None = None,
        applied: OverviewAppliedState | None = None,
        client_id: str | None = None,
        company_name: str | None = None,
        caveats: list[str] | None = None,
    ) -> AskResponse:
        return AskResponse(
            client_id=client_id or principal.client_id or filters.client_id or "",
            company_name=company_name,
            status=status,  # type: ignore[arg-type]
            intent=intent,
            operation=operation,
            source=source,  # type: ignore[arg-type]
            question=question,
            answer=answer,
            caveats=list(caveats or []),
            next_action=None,
            empty_reason=empty_reason,
            evidence=evidence or {},
            applied=applied,
            llm_used=False,
            timings=AskTimings(
                intent_ms=_ms(intent_ms),
                query_ms=_ms(query_ms),
                llm_ms="0.0",
                total_ms=_ms((time.perf_counter() - started) * 1000),
            ),
        )


def _kpi(cards: list[OverviewKpiCard], metric: str) -> OverviewKpiCard | None:
    return next((item for item in cards if item.id == metric), cards[0] if cards else None)


def _float(raw: object) -> float:
    try:
        return abs(float(str(raw)))
    except (TypeError, ValueError):
        return 0.0


def _ms(value: float) -> str:
    return f"{value:.1f}"
