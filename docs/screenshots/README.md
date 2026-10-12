# Screenshots

**This directory is intentionally empty of images.** It is the documented home for
sanitized product captures, so that the repository has a conventional location for
them and reviewers can see what is still missing.

No placeholder or mockup images have been committed. A fabricated screenshot is
worse than no screenshot.

## What to capture

| Subject | Shows | Suggested path |
|---|---|---|
| Analytics Studio | The `/client/studio` canvas with the KPI tiles, trend and narrative zones populated | `analytics-studio.png` |
| Client overview | The analytics workspace with trends, explorer and comparison | `client-overview.png` |
| Downloads | The Downloads area showing all report artifacts, including "Download Consolidated Company Workbook" | `downloads.png` |
| Consolidated workbook | The workbook open in Excel, showing a populated Click-through or Overall conversion section | `consolidated-workbook.png` |
| Upload and publication | Upload centre, processing state, and the publish action | `upload-publish.png` |
| Architecture diagram | The data-flow diagram from [`ARCHITECTURE.md`](../../ARCHITECTURE.md) | `architecture.svg` |

## Rules

Any capture committed here must be sanitized. Specifically, do not include:

- client or company names, display names, codes, or UUIDs
- real campaign names, labels, or rate-card values
- any published or working-set data values
- credentials, tokens, `Authorization` headers, or API keys
- internal hostnames, IP addresses, private URLs, or filesystem paths
- production-only console output, logs, or database contents

Use a **local or disposable** environment with synthetic data. A local development
seed is preferable to production, because it cannot leak by accident.

Do not screenshot a production browser session. If a capture genuinely requires a
production-like state, reproduce that state locally and capture it there.

## Adding a capture

1. Sanitize the image before committing it.
2. Verify it contains no identifiers — check the rendered text, tab titles, browser
   chrome, and any bookmark or profile leakage.
3. Prefer PNG for screenshots and SVG for diagrams.
4. Commit the image here and reference it from the relevant document with a
   repository-relative path.