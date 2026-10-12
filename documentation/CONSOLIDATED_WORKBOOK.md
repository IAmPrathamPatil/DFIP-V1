# Consolidated Company Workbook

The consolidated company workbook is the cumulative-history sibling of the static
company workbook. It uses the same template, renderer, sheet names, formulas,
tenant scoping, and publication rules; the difference is the **row set**.

- Static company workbook — one publication snapshot.
- Consolidated company workbook — the company's entire published history.

Because the cumulative set is routinely far larger than a single publication,
this is the one download expensive enough to justify caching and background
preparation.

---

## 1. Purpose

A client who receives a monthly static workbook gets the current publication.
When they need to see several months together — for a quarter review, a
year-end reconciliation, or a trend they want to see in one file — they would
otherwise have to download several workbooks and combine them by hand.

The consolidated workbook removes that step. One download contains every
published month the company has, already combined, already in the same report
layout, and still a static file that opens without an Excel refresh.

## 2. API surface

```
GET /api/v1/publications/current/consolidated-client-report.xlsx
```

| Property | Value |
|---|---|
| Authentication | Bearer credential required; any authenticated company reader |
| Response type | `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` |
| Filename | `DFIP_<client_code>_<YYYY-MM-DD>_Client_Report_Consolidated.xlsx` |
| Optional query | `client_id` for a publisher selecting a company |
| Success | `200` with the workbook |
| Preparing | `202` with `Retry-After` |
| No published history | `404` |
| Over the row cap | `422` |
| Not authenticated | `401` |

The endpoint reads published data only. It does not process, publish, or move the
current publication pointer.

### `202` Preparing

```http
HTTP/1.1 202 Accepted
Retry-After: 5
Content-Type: application/json

{
  "status": "preparing",
  "message": "The consolidated company workbook is being prepared for the latest published months. It will be ready shortly; retry this download in a moment.",
  "retry_after_seconds": 5
}
```

`202` means the artifact was not ready and a build has been queued. The caller
retries after the interval in `Retry-After`.

The endpoint deliberately does **not** fall back to a synchronous render when the
artifact is missing. A cold build renders the full cumulative history and the
whole nine-sheet workbook, which is far too long to hold a client connection
open. Returning `202` keeps the download responsive and makes the wait explicit.

### Refusals

Two conditions are refused rather than worked around, and neither produces a
partial or truncated workbook:

- **No published history → `404`.** A company with nothing published gets an
  error rather than an empty workbook that looks like a successful report.
- **Cumulative rows over the history cap → `422`.** The cap is
  `HISTORY_FACTS_MAX_PAGE_LIMIT`, the same limit the codebase already applies to
  cumulative published history. The download refuses instead of truncating,
  because a silently truncated report is worse than an explicit refusal.

The over-cap refusal is raised during state classification, before any warm is
queued. Without that, a company over the cap could never produce an artifact and
a caller would retry `preparing` forever instead of being told the real reason.

## 3. Row set

Rows come from the same store method the cumulative history endpoint uses, which
gives the correct semantics for free:

- **Complete snapshots only.** Publications whose snapshot did not complete are
  excluded.
- **Newest wins per grain.** Where the same campaign / variation / day appears in
  more than one publication, the newest publication's row is used. Republishing a
  month therefore *replaces* it instead of duplicating the period.
- **Published only.** Working-set, staged, and failed runs are excluded.
- **Oldest day first.** Row order is chronological.

Deduplication, aggregation, and derived metrics are not reimplemented here. They
remain in the existing renderer and PivotCaches, which is why this artifact
produces the same numbers as the static workbook.

## 4. Conversion sections

The consolidated workbook carries the same two derived conversion sections as the
other artifacts, beneath the row group banners on each report sheet:

| Section | Columns | Measures |
|---|---|---|
| Click-through conversions | O:S | Unique Click-Through Conversions, Click-Through Revenue, Cost per UCT conversion, UCT conversion rate, Click-Through Conv. ROAS |
| Overall conversions | T:X | Unique Conversions, Revenue, Cost per Unique Conversion, Delivered-through conversion rate, Overall ROAS |

These are worksheet cells with fill-down formulas and their own number formats —
not chart objects.

**Why this needed specific handling.** The conversion layout was originally applied
only to the refreshable artifact, because a Power Query refresh rebuilds the shared
pivot cache and drops calculated fields, so the conversion formulas had to be
re-injected afterwards. The static and consolidated artifacts have no Power Query
and no cache rebuild, so they were never given the layout — their templates shipped
the group banners with nothing beneath them, leaving both conversion sections
blank.

The fix was a snapshot variant of the same layout function. It writes the identical
columns, styles, and formulas, and deliberately leaves each PivotTable's data
fields untouched: with no cache rebuild there is no calculated field to drop and
nothing to re-add. Refreshable behavior is unchanged.

## 5. Cache

### Key

The cache key is a **fingerprint of the company's eligible published history**,
not a timestamp and not a publication id. It is a tuple of aggregates computed over
company-scoped tables:

```
(publication_count, max_publication_id, max_published_at,
 grain_row_count, max_day, month_count)
```

Publication identity leads deliberately: rows only change because a publication
was written, so any publish, republish, or snapshot-status change moves the token.
The grain aggregates then catch anything the publication view cannot see.

Counts alone are not sufficient. Republishing the same months with *different
rows* leaves row count, latest day, and month count identical, so a count-only key
would keep serving a stale artifact.

All parts are aggregates, so the fingerprint stays cheap and never materializes the
rows the download actually fetches. It reads two small aggregate queries against
indexed, client-scoped tables rather than the full union the download pages
through — which is what keeps the cache check affordable on every request.

If the fingerprint cannot be computed cheaply, it is treated as absent and any
cached artifact is treated as invalid. Correctness never depends on the cache
being present.

### Storage

One artifact per company. Writing a new fingerprint replaces that company's
previous file, so the cache cannot grow without bound. Files are written
owner-only because the workbook contains one company's published rows, and writes
go through a temporary file plus an atomic replace so a reader never observes a
partial file.

This is not access control. Authorization is enforced by the endpoint before any
cache lookup, so a cache hit never bypasses an authorization decision.

### Invalidation

Invalidation is implicit: a new fingerprint simply no longer matches, so the next
request rebuilds. There is no explicit delete step, and no chance of serving an
artifact whose history has moved.

### Cache location

`DFIP_CONSOLIDATED_CACHE_DIR`, defaulting to a subdirectory of the system
temporary directory. Setting it to persistent storage is recommended for
production, because temporary directories are subject to periodic cleanup — which
is self-healing rather than incorrect, but costs a rebuild when it happens.

`DFIP_CONSOLIDATED_CACHE_ENABLED` defaults to `True`. Setting it to `False`
disables caching and returns to synchronous rendering for every request.

## 6. Warming

A build is triggered by either:

1. a publication completing, or
2. a download that finds no usable artifact.

Warming is **idempotent per company**: if a build is already in flight for that
company it is left alone rather than duplicated. The in-flight set is process-local
and guarded by a lock.

**The warm pool is separate from the upload pool.** The upload pool is one
serialized thread behind an unbounded FIFO queue, because transform work must not
run concurrently with itself. Sharing that pool with workbook warming would park
every later upload behind a build that no user is waiting on, and a burst of
publications would stack large builds. Warming therefore gets its own
single-worker pool. That also caps peak memory at one render, and upload
concurrency is unchanged.

**Detached threads bind their own tenant context.** A pool thread inherits no
request context, so the warm path binds tenant RLS itself before reading. This is
the same contract the upload worker uses.

**Warming fails open.** Every failure is caught and logged. A failed warm must
never surface as a failed publish, and the download path still produces a correct
workbook on its own if warming did not run.

## 7. Frontend behavior

The Downloads view exposes **"Download Consolidated Company Workbook"** alongside
the other report downloads, using the existing API client and download handler.

On click, the client:

1. Requests the artifact.
2. On `200`, receives the file.
3. On `202`, reads `Retry-After` (or the `retry_after_seconds` body field) and
   retries after that interval, up to a bounded attempt budget.
4. On eventual success, receives the file.
5. If the budget is exhausted, shows a clear message that the workbook is still
   being prepared.

The client does **not** fall back to a synchronous render while a build is in
progress. The bounded retry budget means a genuinely long queue surfaces as an
honest message rather than a hung download, and because warming continues in the
background, a later attempt usually hits a warm cache.

## 8. Operational notes

- **Where the pool threads run.** Both pools are in-process and single-instance.
  Restarting the API clears in-flight warming; the next download re-queues it.
- **What a `202` costs.** Effectively nothing. It is a fingerprint check plus, when
  needed, a queued build.
- **Diagnosing a persistent `202`.** It means no artifact has been stored for the
  current fingerprint. Check that the cache directory is writable by the service
  account, and check the API log for `consolidated cache warm failed`.
- **Over-cap companies.** Refused with `422` by design. The cap is
  `HISTORY_FACTS_MAX_PAGE_LIMIT`.
- **Companies with no history.** Refused with `404` by design.

## 9. Verification

Behaviour is covered by the test suite:

| File | Coverage |
|---|---|
| `tests/test_run013_consolidated_charts_cache_downloads.py` | Cumulative row set, newest-wins union, conversion section population, cache hit/miss, fingerprint invalidation, row-cap and no-history refusals, auth and company scoping |
| `tests/test_run014_consolidated_cache_warming.py` | Warm triggering, per-company deduplication, executor isolation from the upload pool, RLS binding on the detached thread, `202` response, cache invalidation on republish |
| `tests/test_run012_consolidated_workbook.py` | Workbook generation for the consolidated artifact |

Run them with:

```bash
python -m pytest tests/test_run012_consolidated_workbook.py \
                 tests/test_run013_consolidated_charts_cache_downloads.py \
                 tests/test_run014_consolidated_cache_warming.py
```

## 10. Related

- [`V1_EXCEL_REPORTING.md`](V1_EXCEL_REPORTING.md) — workbook generation and OOXML handling
- [`V1_API_REFERENCE.md`](V1_API_REFERENCE.md) — HTTP contract
- [`PUBLICATION.md`](PUBLICATION.md) — publication and current-pointer semantics
- [`V1_RLS.md`](V1_RLS.md) — tenant context and RLS
- [`V1_OPERATIONS_RUNBOOK.md`](V1_OPERATIONS_RUNBOOK.md) — operator commands