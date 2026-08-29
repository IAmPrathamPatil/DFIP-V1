import {
  dataTable,
  definitionList,
  lineageTrail,
  metricCard,
  pageHeader,
  workflowSteps,
} from "./components.js";
import {
  badge,
  displayCell,
  emptyState,
  errorBanner,
  html,
  loadingState,
  paginationControls,
  raw,
} from "./format.js";

function qs(params) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null) continue;
    search.set(key, String(value));
  }
  const encoded = search.toString();
  return encoded ? `?${encoded}` : "";
}

function moneyCell(value) {
  if (value === null || value === "") return displayCell(value);
  return html`<span class="mono">${value}</span>`;
}

function pageParams(query) {
  const limit = Number(query.get("limit") || 50);
  const offset = Number(query.get("offset") || 0);
  return { limit, offset };
}

function pager(path, query, pagination) {
  const nextQuery = new URLSearchParams(query);
  return paginationControls(pagination, (offset) => {
    nextQuery.set("offset", String(offset));
    nextQuery.set("limit", String(pagination.limit));
    return `${path}?${nextQuery.toString()}`;
  });
}

function filterForm(path, query, fields) {
  const inputs = fields.map(
    (field) => html`
      <label>
        ${field.label}
        ${field.help ? html`<span class="help">${field.help}</span>` : ""}
        <input name="${field.name}" value="${query.get(field.name) || ""}" />
      </label>
    `,
  );
  return html`
    <form class="toolbar filter-bar" method="get" action="${path}" data-filter-form="true">
      <label>
        Page size
        <select name="limit">
          ${["25", "50", "100", "200"].map(
            (value) =>
              html`<option value="${value}"${query.get("limit") === value || (!query.get("limit") && value === "50") ? raw(" selected") : ""}>${value}</option>`,
          )}
        </select>
      </label>
      ${inputs}
      <input type="hidden" name="offset" value="0" />
      <button type="submit">Apply filters</button>
    </form>
  `;
}

function clientScopeBar(query, path) {
  return html`
    <form class="toolbar" method="get" action="${path}" data-filter-form="true">
      <label>
        Client identifier
        <span class="help">Required for this credential to load versions for one client.</span>
        <input name="client_id" value="${query.get("client_id") || ""}" required />
      </label>
      <button type="submit">Load client</button>
    </form>
  `;
}

function clientField(session, { required = true, query } = {}) {
  const bound = session && session.client_id;
  if (bound) {
    return html`<input type="hidden" name="client_id" value="${bound}" />`;
  }
  const fromQuery = query && query.get ? query.get("client_id") || "" : "";
  return html`
    <label>
      Client identifier
      <span class="help">Required for this credential. The API remains authoritative.</span>
      <input name="client_id" value="${fromQuery}" autocomplete="off" ${required ? raw(" required") : ""} />
    </label>
  `;
}

function shortId(value) {
  if (value == null || value === "") return displayCell(value);
  const text = String(value);
  const shown = text.length > 13 ? `${text.slice(0, 8)}…` : text;
  return html`<span class="mono" title="${text}">${shown}</span>`;
}

export function rawUploadLifecycle(uploadResult) {
  if (!uploadResult) return "idle";
  if (Array.isArray(uploadResult.items)) {
    const lives = uploadResult.items.map((item) => rawUploadLifecycle(item));
    if (!lives.length) return "queued";
    if (lives.some((life) => life === "failed")) return "failed";
    if (lives.every((life) => life === "succeeded")) return "succeeded";
    if (lives.some((life) => life === "processing")) return "processing";
    return "queued";
  }
  const batch = uploadResult.batch;
  const run = uploadResult.processing_run;
  const batchStatus = batch && batch.status;
  const runStatus = run && run.status;
  if (runStatus === "succeeded" || batchStatus === "processed") return "succeeded";
  if (runStatus === "failed" || batchStatus === "failed") return "failed";
  if (
    runStatus === "pending" ||
    runStatus === "running" ||
    batchStatus === "staged" ||
    batchStatus === "validated"
  ) {
    return "processing";
  }
  if (batchStatus === "received" || uploadResult.accepted) return "queued";
  return "queued";
}

export function rawUploadBanner(uploadResult) {
  const life = rawUploadLifecycle(uploadResult);
  if (life === "idle") return "";
  const uploadRun =
    uploadResult && uploadResult.processing_run
      ? uploadResult.processing_run.processing_run_id
      : "";
  const transformed =
    uploadResult && uploadResult.transform ? uploadResult.transform.transformed : "";
  const uploadRunRecord = uploadResult && uploadResult.processing_run;
  const batch = uploadResult && uploadResult.batch;
  const batchId = batch && batch.batch_id;
  const published = uploadResult && uploadResult.published === false;
  const fileCount =
    uploadResult && Array.isArray(uploadResult.items) ? uploadResult.items.length : 0;
  if (life === "queued" || life === "processing") {
    const label = life === "queued" ? "Queued" : "Processing";
    const fileLabel =
      fileCount > 1
        ? `The website stays usable while these ${fileCount} files are processed.`
        : "The website stays usable while this file is processed.";
    return html`
      <div class="banner" data-upload-status="true" role="status">
        <strong>${label}. ${fileLabel}</strong>
        <div>
          ${
            batchId
              ? html`Batch ${shortId(batchId)} is ${badge(batch.status)}.`
              : "The API accepted the workbook."
          }
          This does not publish. You can leave this page and return to Processing.
        </div>
        <p>
          ${
            batchId
              ? html`<a href="/admin/batches/${batchId}">Open batch</a> · `
              : ""
          }
          <a href="/admin/processing-runs">Open processing</a>
        </p>
      </div>
    `;
  }
  if (life === "failed") {
    const summary =
      (batch && batch.error_summary) ||
      (uploadRunRecord && uploadRunRecord.status === "failed"
        ? "Processing failed."
        : "The workbook could not be processed.");
    return html`
      <div class="banner error" data-upload-status="true" role="status">
        <strong>Processing failed.</strong>
        <div>${summary} Publication remains false.</div>
        <p>
          ${
            batchId
              ? html`<a href="/admin/batches/${batchId}">Open batch</a> · `
              : ""
          }
          <a href="/admin/processing-runs">Open processing</a>
        </p>
      </div>
    `;
  }
  return html`
    <div class="banner success" data-upload-status="true" role="status">
      <strong>Upload completed successfully.</strong>
      <div>
        ${
          uploadRun
            ? html`Processing completed. Run ${shortId(uploadRun)}.`
            : "No succeeded processing run (header or file rejection)."
        }
        ${transformed === "" ? "" : html` Transformed ${transformed} row(s).`}
        ${
          uploadRunRecord && uploadRunRecord.campaign_label_version_id
            ? html` Logic ${shortId(uploadRunRecord.campaign_label_version_id)} · Labels
              ${shortId(uploadRunRecord.label_group_version_id)}.`
            : ""
        }
        ${published ? html` Publication required. This does not publish.` : ""}
      </div>
      ${
        uploadRun
          ? html`<p>
              <a href="/admin/processing-runs/${uploadRun}">Open processing run</a>
              ·
              <a href="/admin/review?processing_run_id=${uploadRun}">Review / QA</a>
              ·
              <a href="/admin/publications">Publications</a>
            </p>`
          : ""
      }
    </div>
  `;
}

function when(value) {
  if (value == null || value === "") return displayCell(value);
  return html`<time datetime="${value}">${String(value).replace("T", " ")}</time>`;
}

function filePicker({ name = "file", label = "Workbook (.xlsx)", required = true, multiple = false } = {}) {
  return html`
    <label>
      ${label}
      <span class="help">Accepted type: .xlsx${multiple ? ". You may select several Raw workbooks." : ""}</span>
      <input
        name="${name}"
        type="file"
        accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        data-file-input="true"
        ${multiple ? raw(" multiple") : ""}
        ${required ? raw(" required") : ""}
      />
    </label>
    <p class="file-meta" data-file-chosen>No file selected.</p>
  `;
}

function itemsOf(page) {
  return page && page.items ? page.items : [];
}

function totalOf(page) {
  return page && page.pagination && typeof page.pagination.total === "number"
    ? page.pagination.total
    : null;
}

function sliceBanner(kind) {
  if (kind === "published") {
    return html`<div class="slice-banner published"><strong>Published data</strong> — current publication slice only. This is not the working set.</div>`;
  }
  return html`<div class="slice-banner working"><strong>Working set</strong> — processing results for review. This is not published reporting data.</div>`;
}

function catalogErrors(error, uploadResult) {
  const errors =
    error && Array.isArray(error.details)
      ? error.details
      : uploadResult && Array.isArray(uploadResult.errors)
        ? uploadResult.errors
        : [];
  if (!errors.length) return "";
  return html`
    <div class="banner error" role="alert">
      <strong>Validation errors</strong>
      <ul>
        ${errors.map(
          (item) =>
            html`<li>
              ${item.row ? html`Row ${item.row}: ` : ""}
              <span class="mono">${item.code}</span>
              ${item.detail || ""}
            </li>`,
        )}
      </ul>
    </div>
  `;
}

export function credentialView({ error, nextPath }) {
  return html`
    <div class="login-shell" id="main">
      <section class="login-card">
        <p class="brand">DFIP</p>
        <p class="eyebrow">Publisher control center</p>
        <h1>Sign in</h1>
        <p class="lede">
          Sign in with your username and password. Access is limited to your
          organization. Credentials stay in this browser tab only.
        </p>
        ${errorBanner(error)}
        <form data-credential-form="true" class="stack">
          <input type="hidden" name="next" value="${nextPath || "/client"}" />
          <label>
            Username
            <input name="username" type="text" autocomplete="username" required />
          </label>
          <label>
            Password
            <input name="password" type="password" autocomplete="current-password" required />
          </label>
          <button type="submit">Sign in</button>
        </form>
      </section>
    </div>
  `;
}

export function unauthorizedView() {
  return html`
    <section class="panel">
      ${pageHeader({
        title: "Not authorized",
        description: "The current principal cannot open this UI surface.",
      })}
      <p class="muted">
        Admin routes require role <span class="mono">admin</span> or
        <span class="mono">publisher</span>. The API also enforces those roles on
        working-set inspection and publication writes. That is application-level
        authorization, not PostgreSQL RLS.
      </p>
      <p><a class="btn-secondary" href="/client">Open reporting</a></p>
    </section>
  `;
}

export function notFoundView() {
  return html`
    <section class="panel">
      ${pageHeader({
        title: "Not found",
        description: "That application route does not exist.",
      })}
    </section>
  `;
}

function recentActivity({ files, runs, publications }) {
  const events = [];
  for (const item of itemsOf(files)) {
    events.push({
      at: item.uploaded_at,
      label: "Upload completed",
      detail: item.original_filename || "Workbook",
      href: `/admin/source-files/${item.source_file_id}`,
    });
  }
  for (const item of itemsOf(runs)) {
    events.push({
      at: item.finished_at || item.started_at,
      label: item.status === "failed" ? "Processing failed" : "Processing completed",
      detail: item.status,
      href: `/admin/processing-runs/${item.processing_run_id}`,
    });
  }
  for (const item of itemsOf(publications)) {
    events.push({
      at: item.published_at,
      label: "Publication created",
      detail: "Current pointer updated",
      href: "/admin/publications",
    });
  }
  events.sort((left, right) => String(right.at || "").localeCompare(String(left.at || "")));
  const shown = events.slice(0, 8);
  if (!shown.length) {
    return emptyState("No recent activity is available yet.");
  }
  return html`
    <ul class="activity-list">
      ${shown.map(
        (item) => html`
          <li>
            <span class="muted">${when(item.at)}</span>
            <div>
              <a href="${item.href}">${item.label}</a>
              <div class="muted">${item.detail}</div>
            </div>
          </li>
        `,
      )}
    </ul>
  `;
}

export function adminHomeView({
  health,
  session,
  runs,
  latestRun,
  currentPublication,
  publications,
  files,
  facts,
  findings,
  error,
  loading,
}) {
  if (loading) return loadingState();
  const runCount = totalOf(runs);
  const factCount = totalOf(facts);
  const findingCount = totalOf(findings);
  const fileCount = totalOf(files);
  const latestFile = itemsOf(files).length ? itemsOf(files)[itemsOf(files).length - 1] : null;
  const publication = currentPublication && currentPublication.publication;
  const latestStatus = latestRun ? latestRun.status : "Unknown";
  return html`
    ${errorBanner(error)}
    ${pageHeader({
      eyebrow: "Publisher",
      title: "Dashboard",
      description: "Operational state from the live API. Values are not estimated.",
      actions: html`
        <a class="btn" href="/admin/upload" style="display:inline-flex;align-items:center;">Upload New Dataset</a>
        <a class="btn-secondary" href="/admin/processing-runs" style="display:inline-flex;align-items:center;padding:0.5rem 0.85rem;border-radius:6px;">View Processing Runs</a>
        <a class="btn-secondary" href="/admin/facts" style="display:inline-flex;align-items:center;padding:0.5rem 0.85rem;border-radius:6px;">Review Current Facts</a>
        <a class="btn-secondary" href="/admin/publications" style="display:inline-flex;align-items:center;padding:0.5rem 0.85rem;border-radius:6px;">View Publications</a>
      `,
    })}
    <div class="metrics">
      ${metricCard({
        label: "Latest processing",
        value: latestRun ? latestStatus : "None",
        hint: latestRun ? when(latestRun.started_at) : "No processing runs yet.",
        href: latestRun ? `/admin/processing-runs/${latestRun.processing_run_id}` : "/admin/processing-runs",
        tone: latestStatus === "succeeded" ? "ok" : latestStatus === "failed" ? "danger" : "",
      })}
      ${metricCard({
        label: "Current publication",
        value: publication ? "Published" : "None",
        hint: publication ? shortId(publication.publication_id) : "No current publication.",
        href: "/admin/publications",
        tone: publication ? "ok" : "warn",
      })}
      ${metricCard({
        label: "Working-set facts",
        value: factCount == null ? "Unknown" : String(factCount),
        hint: "Review slice, not published data.",
        href: "/admin/facts",
      })}
      ${metricCard({
        label: "QA findings",
        value: latestRun ? (findingCount == null ? "Unknown" : String(findingCount)) : "—",
        hint: latestRun ? "Findings for the latest run. Not a QA verdict." : "No run to inspect.",
        href: latestRun ? `/admin/review?processing_run_id=${latestRun.processing_run_id}` : "/admin/review",
        tone: findingCount > 0 ? "warn" : "",
      })}
    </div>
    <div class="split">
      <section class="panel">
        <div class="panel-head">
          <h2>Recent activity</h2>
        </div>
        ${recentActivity({ files, runs, publications })}
      </section>
      <section class="panel">
        <div class="panel-head">
          <h2>Environment</h2>
        </div>
        ${
          health
            ? definitionList([
                ["API", displayCell(health.status)],
                ["Application", displayCell(health.application)],
                ["Environment", displayCell(health.environment)],
              ])
            : emptyState("Health is unavailable.")
        }
        ${
          session
            ? html`<p class="muted" style="margin-top:0.9rem;">Signed in as ${session.subject} (${session.role}).</p>`
            : ""
        }
        <p class="muted">
          Latest upload:
          ${
            latestFile
              ? html`<a href="/admin/source-files/${latestFile.source_file_id}">${latestFile.original_filename}</a>`
              : fileCount === 0
                ? "none yet"
                : "unknown"
          }.
        </p>
        ${runCount != null ? html`<p class="muted">${runCount} processing run(s) recorded.</p>` : ""}
      </section>
    </div>
  `;
}

export function uploadCenterView({
  session,
  query,
  logicPage,
  labelsPage,
  uploadResult,
  catalogResult,
  catalogKind,
  error,
  loading,
}) {
  if (loading) return loadingState();
  const life = rawUploadLifecycle(uploadResult);
  const lastItem =
    uploadResult && Array.isArray(uploadResult.items) && uploadResult.items.length
      ? uploadResult.items[uploadResult.items.length - 1]
      : uploadResult;
  const batch = lastItem && lastItem.batch;
  const logicActive = logicPage && logicPage.processing_active;
  const labelsActive = labelsPage && labelsPage.processing_active;
  const scopeQuery = query || new URLSearchParams();
  return html`
    ${errorBanner(error)}
    <div data-upload-status-host="true">${rawUploadBanner(uploadResult)}</div>
    ${
      catalogResult && catalogResult.version
        ? html`
            <div class="banner success" role="status">
              <strong>${catalogKind === "labels" ? "Labels" : "Logic"} upload completed successfully.</strong>
              <div>
                Stored draft ${catalogResult.version.version_label}
                (${catalogResult.version.row_count} row(s)). Not active until you activate it.
                This does not publish.
              </div>
            </div>
          `
        : ""
    }
    ${pageHeader({
      eyebrow: "Ingest",
      title: "Upload Center",
      description: "Raw data feeds processing. Logic and Labels configure processing. None of these uploads publish a report.",
    })}
    ${!(session && session.client_id) ? clientScopeBar(scopeQuery, "/admin/upload") : ""}
    ${workflowSteps([
      { label: "Select file", state: "done" },
      { label: "Upload", state: life === "idle" ? "current" : "done" },
      {
        label: "Validate",
        state:
          life === "queued" || life === "processing"
            ? "current"
            : life === "succeeded" || life === "failed"
              ? "done"
              : "",
      },
      {
        label: "Process",
        state: life === "processing" ? "current" : life === "succeeded" ? "done" : "",
      },
      { label: "Review", state: "" },
      { label: "Publish", state: "" },
    ])}
    <div class="upload-grid">
      <section class="upload-card">
        <p class="eyebrow">Raw data</p>
        <h2>Source dataset</h2>
        <p class="purpose">
          Web-Engage workbook used for staging and processing. Expected sheet:
          Web-Engage Raw. You may upload one file or several files for the same
          period. Publication remains false until you publish a run.
        </p>
        <form class="stack" data-upload-form="true">
          ${filePicker({ name: "files", multiple: true, label: "Workbook(s) (.xlsx)" })}
          ${clientField(session, { query: scopeQuery })}
          <label>
            <span><input name="force" type="checkbox" /> Re-ingest a known file</span>
          </label>
          <button type="submit" ${life === "queued" || life === "processing" ? raw(" disabled") : ""}>
            ${life === "queued" || life === "processing" ? "Processing…" : "Upload Raw Data"}
          </button>
        </form>
        ${
          batch
            ? html`<p class="muted">Staging status: ${badge(batch.status)}. Staged ${displayCell(batch.row_count_staged)}, rejected ${displayCell(batch.row_count_rejected)}${uploadResult && uploadResult.file_count > 1 ? html` · ${uploadResult.file_count} files` : ""}.</p>`
            : ""
        }
      </section>
      <section class="upload-card">
        <p class="eyebrow">Logic</p>
        <h2>Business processing</h2>
        <p class="purpose">
          Campaign mapping workbook. Valid files become a draft version. Activation is explicit.
        </p>
        <p class="muted">
          Current version:
          ${logicActive ? html`${logicActive.version_label} (${logicActive.status})` : "Packaged fallback"}
        </p>
        <form class="stack" data-catalog-upload-form="true">
          <input type="hidden" name="kind" value="logic" />
          ${filePicker({})}
          ${clientField(session, { query: scopeQuery })}
          <button type="submit">Upload Logic</button>
        </form>
        <p><a href="/admin/logic">Open Logic management</a></p>
      </section>
      <section class="upload-card">
        <p class="eyebrow">Labels</p>
        <h2>Business mapping</h2>
        <p class="purpose">
          Filter Logic 1_2 workbook. Labels affect processing after activation.
        </p>
        <p class="muted">
          Current version:
          ${labelsActive ? html`${labelsActive.version_label} (${labelsActive.status})` : "Packaged fallback"}
        </p>
        <form class="stack" data-catalog-upload-form="true">
          <input type="hidden" name="kind" value="labels" />
          ${filePicker({})}
          ${clientField(session, { query: scopeQuery })}
          <button type="submit">Upload Labels</button>
        </form>
        <p><a href="/admin/labels">Open Labels management</a></p>
      </section>
    </div>
  `;
}

export function sourceFileListView({ page, query, error, loading }) {
  if (loading) return loadingState();
  const path = "/admin/source-files";
  return html`
    <section class="panel">
      ${pageHeader({
        title: "Source files",
        description: "Uploaded workbooks that entered staging.",
      })}
      ${errorBanner(error)}
      ${filterForm(path, query, [])}
      ${
        page && page.items && page.items.length
          ? dataTable(
              "source_file",
              ["File", "Filename", "Kind", "Size", "Uploaded"],
              page.items.map((item) => [
                html`<a href="/admin/source-files/${item.source_file_id}">${shortId(item.source_file_id)}</a>`,
                displayCell(item.original_filename),
                displayCell(item.source_kind),
                displayCell(item.byte_size),
                when(item.uploaded_at),
              ]),
            )
          : emptyState("No source files in this page.")
      }
      ${page ? pager(path, query, page.pagination) : ""}
    </section>
  `;
}

export function sourceFileDetailView({ item, error, loading }) {
  if (loading) return loadingState();
  if (error) return html`${errorBanner(error)}`;
  return html`
    <section class="panel">
      ${pageHeader({
        title: item.original_filename || "Source file",
        crumbs: [
          { label: "Source files", href: "/admin/source-files" },
          { label: "File" },
        ],
      })}
      ${lineageTrail([{ label: "Source file", href: `/admin/source-files/${item.source_file_id}` }])}
      ${definitionList([
        ["Filename", displayCell(item.original_filename)],
        ["Kind", displayCell(item.source_kind)],
        ["SHA-256", html`<span class="mono">${item.sha256}</span>`],
        ["Size (bytes)", displayCell(item.byte_size)],
        ["Uploaded", when(item.uploaded_at)],
        ["Identifier", html`<span class="id-chip">${shortId(item.source_file_id)}<button type="button" class="icon-btn" data-copy="${item.source_file_id}" aria-label="Copy file identifier">Copy</button></span>`],
      ])}
      <p><a href="/admin/batches${qs({ source_file_id: item.source_file_id })}">Batches for this file</a></p>
    </section>
  `;
}

export function batchListView({ page, query, error, loading }) {
  if (loading) return loadingState();
  const path = "/admin/batches";
  return html`
    <section class="panel">
      ${pageHeader({
        title: "Batches",
        description: "Staging batches created from source files.",
      })}
      ${errorBanner(error)}
      ${filterForm(path, query, [
        { name: "source_file_id", label: "Source file", help: "Exact identifier" },
        { name: "status", label: "Status" },
      ])}
      ${
        page && page.items && page.items.length
          ? dataTable(
              "batch",
              ["Batch", "Status", "Worksheet", "Staged", "Rejected", "Observed days"],
              page.items.map((item) => [
                html`<a href="/admin/batches/${item.batch_id}">${shortId(item.batch_id)}</a>`,
                badge(item.status),
                displayCell(item.worksheet_name),
                displayCell(item.row_count_staged),
                displayCell(item.row_count_rejected),
                html`${displayCell(item.observed_day_min)} – ${displayCell(item.observed_day_max)}`,
              ]),
            )
          : emptyState("No batches in this page.")
      }
      ${page ? pager(path, query, page.pagination) : ""}
    </section>
  `;
}

export function batchDetailView({ item, rows, query, error, loading }) {
  if (loading) return loadingState();
  if (error && !item) return html`${errorBanner(error)}`;
  const path = `/admin/batches/${item.batch_id}`;
  const clientId = item.client_id || (query && query.get && query.get("client_id")) || "";
  const canReprocess = item.status === "processed" && item.row_count_staged > 0;
  return html`
    <section class="panel">
      ${pageHeader({
        title: "Batch",
        crumbs: [
          { label: "Batches", href: "/admin/batches" },
          { label: "Batch" },
        ],
        actions: canReprocess
          ? html`<button type="button" data-batch-reprocess="${item.batch_id}" data-batch-client="${clientId}">Re-process</button>`
          : "",
      })}
      ${errorBanner(error)}
      ${lineageTrail([
        { label: "Source file", href: `/admin/source-files/${item.source_file_id}` },
        { label: "Batch", href: path },
      ])}
      ${definitionList([
        ["Status", badge(item.status)],
        ["Worksheet", displayCell(item.worksheet_name)],
        ["Declared rows", displayCell(item.row_count_declared)],
        ["Staged rows", displayCell(item.row_count_staged)],
        ["Rejected rows", displayCell(item.row_count_rejected)],
        ["Empty rows", displayCell(item.empty_row_count)],
        ["Observed days", html`${displayCell(item.observed_day_min)} – ${displayCell(item.observed_day_max)}`],
        ["Error summary", displayCell(item.error_summary)],
      ])}
      <p>
        <a href="/admin/processing-runs${qs({ batch_id: item.batch_id })}">Processing runs</a>
        ·
        <a href="/admin/facts${qs({ batch_id: item.batch_id })}">Facts</a>
      </p>
    </section>
    <section class="panel">
      <h2>Staged rows</h2>
      ${filterForm(path, query, [
        { name: "campaign_id", label: "Campaign", help: "Exact match" },
        { name: "variation_id", label: "Variation", help: "Exact match" },
        { name: "day", label: "Day" },
        { name: "source_row_number", label: "Source row" },
      ])}
      ${
        rows && rows.items && rows.items.length
          ? dataTable(
              "stg_source_row",
              ["Row", "Campaign", "Variation", "Day"],
              rows.items.map((row) => [
                displayCell(row.source_row_number),
                displayCell(row.campaign_id),
                displayCell(row.variation_id),
                displayCell(row.day),
              ]),
            )
          : emptyState("No staged rows in this page.")
      }
      ${rows ? pager(path, query, rows.pagination) : ""}
    </section>
  `;
}

export function runListView({ page, query, error, loading }) {
  if (loading) return loadingState();
  const path = "/admin/processing-runs";
  return html`
    <section class="panel">
      ${pageHeader({
        title: "Processing runs",
        description: "Each run binds Logic and Labels versions used during transformation. Publication is a separate step.",
      })}
      ${errorBanner(error)}
      ${filterForm(path, query, [
        { name: "batch_id", label: "Batch", help: "Exact identifier" },
        { name: "status", label: "Status" },
      ])}
      ${
        page && page.items && page.items.length
          ? dataTable(
              "processing_run",
              ["Run", "Status", "Started", "Finished", "Logic", "Labels", "Batch"],
              page.items.map((item) => [
                html`<a href="/admin/processing-runs/${item.processing_run_id}">${shortId(item.processing_run_id)}</a>`,
                badge(item.status),
                when(item.started_at),
                when(item.finished_at),
                shortId(item.campaign_label_version_id),
                shortId(item.label_group_version_id),
                html`<a href="/admin/batches/${item.batch_id}">${shortId(item.batch_id)}</a>`,
              ]),
            )
          : emptyState("No processing runs yet.")
      }
      ${page ? pager(path, query, page.pagination) : ""}
    </section>
  `;
}

function findingsTable(findings, verdict) {
  const items = itemsOf(findings);
  if (!items.length) {
    if (verdict == null || verdict === "" || verdict === "unavailable") {
      return emptyState("QA findings were not recorded. This is not a pass.");
    }
    return emptyState("No QA findings were recorded for this run.");
  }
  return dataTable(
    "qa_findings",
    ["Severity", "Rule", "Message", "Entity", "Context"],
    items.map((item) => [
      badge(item.severity),
      html`<span class="mono">${item.rule_id}</span>`,
      displayCell(item.message),
      displayCell(item.entity_type),
      html`<span class="mono">${item.entity_key}</span>`,
    ]),
  );
}

function qaAllowsPublish(verdict) {
  return verdict === "pass" || verdict === "warn";
}

function qaSummaryOf(findings) {
  return findings && findings.summary ? findings.summary : null;
}

function qaVerdictBanner(verdict, summary) {
  const errors = summary ? summary.error : null;
  const warnings = summary ? summary.warning : null;
  if (verdict == null || verdict === "") {
    return html`
      <div class="banner warn" data-qa-verdict="null" role="status">
        <strong>QA has not been recorded for this run.</strong>
        <div>This is not a pass. Missing findings are not a clean result. Do not publish until QA completes.</div>
      </div>
    `;
  }
  if (verdict === "unavailable") {
    return html`
      <div class="banner error" data-qa-verdict="unavailable" role="alert">
        <strong>QA did not complete.</strong>
        <div>The processing run is not QA-pass. Publication is blocked until QA finishes successfully.</div>
      </div>
    `;
  }
  if (verdict === "fail") {
    return html`
      <div class="banner error" data-qa-verdict="fail" role="alert">
        <strong>QA verdict: fail.</strong>
        <div>${errors == null ? "Blocking findings were recorded." : `${errors} blocking finding(s).`} This run cannot be published.</div>
      </div>
    `;
  }
  if (verdict === "warn") {
    return html`
      <div class="banner warn" data-qa-verdict="warn" role="status">
        <strong>QA verdict: warn.</strong>
        <div>${warnings == null ? "Warnings were recorded." : `${warnings} warning(s).`} Review findings before an explicit publish.</div>
      </div>
    `;
  }
  if (verdict === "pass") {
    return html`
      <div class="banner success" data-qa-verdict="pass" role="status">
        <strong>QA verdict: pass.</strong>
        <div>No warning or blocking findings were recorded. Publication is still an explicit step.</div>
      </div>
    `;
  }
  return html`
    <div class="banner warn" data-qa-verdict="${String(verdict)}" role="status">
      <strong>QA verdict: ${displayCell(verdict)}</strong>
      <div>This stored value is not a pass.</div>
    </div>
  `;
}

function qaOverviewList(item, findings, extra = []) {
  const summary = qaSummaryOf(findings);
  const findingTotal =
    summary && summary.total != null
      ? String(summary.total)
      : totalOf(findings) == null
        ? html`<span class="muted">Unknown</span>`
        : String(totalOf(findings));
  return definitionList([
    ["Status", badge(item.status)],
    [
      "QA verdict",
      item.qa_verdict == null
        ? html`<span class="muted">Not recorded</span>`
        : badge(item.qa_verdict),
    ],
    ["Total findings", findingTotal],
    ["Blocking findings", summary ? String(summary.error) : html`<span class="muted">Unknown</span>`],
    ["Warnings", summary ? String(summary.warning) : html`<span class="muted">Unknown</span>`],
    ["Info", summary ? String(summary.info) : html`<span class="muted">Unknown</span>`],
    ...extra,
  ]);
}

function publishForm({ session, run, disabled }) {
  return html`
    <form class="stack" data-publish-form="true">
      ${clientField(session)}
      <input type="hidden" name="processing_run_id" value="${run ? run.processing_run_id : ""}" />
      ${factScopeField()}
      <label>
        Period start (optional)
        <input name="period_start" type="date" />
      </label>
      <label>
        Period end (optional)
        <input name="period_end" type="date" />
      </label>
      <label>
        Notes (optional)
        <input name="notes" />
      </label>
      <p>
        <button type="submit" ${disabled ? raw(" disabled") : ""}>Publish Run</button>
      </p>
    </form>
  `;
}

function factScopeField() {
  return html`
    <label>
      Published facts
      <select name="fact_scope">
        <option value="processing_run" selected>This processing run only</option>
        <option value="client_current">All current client facts (cumulative monthly)</option>
      </select>
    </label>
    <p class="help">
      Cumulative keeps prior months. Default is this run so existing August publications stay compatible.
    </p>
  `;
}

export function runDetailView({
  item,
  batch,
  findings,
  facts,
  currentPublication,
  session,
  logicVersion,
  labelsVersion,
  error,
  loading,
}) {
  if (loading) return loadingState();
  if (error && !item) return html`${errorBanner(error)}`;
  const publication = currentPublication && currentPublication.publication;
  const isCurrent = publication && publication.processing_run_id === item.processing_run_id;
  const canPublish = item.status === "succeeded" && !isCurrent && qaAllowsPublish(item.qa_verdict);
  const findingCount = totalOf(findings);
  const factCount = totalOf(facts);
  const clientId =
    (batch && batch.client_id) || (session && session.client_id) || "";
  const canReprocess = batch && batch.status === "processed" && batch.row_count_staged > 0;
  const logicCell =
    logicVersion && logicVersion.version_label
      ? html`<span class="mono" title="${item.campaign_label_version_id || ""}">${logicVersion.version_label}</span>`
      : shortId(item.campaign_label_version_id);
  const labelsCell =
    labelsVersion && labelsVersion.version_label
      ? html`<span class="mono" title="${item.label_group_version_id || ""}">${labelsVersion.version_label}</span>`
      : shortId(item.label_group_version_id);
  return html`
    ${errorBanner(error)}
    ${pageHeader({
      title: "Processing run",
      crumbs: [
        { label: "Processing", href: "/admin/processing-runs" },
        { label: "Run" },
      ],
      actions: html`
        ${
          canReprocess
            ? html`<button type="button" data-batch-reprocess="${item.batch_id}" data-batch-client="${clientId}">Re-process</button>`
            : ""
        }
        <a class="btn-secondary" href="/admin/review?processing_run_id=${item.processing_run_id}" style="display:inline-flex;align-items:center;padding:0.5rem 0.85rem;border-radius:6px;">Open review</a>
        <a class="btn-secondary" href="/admin/facts${qs({ processing_run_id: item.processing_run_id })}" style="display:inline-flex;align-items:center;padding:0.5rem 0.85rem;border-radius:6px;">Inspect facts</a>
      `,
    })}
    ${lineageTrail([
      batch
        ? { label: "Source file", href: `/admin/source-files/${batch.source_file_id}` }
        : { label: "Source file" },
      { label: "Batch", href: `/admin/batches/${item.batch_id}` },
      { label: "Processing run", href: `/admin/processing-runs/${item.processing_run_id}` },
      { label: "Facts", href: `/admin/facts${qs({ processing_run_id: item.processing_run_id })}` },
      { label: isCurrent ? "Current publication" : "Publication" },
    ])}
    <section class="panel">
      <h2>Overview</h2>
      ${definitionList([
        [
          "Run",
          html`<span class="id-chip">${shortId(item.processing_run_id)}<button type="button" class="icon-btn" data-copy="${item.processing_run_id}" aria-label="Copy run identifier">Copy</button></span>`,
        ],
        ["Status", badge(item.status)],
        ["Publication", isCurrent ? html`<span class="current-flag">Current publication</span>` : html`<span class="muted">Not the current publication</span>`],
        ["Started", when(item.started_at)],
        ["Finished", when(item.finished_at)],
        ["Working-set facts", factCount == null ? html`<span class="muted">Unknown</span>` : String(factCount)],
        [
          "QA verdict",
          item.qa_verdict == null ? html`<span class="muted">Not recorded</span>` : badge(item.qa_verdict),
        ],
        ["QA findings", findingCount == null ? html`<span class="muted">Unknown</span>` : String(findingCount)],
      ])}
    </section>
    <section class="panel">
      <h2>Inputs and versions</h2>
      ${definitionList([
        ["Batch", html`<a href="/admin/batches/${item.batch_id}">${shortId(item.batch_id)}</a>`],
        ["Staged rows", batch ? displayCell(batch.row_count_staged) : html`<span class="muted">Unknown</span>`],
        ["Rejected rows", batch ? displayCell(batch.row_count_rejected) : html`<span class="muted">Unknown</span>`],
        ["Logic version", logicCell],
        ["Labels version", labelsCell],
        ["Template version", shortId(item.template_label_version_id)],
        ["Engine", displayCell(item.engine_version)],
      ])}
    </section>
    <section class="panel">
      <h2>QA</h2>
      ${qaVerdictBanner(item.qa_verdict, qaSummaryOf(findings))}
      ${qaOverviewList(item, findings, [
        ["Logic version", logicCell],
        ["Labels version", labelsCell],
      ])}
      <p class="muted">
        Findings do not automatically publish this run. Fail and incomplete QA block Publish.
        Warnings may be published only after explicit Review.
      </p>
      ${findingsTable(findings, item.qa_verdict)}
    </section>
    <section class="panel">
      <h2>Publication</h2>
      <p class="muted">
        Publication is intentional. A succeeded run still needs a pass or warn QA verdict.
      </p>
      ${canPublish ? publishForm({ session, run: item, disabled: false }) : html`<p class="muted">${item.status === "succeeded" && isCurrent ? "This run is already the current publication." : item.status === "succeeded" && !qaAllowsPublish(item.qa_verdict) ? "Publish is blocked until QA completes with pass or warn." : "Publish is available after a succeeded run with a pass or warn QA verdict."}</p>`}
    </section>
  `;
}

export function reviewView({
  item,
  findings,
  facts,
  currentPublication,
  runs,
  session,
  error,
  loading,
}) {
  if (loading) return loadingState();
  if (!item) {
    return html`
      ${errorBanner(error)}
      ${pageHeader({
        title: "Review",
        description: "Inspect processing results and QA findings before publication. Findings do not auto-publish a run.",
      })}
      <section class="panel">
        <h2>Select a processing run</h2>
        ${
          itemsOf(runs).length
            ? dataTable(
                "review_runs",
                ["Run", "Status", "Started", "Logic", "Labels"],
                itemsOf(runs).map((run) => [
                  html`<a href="/admin/review?processing_run_id=${run.processing_run_id}">${shortId(run.processing_run_id)}</a>`,
                  badge(run.status),
                  when(run.started_at),
                  shortId(run.campaign_label_version_id),
                  shortId(run.label_group_version_id),
                ]),
              )
            : emptyState("No processing runs yet.")
        }
      </section>
    `;
  }
  const publication = currentPublication && currentPublication.publication;
  const isCurrent = publication && publication.processing_run_id === item.processing_run_id;
  const canPublish = item.status === "succeeded" && !isCurrent && qaAllowsPublish(item.qa_verdict);
  const findingCount = totalOf(findings);
  return html`
    ${errorBanner(error)}
    ${pageHeader({
      title: "Review",
      description: "Working-set results, QA verdict, and findings before an explicit publish.",
      actions: html`
        <a class="btn-secondary" href="/admin/processing-runs/${item.processing_run_id}" style="display:inline-flex;align-items:center;padding:0.5rem 0.85rem;border-radius:6px;">Run detail</a>
        <a class="btn-secondary" href="/admin/facts${qs({ processing_run_id: item.processing_run_id })}" style="display:inline-flex;align-items:center;padding:0.5rem 0.85rem;border-radius:6px;">Working-set facts</a>
      `,
    })}
    ${sliceBanner("working")}
    ${qaVerdictBanner(item.qa_verdict, qaSummaryOf(findings))}
    <section class="panel">
      <h2>Run</h2>
      ${qaOverviewList(item, findings, [
        ["Logic version", shortId(item.campaign_label_version_id)],
        ["Labels version", shortId(item.label_group_version_id)],
        ["Working-set facts", totalOf(facts) == null ? html`<span class="muted">Unknown</span>` : String(totalOf(facts))],
        ["Publication", isCurrent ? html`<span class="current-flag">Current publication</span>` : html`<span class="muted">Not published as current</span>`],
      ])}
    </section>
    <section class="panel">
      <h2>QA findings</h2>
      ${
        findingCount != null && qaSummaryOf(findings) && findingCount < qaSummaryOf(findings).total
          ? html`<p class="muted">Showing ${findingCount} of ${qaSummaryOf(findings).total} findings.</p>`
          : ""
      }
      ${findingsTable(findings, item.qa_verdict)}
    </section>
    <section class="panel">
      <h2>Working-set sample</h2>
      ${
        itemsOf(facts).length
          ? dataTable(
              "review_facts",
              ["Day", "Campaign", "Name", "Template", "Cost"],
              itemsOf(facts).map((row) => [
                displayCell(row.day),
                displayCell(row.campaign_id),
                displayCell(row.campaign_name),
                displayCell(row.template_status),
                moneyCell(row.total_cost),
              ]),
            )
          : emptyState("No facts in this page.")
      }
    </section>
    <section class="panel">
      <h2>Publish</h2>
      ${canPublish ? publishForm({ session, run: item, disabled: false }) : html`<p class="muted">${item.status === "succeeded" && isCurrent ? "This run is already the current publication." : item.status === "succeeded" && !qaAllowsPublish(item.qa_verdict) ? "Publish is blocked until QA completes with pass or warn." : "Publishing is available for a succeeded run with a pass or warn QA verdict that is not already current."}</p>`}
    </section>
  `;
}

function factFilters(path, query) {
  return filterForm(path, query, [
    { name: "batch_id", label: "Batch", help: "Exact identifier" },
    { name: "processing_run_id", label: "Processing run", help: "Exact identifier" },
    { name: "campaign_id", label: "Campaign", help: "Exact match" },
    { name: "variation_id", label: "Variation", help: "Exact match" },
    { name: "day", label: "Day" },
  ]);
}

function factHref(item, base = "/admin/facts/detail") {
  const params = {
    client_id: item.client_id,
    campaign_id: item.campaign_id,
    day: item.day,
  };
  if (item.variation_id === null) {
    params.variation_null = "1";
  } else {
    params.variation_id = item.variation_id;
  }
  return `${base}${qs(params)}`;
}

export function factListView({ page, query, error, loading, path = "/admin/facts", detailHref }) {
  if (loading) return loadingState();
  const linkFor = detailHref || factHref;
  const published = path === "/client/facts";
  return html`
    <section class="panel">
      ${pageHeader({
        title: published ? "Published data" : "Facts",
        description: published
          ? "GET /api/v1/publications/current/facts — current publication only."
          : "Working-set facts from processing. This is not the published reporting slice.",
      })}
      ${sliceBanner(published ? "published" : "working")}
      ${errorBanner(error)}
      ${factFilters(path, query)}
      ${
        page && page.items && page.items.length
          ? dataTable(
              "fact_campaign_day",
              ["Day", "Campaign", "Variation", "Name", "Template", "Delivered", "Cost"],
              page.items.map((item) => [
                html`<a href="${linkFor(item)}">${item.day}</a>`,
                displayCell(item.campaign_id),
                displayCell(item.variation_id),
                displayCell(item.campaign_name),
                displayCell(item.template_status),
                displayCell(item.delivered),
                moneyCell(item.total_cost),
              ]),
            )
          : emptyState(published ? "No published facts." : "No facts in this page.")
      }
      ${page ? pager(path, query, page.pagination) : ""}
    </section>
  `;
}

export function factDetailView({ item, error, loading }) {
  if (loading) return loadingState();
  if (error) return html`${errorBanner(error)}`;
  const runHref = item.processing_run_id
    ? `/admin/processing-runs/${item.processing_run_id}`
    : "";
  const batchHref = item.batch_id ? `/admin/batches/${item.batch_id}` : "";
  return html`
    <section class="panel">
      ${pageHeader({
        title: "Fact",
        crumbs: [
          { label: "Facts", href: "/admin/facts" },
          { label: "Detail" },
        ],
      })}
      ${sliceBanner("working")}
      ${lineageTrail(
        [
          item.batch_id ? { label: "Batch", href: batchHref } : { label: "Batch (unknown)" },
          item.processing_run_id
            ? { label: "Processing run", href: runHref }
            : { label: "Processing run (unknown)" },
          { label: "Fact" },
        ],
      )}
      <h2>Grain</h2>
      ${definitionList([
        ["Campaign", displayCell(item.campaign_id)],
        ["Variation", displayCell(item.variation_id)],
        ["Variation key", displayCell(item.variation_id_key)],
        ["Day", displayCell(item.day)],
      ])}
      <h2>Measures and labels</h2>
      ${definitionList([
        ["Campaign name", displayCell(item.campaign_name)],
        ["Channel", displayCell(item.channel)],
        ["Delivered", displayCell(item.delivered)],
        ["Revenue", moneyCell(item.revenue_inr)],
        ["Template status", displayCell(item.template_status)],
        ["Total cost", moneyCell(item.total_cost)],
        ["Filter Logic 1", displayCell(item.filter_logic_1)],
        ["Label match", displayCell(item.label_match_status)],
        ["Template match", displayCell(item.template_match_status)],
      ])}
      <h2>Lineage</h2>
      ${definitionList([
        ["Batch", batchHref ? html`<a href="${batchHref}">${shortId(item.batch_id)}</a>` : displayCell(item.batch_id)],
        [
          "Processing run",
          runHref ? html`<a href="${runHref}">${shortId(item.processing_run_id)}</a>` : displayCell(item.processing_run_id),
        ],
        ["Logic version", shortId(item.campaign_label_version_id)],
        ["Labels version", shortId(item.label_group_version_id)],
      ])}
      <p>
        <a href="/admin/history${qs({
          campaign_id: item.campaign_id,
          variation_id: item.variation_id,
          day: item.day,
        })}">History for this grain</a>
      </p>
    </section>
  `;
}

export function historyListView({ page, query, error, loading }) {
  if (loading) return loadingState();
  const path = "/admin/history";
  return html`
    <section class="panel">
      ${pageHeader({
        title: "Fact history",
        description: "Superseded working-set rows. This is not publication history.",
      })}
      ${errorBanner(error)}
      ${factFilters(path, query)}
      ${
        page && page.items && page.items.length
          ? dataTable(
              "fact_campaign_day_history",
              ["Superseded at", "Campaign", "Day", "Template", "Cost", "Superseded by"],
              page.items.map((item) => [
                when(item.superseded_at),
                displayCell(item.campaign_id),
                displayCell(item.day),
                displayCell(item.template_status),
                moneyCell(item.total_cost),
                item.superseded_by_run_id
                  ? html`<a href="/admin/processing-runs/${item.superseded_by_run_id}">${shortId(item.superseded_by_run_id)}</a>`
                  : displayCell(item.superseded_by_run_id),
              ]),
            )
          : emptyState("No history rows in this page.")
      }
      ${page ? pager(path, query, page.pagination) : ""}
    </section>
  `;
}

export function publicationsView({
  session,
  runs,
  currentPublication,
  publications,
  findings,
  facts,
  selectedRun,
  publishResult,
  error,
  loading,
}) {
  if (loading) return loadingState();
  const succeeded = itemsOf(runs).filter((item) => item.status === "succeeded");
  const runOptions = succeeded.length
    ? [...succeeded].reverse().map(
        (item) =>
          html`<option value="${item.processing_run_id}"${selectedRun && selectedRun.processing_run_id === item.processing_run_id ? raw(" selected") : ""}>${item.processing_run_id} · ${item.status}</option>`,
      )
    : html`<option value="">No succeeded processing runs</option>`;
  const current = currentPublication && currentPublication.publication;
  const historyItems = itemsOf(publications);
  return html`
    ${errorBanner(error)}
    ${
      publishResult && publishResult.publication
        ? html`
            <div class="banner success" role="status">
              <strong>Publication created successfully.</strong>
              <div>
                Current publication ${shortId(publishResult.publication.publication_id)}
                for processing run ${shortId(publishResult.publication.processing_run_id)}.
              </div>
            </div>
          `
        : ""
    }
    ${pageHeader({
      title: "Publications",
      description: "Publishing sets the current pointer. Excel and published downloads follow that pointer. A succeeded run is not published automatically.",
    })}
    <section class="panel">
      <h2>Current publication</h2>
      ${
        current
          ? html`
              <p><span class="current-flag">Current</span></p>
              ${definitionList([
                [
                  "Publication",
                  html`<span class="id-chip">${shortId(current.publication_id)}<button type="button" class="icon-btn" data-copy="${current.publication_id}" aria-label="Copy publication identifier">Copy</button></span>`,
                ],
                [
                  "Processing run",
                  html`<a href="/admin/processing-runs/${current.processing_run_id}">${shortId(current.processing_run_id)}</a>`,
                ],
                ["Published", when(current.published_at)],
                ["Period start", displayCell(current.period_start)],
                ["Period end", displayCell(current.period_end)],
              ])}
            `
          : emptyState("No published data is available.")
      }
    </section>
    <section class="panel">
      <h2>Publish a run</h2>
      ${
        selectedRun
          ? definitionList([
              ["Selected run", shortId(selectedRun.processing_run_id)],
              ["Status", badge(selectedRun.status)],
              ["Logic version", shortId(selectedRun.campaign_label_version_id)],
              ["Labels version", shortId(selectedRun.label_group_version_id)],
              ["Working-set facts", totalOf(facts) == null ? html`<span class="muted">Unknown</span>` : String(totalOf(facts))],
              ["QA findings", totalOf(findings) == null ? html`<span class="muted">Unknown</span>` : String(totalOf(findings))],
            ])
          : html`<p class="muted">Choose a succeeded processing run. Review findings before you publish.</p>`
      }
      <form class="stack" data-publish-form="true">
        ${clientField(session)}
        <label>
          Processing run
          <select name="processing_run_id" required ${succeeded.length ? "" : raw(" disabled")}>
            ${runOptions}
          </select>
        </label>
        ${factScopeField()}
        <label>
          Period start (optional)
          <input name="period_start" type="date" />
        </label>
        <label>
          Period end (optional)
          <input name="period_end" type="date" />
        </label>
        <label>
          Notes (optional)
          <input name="notes" />
        </label>
        <p>
          <button type="submit" ${succeeded.length ? "" : raw(" disabled")}>Publish Run</button>
        </p>
      </form>
    </section>
    <section class="panel">
      <h2>Publication history</h2>
      <p class="muted">Prior publications remain after the current pointer moves. This is not working-set fact history.</p>
      ${
        historyItems.length
          ? dataTable(
              "publication history",
              ["State", "Publication", "Processing run", "Published", "Report"],
              historyItems.map((item, index) => [
                current && item.publication_id === current.publication_id
                  ? html`<span class="current-flag">Current</span>`
                  : html`<span class="muted">${index === 0 ? "Latest" : "Previous"}</span>`,
                shortId(item.publication_id),
                html`<a href="/admin/processing-runs/${item.processing_run_id}">${shortId(item.processing_run_id)}</a>`,
                when(item.published_at),
                html`<button type="button" class="secondary" data-download-client-report="${item.publication_id}">Download Client Report</button>`,
              ]),
            )
          : emptyState("No publication history is available.")
      }
      ${publications ? pager("/admin/publications", new URLSearchParams(), publications.pagination) : ""}
    </section>
  `;
}

export function downloadsView({ session, query, logicPage, labelsPage, currentPublication, error, loading }) {
  if (loading) return loadingState();
  const current = currentPublication && currentPublication.publication;
  const defaultClient = (session && session.client_id) || "";
  return html`
    ${errorBanner(error)}
    ${pageHeader({
      title: "Downloads",
      description: "Published outputs follow the current publication. Logic and Labels downloads are catalog artifacts, not published facts.",
    })}
    ${!(session && session.client_id) ? clientScopeBar(query || new URLSearchParams(), "/admin/downloads") : ""}
    <section class="panel">
      <h2>Published data</h2>
      <p class="muted">
        CSV and XLSX contain the current published slice only. They are not working-set extracts.
        The Client Report is the nine-sheet workbook for that publication.
      </p>
      ${
        current
          ? html`<p>Current publication ${shortId(current.publication_id)} from run ${shortId(current.processing_run_id)}.</p>`
          : emptyState("No published data is available.")
      }
      <p>
        <button type="button" data-download-client-report="current">Download Client Report</button>
        <button type="button" class="secondary" data-download-published="csv">Download CSV</button>
        <button type="button" class="secondary" data-download-published="xlsx">Download XLSX</button>
      </p>
    </section>
    <section class="panel">
      <h2>Logic downloads</h2>
      ${
        itemsOf(logicPage).length
          ? dataTable(
              "logic_downloads",
              ["Version", "Status", "Rows", "Download"],
              itemsOf(logicPage).map((item) => [
                html`<span class="mono">${item.version_label}</span>`,
                badge(item.status),
                String(item.row_count),
                html`<button type="button" class="secondary" data-catalog-download="${item.version_id}" data-catalog-kind="logic" data-catalog-client="${defaultClient}">Download Logic</button>`,
              ]),
            )
          : emptyState("No uploaded Logic versions yet.")
      }
      <p>
        <button type="button" class="secondary" data-catalog-download="packaged" data-catalog-kind="logic" data-catalog-client="${defaultClient}">
          Download packaged Logic
        </button>
      </p>
    </section>
    <section class="panel">
      <h2>Labels downloads</h2>
      ${
        itemsOf(labelsPage).length
          ? dataTable(
              "labels_downloads",
              ["Version", "Status", "Rows", "Download"],
              itemsOf(labelsPage).map((item) => [
                html`<span class="mono">${item.version_label}</span>`,
                badge(item.status),
                String(item.row_count),
                html`<button type="button" class="secondary" data-catalog-download="${item.version_id}" data-catalog-kind="labels" data-catalog-client="${defaultClient}">Download Labels</button>`,
              ]),
            )
          : emptyState("No uploaded Labels versions yet.")
      }
      <p>
        <button type="button" class="secondary" data-catalog-download="packaged" data-catalog-kind="labels" data-catalog-client="${defaultClient}">
          Download packaged Labels
        </button>
      </p>
    </section>
  `;
}

export function clientHomeView({ session, currentPublication, publications }) {
  const current = currentPublication && currentPublication.publication;
  const historyItems = itemsOf(publications);
  return html`
    <section class="panel">
      ${pageHeader({
        title: "Published reporting",
        description: "Read-only published facts from publication_current (GET /api/v1/publications/current/facts). This is not the admin working set (GET /api/v1/facts), not RLS, and not production tenant isolation.",
      })}
      ${
        session
          ? html`<p class="muted">Signed in as ${session.subject} (${session.role}).</p>`
          : ""
      }
      ${
        current
          ? definitionList([
              ["Current publication", shortId(current.publication_id)],
              ["Published", when(current.published_at)],
            ])
          : emptyState("No published data is available.")
      }
      <p>
        <a class="btn" href="/client/facts" style="display:inline-flex;align-items:center;">Open published data</a>
        <button type="button" data-download-client-report="current">Download Client Report</button>
        <button type="button" class="secondary" data-download-published="csv">Download CSV</button>
        <button type="button" class="secondary" data-download-published="xlsx">Download XLSX</button>
      </p>
      <p class="muted">The Client Report is the nine-sheet workbook for the selected publication. It does not reprocess source files.</p>
    </section>
    ${
      historyItems.length
        ? html`
            <section class="panel">
              <h2>Earlier publications</h2>
              <p class="muted">Download a prior authorized publication without republishing.</p>
              ${dataTable(
                "client publication history",
                ["State", "Published", "Report"],
                historyItems.map((item, index) => [
                  current && item.publication_id === current.publication_id
                    ? html`<span class="current-flag">Current</span>`
                    : html`<span class="muted">${index === 0 ? "Latest" : "Previous"}</span>`,
                  when(item.published_at),
                  html`<button type="button" class="secondary" data-download-client-report="${item.publication_id}">Download Client Report</button>`,
                ]),
              )}
            </section>
          `
        : ""
    }
  `;
}

export function clientFactListView(model) {
  return html`
    <p>
      <button type="button" data-download-client-report="current">Download Client Report</button>
      <button type="button" class="secondary" data-download-published="csv">Download CSV</button>
      <button type="button" class="secondary" data-download-published="xlsx">Download XLSX</button>
    </p>
    ${factListView({
      ...model,
      path: "/client/facts",
      detailHref: (item) => factHref(item, "/client/facts/detail"),
    })}
  `;
}

export function clientFactDetailView({ item, error, loading }) {
  if (loading) return loadingState();
  if (error) return html`${errorBanner(error)}`;
  return html`
    <section class="panel">
      ${pageHeader({
        title: "Published fact",
        crumbs: [
          { label: "Published data", href: "/client/facts" },
          { label: "Detail" },
        ],
      })}
      ${sliceBanner("published")}
      ${definitionList([
        ["Day", displayCell(item.day)],
        ["Campaign", displayCell(item.campaign_id)],
        ["Variation", displayCell(item.variation_id)],
        ["Campaign name", displayCell(item.campaign_name)],
        ["Template status", displayCell(item.template_status)],
        ["Delivered", displayCell(item.delivered)],
        ["Total cost", moneyCell(item.total_cost)],
        ["Revenue", moneyCell(item.revenue_inr)],
      ])}
    </section>
  `;
}

export function catalogView({
  kind,
  page,
  selected,
  session,
  query,
  uploadResult,
  actionResult,
  error,
  loading,
}) {
  if (loading) return loadingState();
  const defaultClient =
    (session && session.client_id) ||
    (query && query.get && query.get("client_id")) ||
    "";
  const items = page && page.items ? page.items : [];
  const active = page && page.processing_active ? page.processing_active : null;
  const packaged = page && page.packaged_fallback ? page.packaged_fallback : null;
  const selectedKind = kind === "labels" ? "labels" : "logic";
  const title = selectedKind === "labels" ? "Labels" : "Logic";
  const path = selectedKind === "labels" ? "/admin/labels" : "/admin/logic";
  const clientQs = defaultClient ? `&client_id=${encodeURIComponent(defaultClient)}` : "";
  return html`
    ${errorBanner(error)}
    ${
      uploadResult && uploadResult.version
        ? html`
            <div class="banner success" role="status">
              <strong>${title} version uploaded.</strong>
              <div>
                Stored draft
                <span class="mono">${uploadResult.version.version_label}</span>
                (${uploadResult.version.row_count} row(s)). Not active until you activate it.
                This does not publish.
              </div>
            </div>
          `
        : ""
    }
    ${
      actionResult
        ? html`
            <div class="banner success" role="status">
              <strong>${title} version ${actionResult.status}.</strong>
              <div>
                Version <span class="mono">${actionResult.version_label}</span>
                is now ${actionResult.status}.
              </div>
            </div>
          `
        : ""
    }
    ${pageHeader({
      title,
      description:
        selectedKind === "labels"
          ? "Labels affect processing after activation. Upload stores a draft. Activation is explicit."
          : "Logic is the campaign-mapping overlay used by processing. Upload stores a draft. Activation is explicit.",
      actions: html`<a class="btn-secondary" href="/admin/upload">Upload Center</a>`,
    })}
    ${!(session && session.client_id) ? clientScopeBar(query || new URLSearchParams(), path) : ""}
    <p>
      <a href="/admin/catalogs?kind=logic">Logic</a>
      ·
      <a href="/admin/catalogs?kind=labels">Labels</a>
    </p>
    <section class="panel">
      <h2>Currently used for processing</h2>
      ${
        active
          ? definitionList([
              ["Kind", selectedKind],
              ["Origin", displayCell(active.origin)],
              ["Version", html`<span class="mono">${active.version_label}</span>`],
              ["Status", badge(active.status)],
              ["Rows", String(active.row_count)],
            ])
          : emptyState(selectedKind === "labels" ? "No active Labels version." : "No active Logic version.")
      }
      ${
        packaged
          ? html`<p class="muted">
              Packaged fallback
              <span class="mono">${packaged.version_label}</span>
              (${packaged.origin}). Used when no uploaded version is active.
            </p>`
          : ""
      }
    </section>
    <section class="panel">
      <h2>Upload ${title}</h2>
      <form class="stack" data-catalog-upload-form="true">
        <input type="hidden" name="kind" value="${selectedKind}" />
        ${filePicker({})}
        ${clientField(session, { query })}
        <p>
          <button type="submit">${selectedKind === "labels" ? "Upload Labels" : "Upload Logic"}</button>
        </p>
      </form>
      ${catalogErrors(error, uploadResult)}
    </section>
    <section class="panel">
      <h2>Versions</h2>
      ${
        items.length
          ? dataTable(
              `${selectedKind} versions`,
              ["Version", "Status", "Origin", "Rows", "Created", "Actions"],
              items.map((item) => [
                html`<a href="${path}?version=${item.version_id}${clientQs}"><span class="mono">${item.version_label}</span></a>`,
                badge(item.status),
                displayCell(item.origin),
                String(item.row_count),
                when(item.created_at),
                html`
                  <div class="actions-cell">
                    ${
                      item.status === "draft"
                        ? html`<button type="button" data-catalog-activate="${item.version_id}" data-catalog-kind="${selectedKind}" data-catalog-client="${defaultClient}" data-catalog-label="${item.version_label}">Activate Version</button>`
                        : ""
                    }
                    ${
                      item.status === "active"
                        ? html`<button type="button" class="secondary" data-catalog-deactivate="${item.version_id}" data-catalog-kind="${selectedKind}" data-catalog-client="${defaultClient}" data-catalog-label="${item.version_label}">Deactivate Version</button>`
                        : ""
                    }
                    <button type="button" class="secondary" data-catalog-download="${item.version_id}" data-catalog-kind="${selectedKind}" data-catalog-client="${defaultClient}">Download</button>
                  </div>
                `,
              ]),
            )
          : emptyState("No uploaded versions yet. The packaged catalog remains in use.")
      }
      <p>
        <button type="button" class="secondary" data-catalog-download="packaged" data-catalog-kind="${selectedKind}" data-catalog-client="${defaultClient}">
          Download packaged ${selectedKind}
        </button>
      </p>
    </section>
    ${
      selected
        ? html`
            <section class="panel">
              <h2>Version inspector</h2>
              <p class="muted">Summary metadata only. Mapping internals stay on download.</p>
              ${definitionList([
                ["Label", html`<span class="mono">${selected.version_label}</span>`],
                ["Status", badge(selected.status)],
                ["Origin", displayCell(selected.origin)],
                ["Rows", String(selected.row_count)],
                ["Distinct keys", displayCell(selected.distinct_key_count)],
                ["Duplicate keys", displayCell(selected.duplicate_key_count)],
                ["Source file", displayCell(selected.source_filename)],
                ["Created", when(selected.created_at)],
              ])}
            </section>
          `
        : ""
    }
  `;
}

export { pageParams };
