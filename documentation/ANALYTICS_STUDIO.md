# Analytics Studio

Analytics Studio is the analytical canvas of the DFIP client web application, at
`/client/studio`. It is a full-width workspace over a company's **published**
data.

This page describes what exists in the code today. Where a capability is not
implemented, that is stated rather than implied.

> Historical and agent-oriented material is preserved in
> [`ANALYTICS_STUDIO_CURRENT_HANDOFF.md`](ANALYTICS_STUDIO_CURRENT_HANDOFF.md).
> That file is a working snapshot, not a specification. Where the two disagree,
> this page and the source code are authoritative.

---

## 1. What Studio is

Studio is a **display surface over server-computed analytical results**. It is not
a general-purpose query surface, and the browser does not compute metrics.

- Every value rendered in Studio is calculated server-side by the analytics service
  and returned as JSON.
- The browser chooses which allowlisted operation to request and renders the
  result.
- All reads are scoped to the authenticated company and to published data. Studio
  never reads the working set.

Studio shares its filters and its API contracts with the rest of the client
workspace, so a filter set behaves consistently across surfaces.

## 2. Data sources

The Studio view issues these requests in parallel:

| Request | Endpoint | Feeds |
|---|---|---|
| KPI tiles | `GET /api/v1/analytics/overview` | Headline metrics, period, comparison |
| Trend | `GET /api/v1/analytics/trends` | Trend zone, cost-and-revenue zone |
| Distribution / detail | `GET /api/v1/analytics/explorer` | Distribution zone, detail zone |
| Mix | `GET /api/v1/analytics/trends` (mix parameters) | Stacked-mix and share-mix zones |
| Insights | `GET /api/v1/analytics/insights` | Narrative zone |
| Anomalies | `GET /api/v1/analytics/anomalies` | Narrative zone |

Each request settles independently, so one failing zone degrades that zone only
rather than blanking the workspace. An authentication failure is re-thrown and
handled at the view level instead.

## 3. Zones

### KPI tiles

Headline metrics for the selected period, with a period-over-period comparison
and a stated reason when a comparison is unavailable or not applicable. The period
label reflects the selected grain — a month, an explicit range, or the whole
published history.

### Trend

A time series for one metric at one grain, with an optional breakdown.

- **Metric** — an allowlisted option: Total Cost, Revenue, Overall ROAS, Delivered,
  Unique Clicks, Unique Conversions.
- **Grain** — Day, Week, or Month.
- **Breakdown** — an allowlisted dimension option.

The picker is a fixed option list, not free text. An unsupported combination is
reported to the caller rather than silently substituted.

### Cost and revenue

A combined view of cost and revenue over the same series used by the trend zone.

### Stacked mix and share mix

Composition over time from the mix parameters: an absolute stacked view and a
share-of-total view of the same breakdown.

### Distribution

The distribution of the selected measure across the selected breakdown dimension.

### Detail

The row-level breakdown behind the distribution view.

### Narrative

Deterministic insights and anomalies for the current filter selection.

This is **server-computed copy**. The server derives the findings and the wording
from the published data. There is no model-generated prose in this zone.

## 4. Not implemented in Studio

Stated explicitly so the boundary is not misread:

- **No free-form metric or grain picker.** The trend zone offers fixed allowlisted
  options only.
- **No matrix or pivot-style builder.** Composition is limited to the stacked,
  share-stacked, distribution, and detail zones described above.
- **No drill-down inside Studio.** The drilldown endpoint exists and is used
  elsewhere in the client workspace, but Studio does not drill.
- **No contextual Ask inside Studio.** The Ask endpoint exists; it is not surfaced
  on this surface.
- **No CSV export from Studio.** The explorer and export CSV endpoints exist; they
  are not wired into this view.
- **No saved-analysis management inside Studio.** Saved analyses are managed
  elsewhere in the client workspace.

`studioReservedZone` exists in the view module as an available placeholder
component for a future zone. It is defined but not currently rendered, so no zone
is presently shown as reserved.

## 5. Wider analytics API

Studio is one consumer of the analytics API. The full registered set:

| Method | Path | Purpose |
|---|---|---|
| GET | `/analytics/overview` | KPI tiles, period, comparison |
| GET | `/analytics/trends` | Trends and mix series |
| GET | `/analytics/drilldown` | Drilldown from a selected point |
| GET | `/analytics/explorer` | Ranked / distribution exploration |
| GET | `/analytics/explorer.csv` | Explorer results as CSV |
| GET | `/analytics/insights` | Deterministic insights |
| GET | `/analytics/anomalies` | Deterministic anomalies |
| POST | `/analytics/ask` | Contextual Ask |
| GET/POST | `/analytics/saved` | List and create saved analyses |
| GET/POST/DELETE | `/analytics/saved/{analysis_id}` | Read, update, delete a saved analysis |
| GET | `/analytics/export.csv` | Export current analytical state as CSV |

### Contextual Ask

Ask maps a natural-language question onto an **allowlisted** analytical operation
and then explains the structured result. The language model does not execute SQL,
does not define or invent KPIs, and cannot widen company scope. Company scoping
and authorization are enforced before the question is ever interpreted.

## 6. Calculation rules

These apply to every analytical surface, Studio included.

- **Aggregate first, then apply the operation.** A ratio is computed from summed
  numerators and denominators, never averaged across rows or days.
- **Division by zero yields null**, never zero and never an invented rate.
- **Rate KPIs** quantize to six decimal places; money ratios to four, matching the
  numeric precision of the underlying columns.
- **Native source-platform rate columns are evidence only** and are rejected as
  calculation inputs.
- Metrics marked non-additive are not safe to sum in an external tool, and the
  Power BI package documents that constraint.

Authoritative formulas live in the analytics package and are mirrored by rows in
the `kpi_definition` table. [`ANALYTICS.md`](ANALYTICS.md) holds the full KPI
dictionary.

## 7. Authorization

Every analytics endpoint requires a bearer credential and resolves a company scope
from the principal before any data is read. Client and reader principals can
reach published analytics for their own company and nothing else. Publishers and
admins can additionally inspect the working set.

Analytics reads never bypass company scoping, and the RLS context for the
transaction is set from the same resolved company. See
[`V1_RLS.md`](V1_RLS.md) for the precise RLS position and its limits.

## 8. Related

- [`ANALYTICS.md`](ANALYTICS.md) — KPI dictionary and calculation contract
- [`V1_API_REFERENCE.md`](V1_API_REFERENCE.md) — HTTP routes and envelopes
- [`PUBLICATION.md`](PUBLICATION.md) — what "published" means for these reads
- [`D9_ADVANCED_ANALYTICS_WORKSPACE.md`](D9_ADVANCED_ANALYTICS_WORKSPACE.md) — the wider client analytics workspace
- [`D9_DASHBOARD_COMPLETE.md`](D9_DASHBOARD_COMPLETE.md) — dashboard capability summary
- [`../ARCHITECTURE.md`](../ARCHITECTURE.md) — analytics flow in system context