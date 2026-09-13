import { escapeHtml, html, raw } from "./format.js";

const SERIES_COLORS = ["#4c8dff", "#3dd68c", "#e3b341", "#f07178", "#c4a0ff", "#7ec2ff", "#6aa0ff", "#8b98a8", "#b7c3d2"];

function asNumber(value) {
  if (value == null || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function formatCompact(kind, value) {
  const number = asNumber(value);
  if (number == null) return "n/a";
  if (kind === "rate") {
    return `${(number * 100).toLocaleString("en-US", { maximumFractionDigits: 1 })}%`;
  }
  if (kind === "roas") {
    return number.toLocaleString("en-US", { maximumFractionDigits: 2 });
  }
  if (kind === "count") {
    if (Math.abs(number) >= 1_000_000) return `${(number / 1_000_000).toLocaleString("en-US", { maximumFractionDigits: 1 })}M`;
    if (Math.abs(number) >= 10_000) return `${(number / 1_000).toLocaleString("en-US", { maximumFractionDigits: 1 })}k`;
    return number.toLocaleString("en-US");
  }
  if (Math.abs(number) >= 1_000_000) return `${(number / 1_000_000).toLocaleString("en-US", { maximumFractionDigits: 1 })}M`;
  if (Math.abs(number) >= 10_000) return `${(number / 1_000).toLocaleString("en-US", { maximumFractionDigits: 1 })}k`;
  return number.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

function formatExact(kind, value) {
  const number = asNumber(value);
  if (number == null) return "n/a";
  if (kind === "rate") {
    return `${(number * 100).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 4 })}%`;
  }
  if (kind === "count") return number.toLocaleString("en-US");
  return number.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 4 });
}

function formatTooltipValue(kind, value) {
  const number = asNumber(value);
  if (number == null) return "n/a";
  if (kind === "rate") {
    return `${(number * 100).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}%`;
  }
  if (kind === "roas") {
    return number.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 4 });
  }
  if (kind === "count") return number.toLocaleString("en-US");
  return number.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export function trendTooltipModel(point, options) {
  const opts = options && typeof options === "object" ? options : {};
  const primary = opts.primary || {};
  const secondary = opts.secondary || null;
  const comparisonShown = Boolean(opts.comparisonShown);
  const metricLabel = primary.label || "Metric";
  const rows = [
    {
      key: "metric",
      label: metricLabel,
      value: formatTooltipValue(primary.kind, point && point.value),
      raw: point && point.value,
    },
  ];
  if (comparisonShown) {
    rows.push({
      key: "comparison",
      label: "Comparison",
      value: formatTooltipValue(primary.kind, point && point.comparison_value),
      raw: point && point.comparison_value,
    });
  }
  if (secondary) {
    rows.push({
      key: "secondary",
      label: secondary.label || "Secondary",
      value: formatTooltipValue(secondary.kind, point && point.secondary_value),
      raw: point && point.secondary_value,
    });
    if (comparisonShown) {
      rows.push({
        key: "comparison_secondary",
        label: `${secondary.label || "Secondary"} comparison`,
        value: formatTooltipValue(secondary.kind, point && point.comparison_secondary_value),
        raw: point && point.comparison_secondary_value,
      });
    }
  }
  return {
    bucket: point && point.bucket,
    bucket_label: (point && (point.bucket_label || point.bucket)) || "",
    series: opts.seriesLabel || "",
    metric: metricLabel,
    rows,
  };
}

function niceTicks(min, max, count) {
  if (!(max > min)) return [min, max];
  const span = max - min;
  const raw = span / Math.max(1, count - 1);
  const mag = 10 ** Math.floor(Math.log10(raw || 1));
  const residual = raw / mag;
  const step = residual >= 5 ? 5 * mag : residual >= 2 ? 2 * mag : mag;
  const start = Math.floor(min / step) * step;
  const ticks = [];
  for (let value = start; value <= max + step / 2; value += step) {
    ticks.push(value);
  }
  return ticks.length ? ticks : [min, max];
}

function pathFor(xs, ys, xAt, yAt) {
  let d = "";
  for (let i = 0; i < xs.length; i += 1) {
    if (ys[i] == null) continue;
    const command = d ? "L" : "M";
    d += `${command}${xAt(i).toFixed(1)} ${yAt(ys[i]).toFixed(1)} `;
  }
  return d.trim();
}

export function renderTrendChart(trend, options) {
  const opts = options && typeof options === "object" ? options : {};
  const interactive = opts.interactive !== false;
  const compact = Boolean(opts.compact);
  const tooltip = Boolean(opts.tooltip) && !compact;
  const selection = trend.selection || {};
  const primary = trend.metric || {};
  const secondary = compact ? null : trend.secondary;
  const series = Array.isArray(trend.series) ? trend.series : [];
  const first = series[0];
  const labels = first && Array.isArray(first.points) ? first.points.map((point) => point.bucket_label) : [];
  const dual = Boolean(!compact && selection.dual_axis && secondary);
  const chart = compact ? "line" : selection.chart === "bar" ? "bar" : "line";
  const width = compact ? 640 : tooltip ? 1000 : 920;
  const height = compact ? 200 : tooltip ? 400 : 320;
  const left = dual ? 64 : compact ? 44 : 52;
  const right = dual ? 64 : compact ? 16 : 20;
  const top = 16;
  const bottom = 42;
  const innerW = width - left - right;
  const innerH = height - top - bottom;
  const n = Math.max(labels.length, 1);

  const primaryValues = [];
  const secondaryValues = [];
  for (const item of series) {
    for (const point of item.points || []) {
      const leftValue = asNumber(point.value);
      if (leftValue != null) primaryValues.push(leftValue);
      if (trend.comparison_shown) {
        const compareValue = asNumber(point.comparison_value);
        if (compareValue != null) primaryValues.push(compareValue);
      }
      if (secondary) {
        const rightValue = asNumber(point.secondary_value);
        if (rightValue != null) secondaryValues.push(rightValue);
        if (trend.comparison_shown) {
          const compareRight = asNumber(point.comparison_secondary_value);
          if (compareRight != null) secondaryValues.push(compareRight);
        }
      }
    }
  }
  const primaryMin = primaryValues.length ? Math.min(0, ...primaryValues) : 0;
  const primaryMax = primaryValues.length ? Math.max(...primaryValues, 0) : 1;
  const secondaryMin = secondaryValues.length ? Math.min(0, ...secondaryValues) : 0;
  const secondaryMax = secondaryValues.length ? Math.max(...secondaryValues, 0) : 1;
  const yTicks = niceTicks(primaryMin, primaryMax === primaryMin ? primaryMax + 1 : primaryMax, 5);
  const yMax = yTicks[yTicks.length - 1];
  const yMin = yTicks[0];
  const y2Ticks = dual ? niceTicks(secondaryMin, secondaryMax === secondaryMin ? secondaryMax + 1 : secondaryMax, 5) : [];
  const y2Max = dual ? y2Ticks[y2Ticks.length - 1] : 1;
  const y2Min = dual ? y2Ticks[0] : 0;
  const yAt = (value) => top + innerH - ((value - yMin) / (yMax - yMin || 1)) * innerH;
  const y2At = (value) => top + innerH - ((value - y2Min) / (y2Max - y2Min || 1)) * innerH;
  const xAt = (index) => left + (n === 1 ? innerW / 2 : (index / (n - 1)) * innerW);
  const barWidth = Math.max(6, Math.min(28, (innerW / Math.max(n, 1) - 8) / Math.max(series.length, 1)));

  const grid = yTicks.map(
    (tick) =>
      `<line class="trend-grid" x1="${left}" x2="${width - right}" y1="${yAt(tick).toFixed(1)}" y2="${yAt(tick).toFixed(1)}" />`,
  );
  const yLabels = yTicks.map(
    (tick) =>
      `<text class="trend-axis-label" x="${left - 8}" y="${yAt(tick).toFixed(1)}" text-anchor="end" dominant-baseline="middle">${formatCompact(primary.kind, tick)}</text>`,
  );
  const y2Labels = dual
    ? y2Ticks.map(
        (tick) =>
          `<text class="trend-axis-label" x="${width - right + 8}" y="${y2At(tick).toFixed(1)}" text-anchor="start" dominant-baseline="middle">${formatCompact(secondary.kind, tick)}</text>`,
      )
    : [];
  const xLabels = labels.map((label, index) => {
    const show = n <= 12 || index === 0 || index === n - 1 || index % Math.ceil(n / 8) === 0;
    if (!show) return "";
    return `<text class="trend-axis-label" x="${xAt(index).toFixed(1)}" y="${height - 14}" text-anchor="middle">${escapeHtml(label)}</text>`;
  });

  const plotted = [];
  series.forEach((item, seriesIndex) => {
    const color = SERIES_COLORS[seriesIndex % SERIES_COLORS.length];
    const points = item.points || [];
    const xs = points.map((_point, index) => index);
    const primaryYs = points.map((point) => asNumber(point.value));
    const compareYs = trend.comparison_shown ? points.map((point) => asNumber(point.comparison_value)) : [];
    const secondaryYs = secondary ? points.map((point) => asNumber(point.secondary_value)) : [];
    if (chart === "bar") {
      points.forEach((point, index) => {
        const value = asNumber(point.value);
        if (value == null) return;
        const x = xAt(index) - (series.length * barWidth) / 2 + seriesIndex * barWidth;
        const y = yAt(Math.max(value, yMin));
        const h = Math.max(1, yAt(0) - y);
        const title = `${escapeHtml(item.label)}: ${formatExact(primary.kind, point.value)} (${escapeHtml(point.bucket_label)})`;
        const drillable = interactive && item.key !== "__other__";
        plotted.push(
          `<rect class="trend-bar${drillable ? " trend-drill" : ""}" x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${barWidth.toFixed(1)}" height="${h.toFixed(1)}" fill="${color}"${drillable ? ` role="button" tabindex="0" data-trend-drill="true" data-bucket="${escapeHtml(point.bucket)}" data-series-key="${escapeHtml(item.key)}"` : ""}>${tooltip ? "" : `<title>${title}</title>`}</rect>`,
        );
      });
      return;
    }
    const line = pathFor(xs, primaryYs, xAt, yAt);
    if (line) plotted.push(`<path class="trend-line" d="${line}" stroke="${color}" fill="none" />`);
    if (trend.comparison_shown) {
      const compareLine = pathFor(xs, compareYs, xAt, yAt);
      if (compareLine) {
        plotted.push(`<path class="trend-line trend-compare" d="${compareLine}" stroke="${color}" fill="none" />`);
      }
    }
    if (dual && secondary) {
      const rightLine = pathFor(xs, secondaryYs, xAt, y2At);
      if (rightLine) plotted.push(`<path class="trend-line" d="${rightLine}" stroke="${SERIES_COLORS[(seriesIndex + 1) % SERIES_COLORS.length]}" fill="none" />`);
    } else if (secondary) {
      const sameLine = pathFor(xs, secondaryYs, xAt, yAt);
      if (sameLine) {
        plotted.push(
          `<path class="trend-line" d="${sameLine}" stroke="${SERIES_COLORS[(seriesIndex + 1) % SERIES_COLORS.length]}" fill="none" />`,
        );
      }
    }
    points.forEach((point, index) => {
      const value = asNumber(point.value);
      if (value == null) return;
      const titleParts = [
        escapeHtml(item.label),
        `${escapeHtml(primary.label || "Metric")}: ${formatExact(primary.kind, point.value)}`,
      ];
      if (secondary) titleParts.push(`${escapeHtml(secondary.label)}: ${formatExact(secondary.kind, point.secondary_value)}`);
      if (trend.comparison_shown) titleParts.push(`Comparison: ${formatExact(primary.kind, point.comparison_value)}`);
      titleParts.push(escapeHtml(point.bucket_label));
      const drillable = interactive && item.key !== "__other__";
      plotted.push(
        `<circle class="trend-point${drillable ? " trend-drill" : ""}" cx="${xAt(index).toFixed(1)}" cy="${yAt(value).toFixed(1)}" r="${compact ? "2.6" : tooltip ? "4" : "3.5"}" fill="${color}"${drillable ? ` role="button" tabindex="0" data-trend-drill="true" data-bucket="${escapeHtml(point.bucket)}" data-series-key="${escapeHtml(item.key)}"` : ""}>${tooltip ? "" : `<title>${titleParts.join(" · ")}</title>`}</circle>`,
      );
    });
  });

  const hits = [];
  const summaryBody = [];
  const summaryHead = ["Date", primary.label || "Metric"];
  if (trend.comparison_shown) summaryHead.push("Comparison");
  if (secondary) summaryHead.push(secondary.label || "Secondary");
  if (tooltip && first && Array.isArray(first.points)) {
    const half = n <= 1 ? innerW / 2 : innerW / Math.max(2 * (n - 1), 1);
    first.points.forEach((point, index) => {
      const model = trendTooltipModel(point, {
        primary,
        secondary,
        comparisonShown: Boolean(trend.comparison_shown),
        seriesLabel: first.label || "",
      });
      const cx = xAt(index);
      const value = asNumber(point.value);
      const cy = value == null ? top + innerH : yAt(value);
      const x0 = Math.max(left, cx - half);
      const x1 = Math.min(left + innerW, cx + half);
      hits.push(
        `<rect class="trend-hit" data-trend-hover="true" data-trend-point="${encodeURIComponent(JSON.stringify(model))}" data-trend-x="${cx.toFixed(1)}" data-trend-y="${cy.toFixed(1)}" x="${x0.toFixed(1)}" y="${top}" width="${Math.max(1, x1 - x0).toFixed(1)}" height="${innerH}" />`,
      );
      const values = [model.rows.find((row) => row.key === "metric")];
      if (trend.comparison_shown) values.push(model.rows.find((row) => row.key === "comparison"));
      if (secondary) values.push(model.rows.find((row) => row.key === "secondary"));
      summaryBody.push(
        `<tr><th scope="row">${escapeHtml(model.bucket_label)}</th>${values.map((row) => `<td>${escapeHtml((row && row.value) || "n/a")}</td>`).join("")}</tr>`,
      );
    });
  }

  const legend = [];
  series.forEach((item, index) => {
    legend.push(
      `<span class="trend-legend-item is-primary"><span class="trend-swatch" style="background:${SERIES_COLORS[index % SERIES_COLORS.length]}"></span>${escapeHtml(item.label)}${dual ? " (left axis)" : ""}</span>`,
    );
  });
  if (secondary) {
    legend.push(
      `<span class="trend-legend-item is-secondary"><span class="trend-swatch" style="background:${SERIES_COLORS[1]}"></span>${escapeHtml(secondary.label)}${dual ? " (right axis)" : ""}</span>`,
    );
  }
  if (trend.comparison_shown) {
    legend.push(`<span class="trend-legend-item"><span class="trend-swatch trend-swatch-dashed"></span>Comparison</span>`);
  }

  const svg = `<svg class="trend-svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHtml(primary.label || "Trend")} trend chart"${tooltip ? ' aria-describedby="drill-trend-point-summary"' : ""}>
      <text class="trend-axis-title" x="${left}" y="12">${escapeHtml(primary.label || "")}${dual ? " (left)" : ""}</text>
      ${dual ? `<text class="trend-axis-title" x="${width - right}" y="12" text-anchor="end">${escapeHtml(secondary.label)} (right)</text>` : ""}
      ${grid.join("")}
      ${yLabels.join("")}
      ${y2Labels.join("")}
      ${xLabels.join("")}
      ${plotted.join("")}
      ${hits.join("")}
      ${tooltip ? `<line class="trend-hover-line" data-trend-hover-line="true" x1="0" x2="0" y1="${top}" y2="${top + innerH}" visibility="hidden" />` : ""}
      ${tooltip ? '<circle class="trend-hover-mark" data-trend-hover-mark="true" cx="0" cy="0" r="5.5" visibility="hidden" />' : ""}
    </svg>`;

  const summary =
    tooltip && summaryBody.length
      ? `<table class="sr-only" id="drill-trend-point-summary" data-trend-point-summary="true">
      <caption>Exact ${escapeHtml(primary.label || "metric")} values for each ${escapeHtml((selection.grain || "day") === "day" ? "day" : selection.grain || "bucket")}</caption>
      <thead><tr>${summaryHead.map((label) => `<th>${escapeHtml(label)}</th>`).join("")}</tr></thead>
      <tbody>${summaryBody.join("")}</tbody>
    </table>`
      : "";

  return html`
    <div class="trend-chart${tooltip ? " is-inspectable" : ""}" data-trend-chart="true"${tooltip ? raw(' data-trend-tooltip-enabled="true"') : ""}>
      <div class="trend-legend">${legend.map((item) => raw(item))}</div>
      ${raw(svg)}
      ${tooltip ? raw('<div class="trend-tooltip" data-trend-tooltip="true" role="status" hidden></div>') : ""}
      ${summary ? raw(summary) : ""}
    </div>
  `;
}
