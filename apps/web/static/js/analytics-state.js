/** Shared Overview analytical state. URL query is the D2/D3/D4 persistence layer. */

export const ANALYTICS_SINGLE = [
  "period",
  "month_start",
  "day_from",
  "day_to",
  "compare",
  "compare_month_start",
  "compare_from",
  "compare_to",
];

export const ANALYTICS_MULTI = ["campaign_id", "channel", "filter_logic_1", "filter_logic_1_group"];

export const TREND_KEYS = ["trend_metric", "trend_secondary", "trend_grain", "trend_breakdown"];

export const TREND_API_KEYS = {
  trend_metric: "metric",
  trend_secondary: "secondary",
  trend_grain: "grain",
  trend_breakdown: "breakdown",
};

export const DRILL_SINGLE = ["drill", "drill_metric", "drill_dimension", "drill_slice", "drill_slice_grain"];
export const DRILL_MULTI = ["drill_parent"];

export const EXPLORER_KEYS = [
  "ex_metric",
  "ex_dimension",
  "ex_secondary",
  "ex_mode",
  "ex_dir",
  "ex_limit",
  "ex_sort",
  "ex_mover",
  "ex_min",
  "ex_min_contrib",
];

export const FOCUS_KEYS = ["kpi", "insight", "anomaly", "ex_row", "trend_point"];
export const SAVED_KEY = "saved";

export const SPARKLINE_KPI_METRICS = [
  "total_cost",
  "revenue_inr",
  "overall_roas",
  "delivered",
  "unique_clicks",
  "unique_conversions",
  "delivery_rate",
  "ctr_del_to_clicks",
];

export const KPI_TO_TREND_METRIC = {
  total_cost: "total_cost",
  revenue: "revenue_inr",
  revenue_inr: "revenue_inr",
  overall_roas: "overall_roas",
  delivered: "delivered",
  unique_clicks: "unique_clicks",
  unique_conversions: "unique_conversions",
  delivery_rate: "delivery_rate",
  ctr: "ctr_del_to_clicks",
  ctr_del_to_clicks: "ctr_del_to_clicks",
};

export const WORKSPACE_SINGLE = [
  ...ANALYTICS_SINGLE,
  ...TREND_KEYS,
  ...DRILL_SINGLE,
  ...EXPLORER_KEYS,
  ...FOCUS_KEYS,
];

export const WORKSPACE_MULTI = [...ANALYTICS_MULTI, ...DRILL_MULTI];

export const EXPLORER_API_KEYS = {
  ex_metric: "metric",
  ex_dimension: "dimension",
  ex_secondary: "secondary",
  ex_mode: "mode",
  ex_dir: "direction",
  ex_limit: "limit",
  ex_sort: "sort",
  ex_mover: "mover",
  ex_min: "min_value",
  ex_min_contrib: "min_contribution",
};

function currentSearch() {
  try {
    return new URLSearchParams(window.location.search);
  } catch (error) {
    return new URLSearchParams();
  }
}

function copyKeys(source, keys, query) {
  for (const key of keys) {
    const value = source.get(key);
    if (value) query.set(key, value);
  }
}

function copyMulti(source, keys, query) {
  for (const key of keys) {
    for (const value of source.getAll(key)) {
      query.append(key, String(value));
    }
  }
}

export function overviewHref(query) {
  const encoded = query instanceof URLSearchParams ? query.toString() : "";
  return encoded ? `/client/overview?${encoded}` : "/client/overview";
}

export function filterParamsFromQuery(query, scoped) {
  const next = { ...scoped };
  for (const key of ANALYTICS_SINGLE) {
    const value = query.get(key);
    if (value) next[key] = value;
  }
  for (const key of ANALYTICS_MULTI) {
    const values = query.getAll(key);
    if (values.length) next[key] = values;
  }
  if (next.compare === "none") {
    delete next.compare_month_start;
    delete next.compare_from;
    delete next.compare_to;
  }
  return next;
}

export function overviewParamsFromQuery(query, scoped) {
  const next = filterParamsFromQuery(query, scoped);
  for (const [urlKey, apiKey] of Object.entries(TREND_API_KEYS)) {
    const value = query.get(urlKey);
    if (value) next[apiKey] = value;
  }
  return next;
}

export function sparklineParamsFromQuery(query, scoped) {
  const next = filterParamsFromQuery(query, scoped);
  next.compare = "none";
  delete next.compare_month_start;
  delete next.compare_from;
  delete next.compare_to;
  next.grain = next.period === "all_history" ? "month" : "day";
  next.include_metric = [...SPARKLINE_KPI_METRICS];
  return next;
}

export function drillParamsFromQuery(query, scoped) {
  const next = filterParamsFromQuery(query, scoped);
  const origin = query.get("drill");
  if (origin) next.origin = origin;
  const metric = query.get("drill_metric");
  if (metric) next.metric = metric;
  const dimension = query.get("drill_dimension");
  if (dimension) next.dimension = dimension;
  const slice = query.get("drill_slice");
  if (slice) next.slice_bucket = slice;
  const sliceGrain = query.get("drill_slice_grain");
  if (sliceGrain) next.slice_grain = sliceGrain;
  const parents = query.getAll("drill_parent");
  if (parents.length) next.parent = parents;
  return next;
}

export function parseDrillQuery(query) {
  const origin = query && query.get ? query.get("drill") : "";
  if (!origin) return null;
  return {
    origin,
    metric: query.get("drill_metric") || "total_cost",
    dimension: query.get("drill_dimension") || "campaign_id",
    slice: query.get("drill_slice") || "",
    sliceGrain: query.get("drill_slice_grain") || "",
    parents: query.getAll("drill_parent"),
  };
}

export function drillTrendParamsFromQuery(query, scoped) {
  const next = filterParamsFromQuery(query, scoped);
  const parsed = parseDrillQuery(query);
  const rawMetric = parsed && parsed.metric ? parsed.metric : "total_cost";
  next.metric = KPI_TO_TREND_METRIC[rawMetric] || rawMetric;
  next.grain = next.period === "all_history" ? "month" : "day";
  delete next.secondary;
  delete next.breakdown;
  delete next.include_metric;
  return next;
}

export function stripDrill(query) {
  const next = new URLSearchParams(query instanceof URLSearchParams ? query : currentSearch());
  for (const key of DRILL_SINGLE) next.delete(key);
  next.delete("drill_parent");
  return next;
}

export function withDrill(query, { origin, metric, dimension, parents, slice, sliceGrain }) {
  const next = stripDrill(query);
  if (origin) next.set("drill", origin);
  if (metric && metric !== "total_cost") next.set("drill_metric", metric);
  if (dimension && dimension !== "campaign_id") next.set("drill_dimension", dimension);
  if (slice) {
    next.set("drill_slice", slice);
    if (sliceGrain && sliceGrain !== "day") next.set("drill_slice_grain", sliceGrain);
  }
  for (const parent of parents || []) {
    next.append("drill_parent", parent);
  }
  return next;
}

export function queryFromOverviewForm(form) {
  const data = new FormData(form);
  const query = new URLSearchParams();
  const period = String(data.get("period") || "month");
  if (period && period !== "month") query.set("period", period);
  const month = String(data.get("month_start") || "").trim();
  const latest = form.dataset.latestMonth || "";
  if (period === "month" && month && month !== latest) query.set("month_start", month);
  if (period === "range") {
    const fromEl = form.elements.namedItem("day_from");
    const toEl = form.elements.namedItem("day_to");
    const from = String((fromEl && "value" in fromEl && fromEl.value) || data.get("day_from") || "").trim();
    const to = String((toEl && "value" in toEl && toEl.value) || data.get("day_to") || "").trim();
    query.set("period", "range");
    if (from) query.set("day_from", from);
    if (to) query.set("day_to", to);
  }
  if (period === "all_history") query.set("period", "all_history");
  const compare = String(data.get("compare") || "auto");
  const compareMonth = String(data.get("compare_month_start") || "").trim();
  if (compare === "none" || compareMonth === "none") {
    query.set("compare", "none");
  } else if (compareMonth) {
    query.set("compare_month_start", compareMonth);
  }
  copyMulti(data, ANALYTICS_MULTI, query);
  const existing = currentSearch();
  copyKeys(existing, TREND_KEYS, query);
  copyKeys(existing, DRILL_SINGLE, query);
  copyMulti(existing, DRILL_MULTI, query);
  copyKeys(existing, EXPLORER_KEYS, query);
  copyKeys(existing, FOCUS_KEYS, query);
  return query;
}

export function queryFromTrendForm(form, currentQuery) {
  const source = currentQuery instanceof URLSearchParams ? currentQuery : currentSearch();
  const query = new URLSearchParams();
  copyKeys(source, ANALYTICS_SINGLE, query);
  copyMulti(source, ANALYTICS_MULTI, query);
  copyKeys(source, DRILL_SINGLE, query);
  copyMulti(source, DRILL_MULTI, query);
  copyKeys(source, EXPLORER_KEYS, query);
  copyKeys(source, FOCUS_KEYS, query);
  const data = new FormData(form);
  const metric = String(data.get("trend_metric") || "total_cost").trim();
  if (metric && metric !== "total_cost") query.set("trend_metric", metric);
  const secondary = String(data.get("trend_secondary") || "").trim();
  if (secondary) query.set("trend_secondary", secondary);
  const grain = String(data.get("trend_grain") || "day").trim();
  if (grain && grain !== "day") query.set("trend_grain", grain);
  const breakdown = String(data.get("trend_breakdown") || "").trim();
  if (breakdown) query.set("trend_breakdown", breakdown);
  return query;
}

export function explorerParamsFromQuery(query, scoped) {
  const next = filterParamsFromQuery(query, scoped);
  for (const [urlKey, apiKey] of Object.entries(EXPLORER_API_KEYS)) {
    const value = query.get(urlKey);
    if (value) next[apiKey] = value;
  }
  return next;
}

export function insightsParamsFromQuery(query, scoped) {
  return filterParamsFromQuery(query, scoped);
}

export function anomaliesParamsFromQuery(query, scoped) {
  return filterParamsFromQuery(query, scoped);
}

export function askFiltersFromQuery(query) {
  const params = filterParamsFromQuery(query, {});
  delete params.client_id;
  const filters = {};
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    filters[key] = value;
  }
  return filters;
}

export function askPayloadFromForm(form, query) {
  const data = new FormData(form);
  const question = String(data.get("question") || "").trim();
  const source = String(data.get("source") || "overview");
  const focus = {};
  const keys = [
    "metric",
    "dimension",
    "insight_id",
    "anomaly_id",
    "trend_metric",
    "trend_secondary",
    "trend_grain",
    "trend_breakdown",
    "explorer_metric",
    "explorer_dimension",
    "explorer_mode",
    "drill_origin",
    "drill_metric",
    "drill_dimension",
  ];
  for (const key of keys) {
    const value = String(data.get(key) || "").trim();
    if (value) focus[key] = value;
  }
  const parents = String(data.get("drill_parents") || "").trim();
  if (parents) focus.drill_parents = parents.split("|").filter(Boolean);
  if (!focus.trend_metric && query && query.get("trend_metric")) focus.trend_metric = query.get("trend_metric");
  if (!focus.trend_grain && query && query.get("trend_grain")) focus.trend_grain = query.get("trend_grain");
  if (!focus.trend_breakdown && query && query.get("trend_breakdown")) focus.trend_breakdown = query.get("trend_breakdown");
  if (!focus.explorer_metric && query && query.get("ex_metric")) focus.explorer_metric = query.get("ex_metric");
  if (!focus.explorer_dimension && query && query.get("ex_dimension")) focus.explorer_dimension = query.get("ex_dimension");
  if (!focus.explorer_mode && query && query.get("ex_mode")) focus.explorer_mode = query.get("ex_mode");
  const drill = parseDrillQuery(query);
  if (drill) {
    if (!focus.drill_origin) focus.drill_origin = drill.origin;
    if (!focus.drill_metric) focus.drill_metric = drill.metric;
    if (!focus.drill_dimension) focus.drill_dimension = drill.dimension;
    if (!focus.drill_parents && drill.parents && drill.parents.length) focus.drill_parents = drill.parents;
  }
  return { question, source, filters: askFiltersFromQuery(query), focus };
}

export function generateTrendAskPayload(question, query) {
  const params = query instanceof URLSearchParams ? query : new URLSearchParams();
  return {
    question: String(question || "").trim(),
    source: "overview",
    filters: askFiltersFromQuery(params),
  };
}

export function generatedTrendSelectionFromAsk(body) {
  if (!body || typeof body !== "object") return null;
  if (body.intent !== "generate_trend" || body.status !== "answered") return null;
  const selection = body.evidence && body.evidence.selection;
  if (!selection || typeof selection !== "object") return null;
  return selection;
}

export function queryFromExplorerForm(form, currentQuery) {
  const source = currentQuery instanceof URLSearchParams ? currentQuery : currentSearch();
  const query = new URLSearchParams();
  copyKeys(source, ANALYTICS_SINGLE, query);
  copyMulti(source, ANALYTICS_MULTI, query);
  copyKeys(source, TREND_KEYS, query);
  copyKeys(source, DRILL_SINGLE, query);
  copyMulti(source, DRILL_MULTI, query);
  copyKeys(source, FOCUS_KEYS, query);
  const data = new FormData(form);
  const metric = String(data.get("ex_metric") || "total_cost").trim();
  if (metric && metric !== "total_cost") query.set("ex_metric", metric);
  const dimension = String(data.get("ex_dimension") || "campaign_id").trim();
  if (dimension && dimension !== "campaign_id") query.set("ex_dimension", dimension);
  const secondary = String(data.get("ex_secondary") || "").trim();
  if (secondary) query.set("ex_secondary", secondary);
  const mode = String(data.get("ex_mode") || "ranking").trim();
  if (mode && mode !== "ranking") query.set("ex_mode", mode);
  const direction = String(data.get("ex_dir") || "").trim();
  if (direction && direction !== "desc") query.set("ex_dir", direction);
  const sort = String(data.get("ex_sort") || "").trim();
  if (sort && sort !== "value") query.set("ex_sort", sort);
  const limit = String(data.get("ex_limit") || "").trim();
  if (limit) query.set("ex_limit", limit);
  const mover = String(data.get("ex_mover") || "up").trim();
  if (mode === "movers" && mover && mover !== "up") query.set("ex_mover", mover);
  const minValue = String(data.get("ex_min") || "").trim();
  if (minValue) query.set("ex_min", minValue);
  const minContrib = String(data.get("ex_min_contrib") || "").trim();
  if (minContrib) query.set("ex_min_contrib", minContrib);
  return query;
}

export function withExplorerSort(query, sort) {
  const next = new URLSearchParams(query instanceof URLSearchParams ? query : currentSearch());
  const currentSort = next.get("ex_sort") || (next.get("ex_mode") === "movers" ? "delta" : "value");
  const currentDir = next.get("ex_dir") || "desc";
  let nextDir = "desc";
  if (currentSort === sort) nextDir = currentDir === "asc" ? "desc" : "asc";
  next.delete("ex_mode");
  next.delete("ex_mover");
  if (sort === "value") next.delete("ex_sort");
  else next.set("ex_sort", sort);
  if (nextDir === "desc") next.delete("ex_dir");
  else next.set("ex_dir", nextDir);
  return next;
}

export function workspaceStateFromQuery(query) {
  const source = query instanceof URLSearchParams ? query : currentSearch();
  const state = {};
  for (const key of WORKSPACE_SINGLE) {
    const value = source.get(key);
    if (value) state[key] = value;
  }
  for (const key of WORKSPACE_MULTI) {
    const values = source.getAll(key);
    if (values.length) state[key] = values;
  }
  if (state.compare === "none") {
    delete state.compare_month_start;
    delete state.compare_from;
    delete state.compare_to;
  }
  return state;
}

export function queryFromWorkspaceState(state) {
  const query = new URLSearchParams();
  const source = state && typeof state === "object" ? state : {};
  const compareNone = String(source.compare || "") === "none";
  for (const key of WORKSPACE_SINGLE) {
    if (compareNone && (key === "compare_month_start" || key === "compare_from" || key === "compare_to")) continue;
    const value = source[key];
    if (value) query.set(key, String(value));
  }
  for (const key of WORKSPACE_MULTI) {
    const values = source[key];
    if (!values) continue;
    const list = Array.isArray(values) ? values : [values];
    for (const value of list) {
      if (value) query.append(key, String(value));
    }
  }
  return query;
}

export function withFocus(query, patch) {
  const next = new URLSearchParams(query instanceof URLSearchParams ? query : currentSearch());
  const entries = patch && typeof patch === "object" ? patch : {};
  for (const [key, value] of Object.entries(entries)) {
    if (!FOCUS_KEYS.includes(key) && key !== SAVED_KEY) continue;
    if (!value) next.delete(key);
    else next.set(key, String(value));
  }
  return next;
}

export function withTrendMetric(query, metric) {
  const next = withFocus(query, { kpi: metric });
  if (metric && metric !== "total_cost") next.set("trend_metric", metric);
  else next.delete("trend_metric");
  return next;
}

export function withTrendSelection(query, selection, compare) {
  const next = new URLSearchParams(query instanceof URLSearchParams ? query : currentSearch());
  for (const key of TREND_KEYS) next.delete(key);
  const metric = selection && selection.metric;
  if (metric && metric !== "total_cost") next.set("trend_metric", metric);
  if (selection && selection.secondary) next.set("trend_secondary", selection.secondary);
  if (selection && selection.grain && selection.grain !== "day") next.set("trend_grain", selection.grain);
  if (selection && selection.breakdown) next.set("trend_breakdown", selection.breakdown);
  if (compare === "none" || (selection && selection.compare === "none")) next.set("compare", "none");
  return next;
}

export function withExplorerFromTrend(query) {
  const next = new URLSearchParams(query instanceof URLSearchParams ? query : currentSearch());
  const metric = next.get("trend_metric");
  if (metric && metric !== "total_cost") next.set("ex_metric", metric);
  else next.delete("ex_metric");
  const breakdown = next.get("trend_breakdown");
  if (breakdown) next.set("ex_dimension", breakdown);
  else next.delete("ex_dimension");
  return next;
}

export function exportParamsFromQuery(query, scoped) {
  const next = explorerParamsFromQuery(query, scoped);
  const drill = parseDrillQuery(query);
  if (drill) {
    next.origin = drill.origin;
    next.drill_metric = drill.metric;
    next.drill_dimension = drill.dimension;
    if (drill.slice) next.slice_bucket = drill.slice;
    if (drill.sliceGrain) next.slice_grain = drill.sliceGrain;
    if (drill.parents && drill.parents.length) next.parent = drill.parents;
  }
  if (query.get("trend_metric")) next.metric = query.get("trend_metric");
  if (query.get("trend_secondary")) next.secondary = query.get("trend_secondary");
  if (query.get("trend_grain")) next.grain = query.get("trend_grain");
  if (query.get("trend_breakdown")) next.breakdown = query.get("trend_breakdown");
  if (query.get("ex_secondary")) next.explorer_secondary = query.get("ex_secondary");
  return next;
}
