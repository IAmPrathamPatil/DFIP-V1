import { KPI_TO_TREND_METRIC } from "./analytics-state.js";
import { html, raw } from "./format.js";

export function trendMetricForKpi(kpiId) {
  return KPI_TO_TREND_METRIC[kpiId] || "";
}

export function sparklinePointsForMetric(trend, metricKey) {
  const series = Array.isArray(trend && trend.series) ? trend.series : [];
  const total = series.find((item) => item && item.key === "total") || series[0];
  const points = total && Array.isArray(total.points) ? total.points : [];
  return points.map((point) => {
    const values = point && point.values;
    if (!values || typeof values !== "object") return null;
    const rawValue = values[metricKey];
    if (rawValue == null || rawValue === "") return null;
    const number = Number(rawValue);
    return Number.isFinite(number) ? number : null;
  });
}

export function renderKpiSparkline(values) {
  const nums = Array.isArray(values) ? values : [];
  const valid = nums.filter((item) => item != null);
  if (!valid.length) return "";
  const width = 120;
  const height = 28;
  const padX = 1.5;
  const padY = 3.5;
  const innerW = width - padX * 2;
  const innerH = height - padY * 2;
  const min = Math.min(...valid);
  const max = Math.max(...valid);
  const span = max - min;
  const xAt = (index) => {
    if (nums.length <= 1) return width / 2;
    return padX + (index / (nums.length - 1)) * innerW;
  };
  const yAt = (value) => {
    if (!(span > 0)) return padY + innerH / 2;
    return padY + innerH - ((value - min) / span) * innerH;
  };
  const parts = [];
  let segment = [];
  const flush = () => {
    if (!segment.length) return;
    if (segment.length === 1) {
      const index = segment[0];
      parts.push(
        `<circle cx="${xAt(index).toFixed(1)}" cy="${yAt(nums[index]).toFixed(1)}" r="1.5" fill="currentColor" />`,
      );
    } else {
      let d = "";
      for (const index of segment) {
        d += `${d ? "L" : "M"}${xAt(index).toFixed(1)} ${yAt(nums[index]).toFixed(1)} `;
      }
      parts.push(`<path d="${d.trim()}" />`);
    }
    segment = [];
  };
  for (let i = 0; i < nums.length; i += 1) {
    if (nums[i] == null) flush();
    else segment.push(i);
  }
  flush();
  if (!parts.length) return "";
  return html`<span class="kpi-sparkline-wrap" data-kpi-sparkline-chart="true" aria-hidden="true"><svg class="kpi-sparkline" viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" focusable="false">${raw(parts.join(""))}</svg></span>`;
}

export function kpiSparklineMarkup(trend, kpiId) {
  const metricKey = trendMetricForKpi(kpiId);
  if (!metricKey) return "";
  return renderKpiSparkline(sparklinePointsForMetric(trend, metricKey));
}

export function contextualTrendFromSparkline(trend, kpiId) {
  const metricKey = trendMetricForKpi(kpiId);
  if (!trend || !metricKey) return null;
  const series = Array.isArray(trend.series) ? trend.series : [];
  const total = series.find((item) => item && item.key === "total") || series[0];
  const sourcePoints = total && Array.isArray(total.points) ? total.points : [];
  if (!sourcePoints.length) return null;
  const points = sourcePoints.map((point) => {
    const values = point && point.values;
    const hasMap = values && typeof values === "object";
    const rawValue = hasMap ? values[metricKey] : point && point.value;
    return {
      bucket: point.bucket,
      bucket_label: point.bucket_label,
      value: rawValue,
      comparison_value: null,
    };
  });
  if (!points.some((point) => point.value != null && point.value !== "")) return null;
  const catalog = Array.isArray(trend.metrics) ? trend.metrics : [];
  const info = catalog.find((item) => item && item.key === metricKey) || {};
  return {
    client_id: trend.client_id,
    has_published_history: trend.has_published_history,
    period: trend.period,
    comparison: trend.comparison,
    comparison_shown: false,
    metric: { ...(trend.metric || {}), ...info, id: metricKey, key: metricKey },
    secondary: null,
    selection: {
      metric: metricKey,
      secondary: null,
      grain: (trend.selection && trend.selection.grain) || "day",
      breakdown: null,
      dual_axis: false,
      chart: "line",
    },
    series: [{ key: "total", label: (total && total.label) || "Company total", points }],
  };
}
