import {
  dataTable,
  definitionList,
  lineageTrail,
  metricCard,
  overviewKpiCard,
  pageHeader,
  workflowSteps,
  icon,
  themeSwitcher,
} from "./components.js";
import { companyLabel, inspectorClients, isCompanyInactive, operationalClients } from "./roles.js";
import { overviewHref, parseDrillQuery, stripDrill, withDrill, withExplorerFromTrend, withExplorerSort, withFocus, withTrendMetric } from "./analytics-state.js";
import { renderTrendChart } from "./trend-chart.js";
import { kpiSparklineMarkup } from "./sparkline.js";
import {
  badge,
  displayCell,
  emptyState,
  errorBanner,
  html,
  loadingState,
  paginationControls,
  raw,
  formatByteLimit,
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

function formatCount(value) {
  if (value == null || value === "") return "";
  const number = Number(value);
  if (!Number.isFinite(number)) return String(value);
  return number.toLocaleString("en-US");
}

function secondsAgoLabel(iso) {
  if (!iso) return "";
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return "";
  const seconds = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (seconds <= 1) return "last update just now";
  if (seconds < 60) return `last update ${seconds} seconds ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes === 1) return "last update 1 minute ago";
  return `last update ${minutes} minutes ago`;
}

const STAGE_LABELS = {
  received: "Received",
  ingesting: "Ingesting",
  staging: "Staging",
  pending: "Pending",
  processing: "Processing",
  validating: "Validating",
  succeeded: "Ready to Publish",
  failed: "Failed",
  cancelling: "Cancelling...",
  cancelled: "Cancelled",
};

const PUBLISH_STAGE_LABELS = {
  preparing: "Preparing publication",
  validating: "Validating snapshot",
  creating: "Creating publication",
  history: "Updating historical serving data",
  finalizing: "Finalizing",
  published: "Published",
  failed: "Publication failed",
};

export function processingProgressPanel(batch, run) {
  if (!batch && !run) return "";
  const stage = (batch && batch.stage) || (run && run.stage) || "";
  const label = STAGE_LABELS[stage] || "Processing";
  const current = batch && batch.progress_current;
  const total = batch && batch.progress_total;
  const percent = batch && batch.progress_percent;
  const message = (batch && batch.progress_message) || "";
  const heartbeat = (batch && batch.progress_at) || (run && run.progress_at);
  const started = (batch && (batch.started_at || batch.created_at)) || (run && run.started_at);
  const determinate = percent != null && percent !== "" && Number.isFinite(Number(percent));
  const succeeded = stage === "succeeded";
  const cancelled = stage === "cancelled";
  const width = succeeded ? 100 : determinate ? Math.max(0, Math.min(99, Number(percent))) : 0;
  const countLine =
    current != null && total != null
      ? `${formatCount(current)} / ${formatCount(total)}`
      : current != null
        ? formatCount(current)
        : total != null
          ? `${formatCount(total)} rows discovered`
          : "";
  return html`
    <div class="processing-progress" data-processing-progress="true" data-upload-stage="${stage}">
      <div class="processing-progress-head">
        <strong>${label}${succeeded ? " 100%" : ""}</strong>
        ${
          heartbeat && !succeeded && !cancelled
            ? html`<span class="muted">Processing — ${secondsAgoLabel(heartbeat)}</span>`
            : ""
        }
      </div>
      <div
        class="progress-track"
        data-indeterminate="${!determinate && !succeeded && !cancelled ? "true" : "false"}"
        role="progressbar"
        aria-valuemin="0"
        aria-valuemax="100"
        ${determinate || succeeded ? raw(` aria-valuenow="${succeeded ? 100 : width}"`) : raw(' aria-valuetext="In progress"')}
      >
        <div class="progress-fill" style="width: ${succeeded || determinate ? width : 30}%"></div>
      </div>
      ${message ? html`<div class="processing-progress-msg">${message}</div>` : ""}
      ${countLine ? html`<div class="processing-progress-count">${countLine}</div>` : ""}
      ${started ? html`<div class="muted">Started ${when(started)}</div>` : ""}
    </div>
  `;
}

function batchRetryAction(batch) {
  if (!batch) return null;
  if (batch.stuck === true) {
    return { label: "Retry", kind: "retry" };
  }
  const stage = batch.stage || "";
  if (
    stage === "received" ||
    stage === "ingesting" ||
    stage === "staging" ||
    stage === "pending" ||
    stage === "processing" ||
    stage === "validating"
  ) {
    return null;
  }
  const staged = Number(batch.row_count_staged || 0);
  if (batch.status === "processed" && staged > 0) {
    return { label: "Re-process", kind: "reprocess" };
  }
  if (batch.status === "failed") {
    return { label: "Retry", kind: "retry" };
  }
  if (batch.status === "cancelled" || stage === "cancelled" || stage === "cancelling") {
    return null;
  }
  return null;
}

function batchCancelAction(batch) {
  if (!batch) return null;
  const stage = batch.stage || "";
  if (stage === "cancelling") {
    return { label: "Cancelling...", disabled: true };
  }
  if (
    stage === "received" ||
    stage === "ingesting" ||
    stage === "staging" ||
    stage === "pending" ||
    stage === "processing" ||
    stage === "validating"
  ) {
    return { label: "Cancel Processing", disabled: false };
  }
  return null;
}

function batchDeleteAction(batch) {
  if (!batch) return null;
  const stage = batch.stage || "";
  if (
    stage === "ingesting" ||
    stage === "staging" ||
    stage === "pending" ||
    stage === "processing" ||
    stage === "validating" ||
    stage === "cancelling" ||
    stage === "received"
  ) {
    return null;
  }
  if (
    batch.status === "failed" ||
    batch.status === "cancelled" ||
    batch.status === "processed" ||
    stage === "failed" ||
    stage === "cancelled" ||
    stage === "succeeded"
  ) {
    return { label: "Delete Upload" };
  }
  return null;
}

function batchActionButtons(batch, clientId) {
  if (!batch) return "";
  const parts = [];
  const cancel = batchCancelAction(batch);
  if (cancel) {
    parts.push(
      html`<button type="button" data-batch-cancel="${batch.batch_id}" data-batch-client="${clientId || ""}"${cancel.disabled ? raw(" disabled") : ""}>${cancel.label}</button>`,
    );
  }
  const retry = batchRetryAction(batch);
  if (retry) {
    parts.push(
      html`<button type="button" data-batch-reprocess="${batch.batch_id}" data-batch-client="${clientId || ""}" data-batch-retry-kind="${retry.kind}">${retry.label}</button>`,
    );
  }
  const del = batchDeleteAction(batch);
  if (del) {
    parts.push(
      html`<button type="button" class="danger" data-batch-delete="${batch.batch_id}" data-batch-client="${clientId || ""}">${del.label}</button>`,
    );
  }
  return parts.length ? html`${parts}` : "";
}

export function publicationProgressPanel(item) {
  if (!item) return "";
  const stage = item.stage || "";
  const label = PUBLISH_STAGE_LABELS[stage] || item.message || "Publishing";
  const succeeded = stage === "published" || item.status === "succeeded";
  const failed = stage === "failed" || item.status === "failed";
  const percent = item.progress_percent;
  const determinate = percent != null && percent !== "" && Number.isFinite(Number(percent));
  const width = succeeded ? 100 : determinate ? Math.max(0, Math.min(99, Number(percent))) : 0;
  const countLine =
    item.current_count != null && item.total_count != null
      ? `${formatCount(item.current_count)} / ${formatCount(item.total_count)}`
      : item.total_count != null
        ? `${formatCount(item.total_count)} facts`
        : "";
  return html`
    <div class="processing-progress" data-publish-progress="true" data-publish-stage="${stage}">
      <div class="processing-progress-head">
        <strong>${label}${succeeded ? " 100%" : ""}</strong>
      </div>
      <div
        class="progress-track"
        data-indeterminate="${!determinate && !succeeded && !failed ? "true" : "false"}"
        role="progressbar"
        aria-valuemin="0"
        aria-valuemax="100"
        ${determinate || succeeded ? raw(` aria-valuenow="${succeeded ? 100 : width}"`) : raw(' aria-valuetext="Publishing"')}
      >
        <div class="progress-fill" style="width: ${succeeded || determinate ? width : 30}%"></div>
      </div>
      ${item.message && item.message !== label ? html`<div class="processing-progress-msg">${item.message}</div>` : ""}
      ${countLine ? html`<div class="processing-progress-count">${countLine}</div>` : ""}
      ${item.error_summary ? html`<div class="processing-progress-msg">${item.error_summary}</div>` : ""}
    </div>
  `;
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

function companyWorkbookButton() {
  return html`<button type="button" data-download-client-report="current" data-download-company-workbook="true">Download Company Workbook</button>`;
}

function companyRefreshableWorkbookButton() {
  return html`<button type="button" class="secondary" data-download-refreshable-client-report="current">Download Refreshable Workbook</button>`;
}

function refreshableDownloadErrorHost() {
  return html`<p class="banner warn" data-refreshable-download-error="true" hidden role="alert"></p>`;
}

function activeCompanyCaption(session) {
  if (!session || !session.client_id) {
    return html`<p class="muted">Select a company before downloading a company workbook.</p>`;
  }
  const item = inspectorClients(session).find((row) => row.client_id === session.client_id);
  const label = companyLabel(item) || session.client_id;
  const code = item && item.code ? item.code : session.client_id;
  return html`<p data-active-company-download="${session.client_id}">Active company: <strong>${label}</strong> <span class="muted">(${code})</span></p>`;
}

function publicationIsCurrent(item, current) {
  return Boolean(current && item && item.publication_id === current.publication_id);
}

function snapshotStatusCell(item) {
  if (item && item.snapshot_status === "complete") {
    return html`<span data-snapshot-status="complete">Frozen snapshot</span>`;
  }
  return html`<span class="badge warn" data-snapshot-status="none">Not a frozen snapshot</span>`;
}

function publicationStateCell(item, current) {
  if (publicationIsCurrent(item, current)) {
    return html`<span class="current-flag">Current publication</span>`;
  }
  return html`<span class="muted">Historical publication</span>`;
}

function publicationIdCell(item) {
  const full = item && item.publication_id ? String(item.publication_id) : "";
  return html`<span class="id-chip">${shortId(full)}<button type="button" class="icon-btn" data-copy="${full}" aria-label="Copy publication identifier">Copy</button></span>`;
}

function historicalReportButton(item, current) {
  const id = item && item.publication_id ? String(item.publication_id) : "";
  const target = publicationIsCurrent(item, current) ? "current" : id;
  return html`<button type="button" class="secondary" data-download-client-report="${target}">Download Client Report</button>`;
}

export function rawUploadLifecycle(uploadResult) {
  if (!uploadResult) return "idle";
  if (Array.isArray(uploadResult.items)) {
    const lives = uploadResult.items.map((item) => rawUploadLifecycle(item));
    if (!lives.length) return "queued";
    if (lives.some((life) => life === "failed")) return "failed";
    if (lives.some((life) => life === "cancelled")) {
      return lives.every((life) => life === "cancelled") ? "cancelled" : "processing";
    }
    if (lives.every((life) => life === "succeeded")) return "succeeded";
    if (lives.some((life) => life === "cancelling" || life === "processing")) return "processing";
    return "queued";
  }
  const batch = uploadResult.batch;
  const run = uploadResult.processing_run;
  const batchStatus = batch && batch.status;
  const runStatus = run && run.status;
  const stage = (batch && batch.stage) || (run && run.stage) || "";
  if (runStatus === "failed" || batchStatus === "failed" || stage === "failed") return "failed";
  if (runStatus === "cancelled" || batchStatus === "cancelled" || stage === "cancelled") {
    return "cancelled";
  }
  if (stage === "cancelling") return "cancelling";
  if (
    stage === "ingesting" ||
    stage === "staging" ||
    stage === "processing" ||
    stage === "validating" ||
    stage === "pending"
  ) {
    return "processing";
  }
  if (
    runStatus === "pending" ||
    runStatus === "running" ||
    batchStatus === "staged" ||
    batchStatus === "validated"
  ) {
    return "processing";
  }
  if (runStatus === "succeeded" || batchStatus === "processed" || stage === "succeeded") {
    return "succeeded";
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
  if (life === "queued" || life === "processing" || life === "cancelling") {
    if (batch && batch.stuck === true) {
      const reason =
        (batch && batch.error_summary) ||
        (uploadRunRecord && uploadRunRecord.error_summary) ||
        "No worker is running this job.";
      return html`
        <div class="banner" data-upload-status="true" role="status">
          <strong>Processing stopped. ${fileCount > 1 ? "One or more files need recovery." : "This file needs recovery."}</strong>
          <div>
            ${
              batchId
                ? html`Batch ${shortId(batchId)} is ${badge(batch.status)}${
                    batch.progress_at || (uploadRunRecord && uploadRunRecord.progress_at)
                      ? html` · last heartbeat ${when(
                          (uploadRunRecord && uploadRunRecord.progress_at) || batch.progress_at
                        )}`
                      : ""
                  }. ${reason}`
                : reason
            }
            Retry is only for recovery. This does not publish.
          </div>
          <p>
            ${
              batchId
                ? html`${batchActionButtons(batch, uploadResult.client_id || "")} · <a href="/admin/batches/${batchId}">Open batch</a> · `
                : ""
            }
            <a href="/admin/processing-runs">Open processing</a>
          </p>
        </div>
      `;
    }
    const stage =
      (batch && batch.stage) ||
      (uploadRunRecord && uploadRunRecord.stage) ||
      (life === "queued" ? "received" : "processing");
    const stageLabel = STAGE_LABELS[stage] || "Processing";
    const fileLabel =
      fileCount > 1
        ? `The website stays usable while these ${fileCount} files are processed.`
        : "The website stays usable while this file is processed.";
    return html`
      <div class="banner" data-upload-status="true" data-upload-stage="${stage}" role="status">
        <strong>${stageLabel}. ${fileLabel}</strong>
        ${processingProgressPanel(batch, uploadRunRecord)}
        <div>
          ${
            batchId
              ? html`Batch ${shortId(batchId)} is ${badge(batch.status)}${
                  batch.progress_at || (uploadRunRecord && uploadRunRecord.progress_at)
                    ? html` · ${secondsAgoLabel(
                        (batch && batch.progress_at) || (uploadRunRecord && uploadRunRecord.progress_at)
                      )}`
                    : ""
                }.`
              : "The API accepted the workbook."
          }
          Upload progresses automatically. Retry is only for failed recovery. This does not publish.
        </div>
        <p>
          ${
            batchId
              ? html`${batchActionButtons(batch, uploadResult.client_id || "")} · <a href="/admin/batches/${batchId}">Open batch</a> · `
              : ""
          }
          <a href="/admin/processing-runs">Open processing</a>
        </p>
      </div>
    `;
  }
  if (life === "cancelled") {
    return html`
      <div class="banner" data-upload-status="true" data-upload-stage="cancelled" role="status">
        <strong>Cancelled.</strong>
        ${processingProgressPanel(batch, uploadRunRecord)}
        <div>Processing stopped. Temporary work was discarded. No publication was created.</div>
        <p>
          ${
            batchId
              ? html`${batchActionButtons(batch, uploadResult.client_id || "")} · <a href="/admin/batches/${batchId}">Open batch</a> · `
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
              ? html`${batchActionButtons(batch, uploadResult.client_id || "")} · <a href="/admin/batches/${batchId}">Open batch</a> · `
              : ""
          }
          <a href="/admin/processing-runs">Open processing</a>
        </p>
      </div>
    `;
  }
  return html`
    <div class="banner success" data-upload-status="true" data-upload-stage="succeeded" role="status">
      <strong>Ready to Publish 100%. Upload completed successfully.</strong>
      ${processingProgressPanel(
        batch
          ? { ...batch, stage: "succeeded", progress_percent: 100 }
          : null,
        uploadRunRecord,
      )}
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
        html`<p>
          ${batchActionButtons(batch, uploadResult.client_id || "")}
          ${
            uploadRun
              ? html`${batch ? " · " : ""}<a href="/admin/processing-runs/${uploadRun}">Open processing run</a>
              ·
              <a href="/admin/review?processing_run_id=${uploadRun}">Review / QA</a>
              ·
              <a href="/admin/publications">Publications</a>`
              : ""
          }
        </p>`
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

export function credentialView({ error, nextPath, setupRequired, setupDone, standalone }) {
  const theme = standalone ? html`<div class="login-theme">${themeSwitcher()}</div>` : "";
  if (setupRequired) {
    return html`
      <div class="login-shell" id="main">
        ${theme}
        <section class="login-card">
          <p class="brand">DFIP</p>
          <p class="eyebrow">Initial setup</p>
          <h1>Create the publisher account</h1>
          <p class="lede">
            Enter the username and password for the one V1 operator account.
            DFIP does not generate credentials. This is one-time setup, not
            public registration.
          </p>
          ${errorBanner(error)}
          <form data-publisher-setup-form="true" class="stack">
            <input type="hidden" name="next" value="${nextPath || "/admin"}" />
            <label>
              Publisher username
              <input name="username" type="text" autocomplete="username" required maxlength="320" data-publisher-setup-username="true" />
            </label>
            <label>
              Publisher password
              <input name="password" type="password" autocomplete="new-password" required maxlength="1024" data-publisher-setup-password="true" />
            </label>
            <label>
              Confirm password
              <input name="confirm_password" type="password" autocomplete="new-password" required maxlength="1024" data-publisher-setup-confirm="true" />
            </label>
            <button type="submit" data-publisher-setup-button="true">Create publisher account</button>
          </form>
        </section>
      </div>
    `;
  }
  return html`
    <div class="login-shell" id="main">
      ${theme}
      <section class="login-card">
        <p class="brand">DFIP</p>
        <p class="eyebrow">Publisher control center</p>
        <h1>Sign in</h1>
        <p class="lede">
          Sign in with your username and password. Access is limited to your
          organization. Credentials stay in this browser tab only.
        </p>
        ${
          setupDone
            ? html`<p class="banner success" data-publisher-setup-done="true">Publisher account created. Sign in with the username and password you entered. DFIP stored a password hash only.</p>`
            : ""
        }
        ${errorBanner(error)}
        <form data-credential-form="true" class="stack">
          <input type="hidden" name="next" value="${nextPath || "/client/overview"}" />
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

export function companySelectView({ session, error }) {
  const choices = operationalClients(session);
  const inactive = inspectorClients(session).filter((item) => isCompanyInactive(item));
  return html`
    <section class="panel" data-company-select-panel="true">
      ${pageHeader({
        title: "Select a company",
        description: "This publisher can operate more than one tenant. Choose an active company before upload, process, QA, or publish. Inactive companies stay in the registry for historical recovery and cannot be selected for live operations.",
      })}
      ${errorBanner(error)}
      <div class="stack">
        ${choices.map(
          (item) => html`
            <form data-company-select-form="true">
              <input type="hidden" name="client_id" value="${item.client_id}" />
              <button type="submit" data-company-select-button="${item.client_id}">
                ${companyLabel(item)}
              </button>
              <p class="muted">${item.code || item.client_id}</p>
            </form>
          `,
        )}
        ${
          inactive.length
            ? html`<p class="muted">Inactive companies are listed on Companies. They cannot be selected for live operations. Deactivate, then Delete Company to permanently remove a tenant.</p>`
            : ""
        }
        <p><a href="/admin/companies">Manage companies</a></p>
      </div>
    </section>
  `;
}

export function companiesView({ session, companies, error }) {
  const rows = Array.isArray(companies) ? companies : [];
  const current = session && session.client_id ? session.client_id : "";
  return html`
    <section class="panel" data-company-registry="true">
      ${pageHeader({
        title: "Companies",
        description: "Companies this publisher is authorized to operate. Add Company creates a new tenant id. Rename changes the display name only. Deactivate blocks new live work and keeps history. Delete Company permanently removes a deactivated company and its data.",
      })}
      ${errorBanner(error)}
      <div class="company-onboarding" data-company-onboarding="true">
        <p><strong>Processing-ready</strong> after you add and select a company: upload, process, QA, and publish use packaged Logic, Labels, templates, and rate cards. Optional tenant Logic/Labels: upload and activate on <a href="/admin/logic">Logic</a> and <a href="/admin/labels">Labels</a> while this company is selected. Templates and rate cards stay packaged shared content; this tenant does not store another company's catalog UUIDs.</p>
        <p class="muted"><strong>Lifecycle:</strong> Deactivate keeps all data and memberships. Inactive companies cannot be selected for upload, process, publish, or client-portal use and are hidden from the normal company selector. Publisher historical publication downloads remain available while inactive. Delete Company permanently deletes the company and all company-owned application data. Direct SQL reporting views do not automatically hide inactive companies.</p>
      </div>
      <form class="company-create" data-company-create-form="true">
        <label>
          <span>Company name</span>
          <input name="name" maxlength="200" required data-company-create-name="true" placeholder="Display name" />
        </label>
        <button type="submit" data-company-create-button="true">+ Add Company</button>
      </form>
      ${
        rows.length
          ? rows.map(
              (item) => html`
                <article class="company-row" data-company-id="${item.client_id}" data-company-status="${item.lifecycle_status || "active"}">
                  <div>
                    <p class="company-row-name" data-company-name="${item.client_id}">${item.name}</p>
                    <p class="muted">${item.code || ""} · <span class="mono">${item.client_id}</span></p>
                    <p>
                      <span class="role-pill ${item.lifecycle_status === "inactive" ? "lifecycle-inactive" : "lifecycle-active"}" data-company-status-chip="${item.client_id}">${item.lifecycle_status === "inactive" ? "Inactive" : "Active"}</span>
                    </p>
                    ${
                      item.lifecycle_status === "inactive"
                        ? html`
                            <p class="muted" data-company-deactivated="${item.client_id}">Deactivated ${item.deactivated_at || ""}</p>
                            <p class="muted" data-company-purge-eligible="${item.client_id}">Purge eligible after ${item.purge_eligible_after || "not clock-eligible (operator policy unset)"}</p>
                          `
                        : ""
                    }
                    ${
                      item.client_id === current && item.lifecycle_status !== "inactive"
                        ? html`
                            <p class="muted" data-company-active="${item.client_id}">Active company</p>
                            ${companyWorkbookButton()}
                            ${companyRefreshableWorkbookButton()}
                            ${refreshableDownloadErrorHost()}
                          `
                        : ""
                    }
                    ${
                      item.lifecycle_status === "inactive"
                        ? html`
                            <button type="button" class="secondary" data-company-reactivate="${item.client_id}">Reactivate</button>
                            <button type="button" class="danger" data-company-delete="${item.client_id}" data-company-delete-name="${item.name}">Delete Company</button>
                          `
                        : html`<button type="button" class="secondary" data-company-deactivate="${item.client_id}">Deactivate</button>`
                    }
                  </div>
                  <form data-company-rename-form="true">
                    <input type="hidden" name="client_id" value="${item.client_id}" />
                    <label>
                      <span class="visually-hidden">Display name</span>
                      <input name="name" value="${item.name}" maxlength="200" required data-company-rename-input="${item.client_id}" />
                    </label>
                    <button type="submit" data-company-rename-button="${item.client_id}">Rename</button>
                  </form>
                  ${
                    item.lifecycle_status === "inactive"
                      ? ""
                      : html`
                  <form class="company-client-create" data-company-client-form="true">
                    <input type="hidden" name="client_id" value="${item.client_id}" />
                    <p class="muted">Company: <strong>${item.name}</strong></p>
                    <label>
                      <span>Client username</span>
                      <input name="username" maxlength="320" required autocomplete="off" data-company-client-username="${item.client_id}" />
                    </label>
                    <label>
                      <span>Client password</span>
                      <input name="password" type="password" minlength="12" maxlength="1024" required autocomplete="new-password" data-company-client-password="${item.client_id}" />
                      <span class="muted">At least 12 characters. DFIP stores a password hash only.</span>
                    </label>
                    <label>
                      <span>Confirm password</span>
                      <input name="confirm_password" type="password" minlength="12" maxlength="1024" required autocomplete="new-password" data-company-client-confirm="${item.client_id}" />
                    </label>
                    <button type="submit" data-company-client-button="${item.client_id}">Create Client Account</button>
                  </form>
                      `
                  }
                </article>
              `,
            )
          : emptyState("No companies are assigned to this publisher.")
      }
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
  ready,
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
                ["API liveness", displayCell(health.status)],
                ["Application", displayCell(health.application)],
                ["Environment", displayCell(health.environment)],
              ])
            : emptyState("Health is unavailable.")
        }
        ${
          ready
            ? definitionList([
                ["Readiness", displayCell(ready.status)],
                ["Database", displayCell(ready.database && ready.database.status)],
                ["Source storage", displayCell(ready.storage && ready.storage.status)],
                ["Worker", displayCell(ready.worker)],
              ])
            : html`<p class="muted" style="margin-top:0.9rem;">Readiness is unavailable. Public /health is liveness only and does not prove the database.</p>`
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
  uploadMaxBytes,
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
  const sizeHint = formatByteLimit(uploadMaxBytes);
  const sizeLine = sizeHint
    ? html`<p class="muted">Maximum workbook size: ${sizeHint} (${Number(uploadMaxBytes)} bytes). Raw, Logic, and Labels share this API limit.</p>`
    : "";
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
        ${sizeLine}
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
  return html`
    <section class="panel">
      ${pageHeader({
        title: "Batch",
        crumbs: [
          { label: "Batches", href: "/admin/batches" },
          { label: "Batch" },
        ],
        actions: batchActionButtons(item, clientId),
      })}
      ${errorBanner(error)}
      ${lineageTrail([
        { label: "Source file", href: `/admin/source-files/${item.source_file_id}` },
        { label: "Batch", href: path },
      ])}
      <div data-batch-progress-host="true">${processingProgressPanel(item)}</div>
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
        description: "Each run binds Logic and Labels versions used during transformation. Failed and abandoned runs can be retried from the batch. Filter status=failed to list failed and abandoned work. Publication is a separate step.",
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
              ["Run", "Status", "Progress", "Started", "Finished", "Error", "Batch"],
              page.items.map((item) => [
                html`<a href="/admin/processing-runs/${item.processing_run_id}">${shortId(item.processing_run_id)}</a>`,
                badge(item.status),
                when(item.progress_at),
                when(item.started_at),
                when(item.finished_at),
                displayCell(item.error_summary),
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
      <div data-publish-progress-host="true"></div>
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
        ${batchActionButtons(batch, clientId)}
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
        ["Progress", item.progress_at ? when(item.progress_at) : html`<span class="muted">Not recorded</span>`],
        ["Publication", isCurrent ? html`<span class="current-flag">Current publication</span>` : html`<span class="muted">Not the current publication</span>`],
        ["Error summary", displayCell(item.error_summary)],
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
        description: "Superseded working-set rows from fact_campaign_day_history. This is not Publications report history and is not a client report catalog.",
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
  const inactive = isCompanyInactive(
    inspectorClients(session).find((item) => item.client_id === (session && session.client_id)),
  );
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
      description: "Publishing sets the current pointer. Current static recovery and refreshable workbooks follow that pointer. Historical Client Report downloads stay bound to a publication_id and do not refresh themselves. A succeeded run is not published automatically. This is not working-set Fact history.",
    })}
    <section class="panel">
      <h2>Current publication</h2>
      ${
        current
          ? html`
              <p><span class="current-flag">Current publication</span></p>
              ${definitionList([
                [
                  "Publication",
                  publicationIdCell(current),
                ],
                [
                  "Processing run",
                  html`<a href="/admin/processing-runs/${current.processing_run_id}">${shortId(current.processing_run_id)}</a>`,
                ],
                ["Published", when(current.published_at)],
                ["Period start", displayCell(current.period_start)],
                ["Period end", displayCell(current.period_end)],
                ["Snapshot", snapshotStatusCell(current)],
                ["Snapshot rows", displayCell(current.snapshot_row_count)],
                ["Notes", displayCell(current.notes)],
              ])}
            `
          : emptyState("No published data is available.")
      }
      ${activeCompanyCaption(session)}
      ${
        inactive
          ? html`<p class="muted">This company is inactive. Current workbooks and new publishes are blocked. Historical Client Report downloads below remain available until the company is deleted.</p>`
          : html`
      <p class="muted">Download Company Workbook is current static recovery. Download Refreshable Workbook follows this company's current publication after Excel Refresh All. Neither file is a historical report.</p>
      <p>${companyWorkbookButton()} ${companyRefreshableWorkbookButton()}</p>
      ${refreshableDownloadErrorHost()}
          `
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
          <button type="submit" ${succeeded.length && !inactive ? "" : raw(" disabled")}>Publish Run</button>
        </p>
      </form>
    </section>
    <section class="panel">
      <h2>Publication history</h2>
      <p class="muted">Prior publications remain after the current pointer moves. Historical Client Report downloads do not follow publication_current and are not refreshable. This is not working-set Fact history.</p>
      ${
        historyItems.length
          ? dataTable(
              "publication history",
              ["State", "Snapshot", "Publication", "Published", "Period", "Rows", "Notes", "Report"],
              historyItems.map((item) => [
                publicationStateCell(item, current),
                snapshotStatusCell(item),
                publicationIdCell(item),
                when(item.published_at),
                html`${displayCell(item.period_start)} – ${displayCell(item.period_end)}`,
                displayCell(item.snapshot_row_count),
                displayCell(item.notes),
                historicalReportButton(item, current),
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
        Download Company Workbook is the static recovery snapshot for the active company current publication.
        Download Refreshable Workbook keeps the PublishedFacts query so Excel Refresh All can
        load later published months for that same company. The download writes the current
        session JWT into Settings for Refresh All (it expires; re-download for a fresh token).
        Refreshable workbooks are current-only.
        Historical publication downloads stay on Publications history and do not update themselves.
      </p>
      ${activeCompanyCaption(session)}
      ${
        current
          ? html`<p>Current publication ${shortId(current.publication_id)} from run ${shortId(current.processing_run_id)}.</p>`
          : emptyState("No published data is available.")
      }
      <p>
        ${companyWorkbookButton()}
        ${companyRefreshableWorkbookButton()}
        <button type="button" class="secondary" data-download-published="csv">Download CSV</button>
        <button type="button" class="secondary" data-download-published="xlsx">Download XLSX</button>
      </p>
      ${refreshableDownloadErrorHost()}
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

function formatMoneyDisplay(value) {
  if (value == null || value === "") return "n/a";
  const number = Number(value);
  if (!Number.isFinite(number)) return String(value);
  return number.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function formatRateDisplay(value) {
  if (value == null || value === "") return "n/a";
  const number = Number(value);
  if (!Number.isFinite(number)) return String(value);
  return `${(number * 100).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}%`;
}

function formatRoasDisplay(value) {
  if (value == null || value === "") return "n/a";
  const number = Number(value);
  if (!Number.isFinite(number)) return String(value);
  return number.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 4 });
}

function formatKpiValue(kind, value) {
  if (kind === "count") {
    if (value == null || value === "") return "n/a";
    return formatCount(value) || "n/a";
  }
  if (kind === "rate") return formatRateDisplay(value);
  if (kind === "roas") return formatRoasDisplay(value);
  return formatMoneyDisplay(value);
}

function formatDelta(kind, delta, deltaPct) {
  const parts = [];
  if (delta != null && delta !== "") {
    const prefix = Number(delta) > 0 ? "+" : "";
    parts.push(`${prefix}${formatKpiValue(kind, delta)}`);
  }
  if (deltaPct != null && deltaPct !== "") {
    const pct = Number(deltaPct) * 100;
    if (Number.isFinite(pct)) {
      const prefix = pct > 0 ? "+" : "";
      parts.push(`${prefix}${pct.toLocaleString("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 })}%`);
    }
  }
  return parts.join(" · ");
}

function overviewCompanyLabel(session, data) {
  if (data && data.company_name) return data.company_name;
  if (!session) return "No company selected";
  const item = inspectorClients(session).find((row) => row.client_id === session.client_id);
  if (item) return companyLabel(item);
  if (session.client_id) return session.client_id;
  return "No company selected";
}

function selectedSet(values) {
  return new Set(Array.isArray(values) ? values.map(String) : []);
}

function filterOptions(name, options, selected) {
  const picked = selectedSet(selected);
  return (options || []).map(
    (item) =>
      html`<label class="filter-picker-option"><input type="checkbox" name="${name}" value="${item.value}" data-filter-picker-option="true" data-label="${item.label}" ${picked.has(String(item.value)) ? raw(" checked") : ""} /><span title="${item.label}">${item.label}</span></label>`,
  );
}

export function filterPickerSummary(selected, options, kind) {
  const items = options || [];
  const picked = Array.isArray(selected) ? selected.map(String) : [];
  const labels = picked.map((value) => optionLabel(items, value));
  const total = items.length;
  if (kind === "campaign") {
    if (!picked.length) return "All selected";
    if (picked.length === 1) return labels[0] || "1 selected";
    return `${picked.length} selected`;
  }
  if (!picked.length) return total ? `All selected (${total})` : "All selected";
  if (picked.length === total) return `All selected (${total})`;
  if (picked.length === 1) return labels[0] || "1 selected";
  if (picked.length === 2) return `${labels[0]}, ${labels[1]}`;
  return `${labels[0]}, ${labels[1]} +${picked.length - 2}`;
}

export function syncFilterPickerSummary(picker) {
  if (!picker) return;
  const name = picker.getAttribute("data-filter-picker") || "";
  const boxes = [...picker.querySelectorAll(`input[type="checkbox"][name="${name}"]`)];
  const options = boxes.map((box) => ({
    value: box.value,
    label: box.getAttribute("data-label") || box.value,
  }));
  const selected = boxes.filter((box) => box.checked).map((box) => box.value);
  const summary = picker.querySelector("[data-filter-picker-summary]");
  if (summary) summary.textContent = filterPickerSummary(selected, options, name === "campaign_id" ? "campaign" : "dim");
  const trigger = picker.querySelector("[data-filter-picker-open]");
  if (trigger) trigger.setAttribute("aria-expanded", picker.classList.contains("is-open") ? "true" : "false");
}

function overviewFilterPicker({ name, label, options, selected, searchPlaceholder, searchAttr, listAttr }) {
  const items = options || [];
  const picked = selectedSet(selected);
  const selectedValues = items.filter((item) => picked.has(String(item.value))).map((item) => String(item.value));
  const kind = name === "campaign_id" ? "campaign" : "dim";
  const showSearch = Boolean(searchPlaceholder) && (kind === "campaign" || name === "channel" || items.length >= 6);
  const searchId = `filter-search-${name}`;
  return html`
    <div class="filter-picker" data-filter-picker="${name}">
      <span class="filter-picker-label" id="filter-label-${name}">${label}</span>
      <button type="button" class="filter-picker-trigger" data-filter-picker-open="${name}" aria-haspopup="dialog" aria-expanded="false" aria-controls="filter-popover-${name}" aria-labelledby="filter-label-${name} filter-summary-${name}">
        <span id="filter-summary-${name}" data-filter-picker-summary="${name}">${filterPickerSummary(selectedValues, items, kind)}</span>
        <span class="filter-picker-caret" aria-hidden="true">▾</span>
      </button>
      <div class="filter-picker-popover" id="filter-popover-${name}" data-filter-picker-popover="${name}" hidden role="dialog" aria-label="${label}">
        ${
          showSearch
            ? html`<input id="${searchId}" type="search" class="filter-picker-search" placeholder="${searchPlaceholder}" aria-label="${searchPlaceholder}" data-filter-picker-search="${name}" ${searchAttr ? raw(` ${searchAttr}`) : ""} />`
            : ""
        }
        <div class="filter-picker-list" role="group" aria-labelledby="filter-label-${name}" data-filter-picker-list="${name}" ${listAttr ? raw(` ${listAttr}`) : ""}>
          ${filterOptions(name, items, selectedValues)}
        </div>
        <div class="filter-picker-toolbar">
          <button type="button" class="action-link" data-filter-picker-all="${name}" aria-label="Select all ${label}">Select all</button>
          <button type="button" class="action-link" data-filter-picker-clear="${name}" aria-label="Clear ${label}">Clear</button>
        </div>
      </div>
    </div>
  `;
}

function optionLabel(options, value) {
  const found = (options || []).find((item) => String(item.value) === String(value));
  return found && found.label ? found.label : value;
}

function requestedCompareMonth(query, applied) {
  const compare = (query && query.get && query.get("compare")) || (applied && applied.compare) || "auto";
  if (compare === "none") return "none";
  return (query && query.get && query.get("compare_month_start")) || "";
}

function filterChip(key, value, label) {
  return html`
    <span class="overview-chip" data-filter-chip="${key}">
      <span>${label}</span>
      <button type="button" class="overview-chip-remove" data-filter-chip-clear="${key}" data-filter-chip-value="${value}" aria-label="Remove ${label}">×</button>
    </span>
  `;
}

function overviewFilterChips(data, query) {
  const applied = (data && data.applied) || {};
  const options = (data && data.options) || {};
  const chips = [];
  const requestedMonth = query && query.get ? query.get("compare_month_start") : "";
  const requestedCompare = query && query.get ? query.get("compare") : "";
  if (requestedMonth && requestedCompare !== "none") {
    const month = (options.months || []).find((item) => item.month_start === requestedMonth);
    chips.push(filterChip("compare_month_start", requestedMonth, `Compare: ${month ? month.month_label : requestedMonth}`));
  }
  for (const value of applied.channels || []) {
    chips.push(filterChip("channel", value, `Channel: ${optionLabel(options.channels, value)}`));
  }
  for (const value of applied.filter_logic_1 || []) {
    chips.push(filterChip("filter_logic_1", value, `Logic: ${optionLabel(options.filter_logic_1, value)}`));
  }
  for (const value of applied.filter_logic_1_group || []) {
    chips.push(filterChip("filter_logic_1_group", value, `Group: ${optionLabel(options.filter_logic_1_group, value)}`));
  }
  const campaigns = applied.campaign_ids || [];
  if (campaigns.length > 2) {
    chips.push(filterChip("campaign_id", "", `Campaigns: ${campaigns.length}`));
  } else {
    for (const value of campaigns) {
      chips.push(filterChip("campaign_id", value, `Campaign: ${optionLabel(options.campaigns, value)}`));
    }
  }
  const dimActive = (applied.channels || []).length || (applied.filter_logic_1 || []).length || (applied.filter_logic_1_group || []).length || campaigns.length;
  const body = chips.length
    ? html`<div class="overview-filter-chips">${chips}</div>`
    : html`<p class="muted overview-filter-chips-empty">All values</p>`;
  return html`
    <div class="overview-active-filters" data-overview-active-filters="true" data-overview-dim-filters="${dimActive ? "true" : "false"}">
      <span class="overview-kicker">Active filters</span>
      ${body}
    </div>
  `;
}

export function overviewFilterPatchModel(data, query) {
  const options = (data && data.options) || {};
  const applied = (data && data.applied) || {};
  const months = options.months || [];
  const latest = months.length ? months[months.length - 1].month_start : "";
  const grain = applied.period || "month";
  const compare = (query && query.get && query.get("compare")) || applied.compare || "auto";
  const dropped = (data && data.dropped_filters) || {};
  const droppedKeys = Object.keys(dropped);
  const compareMonthValue = requestedCompareMonth(query, applied);
  return {
    latest,
    grain,
    compare: compare === "none" ? "none" : "auto",
    compareMonthValue,
    applied,
    options,
    months,
    droppedKeys,
    chipsHtml: overviewFilterChips(data, query),
    monthOptions: months.map(
      (item) =>
        html`<option value="${item.month_start}" ${item.month_start === (applied.month_start || latest) ? raw(" selected") : ""}>${item.month_label}</option>`,
    ),
    compareMonthOptions: [
      html`<option value="none" ${compareMonthValue === "none" ? raw(" selected") : ""}>None</option>`,
      html`<option value="" ${compareMonthValue === "" ? raw(" selected") : ""}>Auto</option>`,
      ...months.map(
        (item) =>
          html`<option value="${item.month_start}" ${item.month_start === compareMonthValue ? raw(" selected") : ""}>${item.month_label}</option>`,
      ),
    ],
    channelOptions: filterOptions("channel", options.channels, applied.channels),
    logic1Options: filterOptions("filter_logic_1", options.filter_logic_1, applied.filter_logic_1),
    logic1GroupOptions: filterOptions("filter_logic_1_group", options.filter_logic_1_group, applied.filter_logic_1_group),
    campaignOptions: filterOptions("campaign_id", options.campaigns, applied.campaign_ids),
  };
}

export function overviewFilterBar(data, query) {
  const model = overviewFilterPatchModel(data, query);
  const { grain, compare, applied, options, latest, droppedKeys } = model;
  return html`
    <form class="overview-filters" data-overview-filters="true" data-latest-month="${latest}">
      <div class="overview-filter-grid">
        <div class="overview-filter-row overview-filter-row-period">
          <label>
            Period
            <select name="period" data-overview-autosubmit="true">
              <option value="month" ${grain === "month" ? raw(" selected") : ""}>Published month</option>
              <option value="range" ${grain === "range" ? raw(" selected") : ""}>Date range</option>
              <option value="all_history" ${grain === "all_history" ? raw(" selected") : ""}>All published history</option>
            </select>
          </label>
          <label>
            Month
            <select name="month_start" data-overview-autosubmit="true" ${grain === "month" ? "" : raw(" disabled")}>
              ${model.monthOptions}
            </select>
          </label>
          <label>
            From
            <input name="day_from" type="date" value="${applied.day_from || options.published_day_min || ""}" min="${options.published_day_min || ""}" max="${options.published_day_max || ""}" ${grain === "range" ? "" : raw(" disabled")} data-overview-autosubmit="true" />
          </label>
          <label>
            To
            <input name="day_to" type="date" value="${applied.day_to || options.published_day_max || ""}" min="${options.published_day_min || ""}" max="${options.published_day_max || ""}" ${grain === "range" ? "" : raw(" disabled")} data-overview-autosubmit="true" />
          </label>
          <label>
            Comparison
            <select name="compare" data-overview-autosubmit="true">
              <option value="auto" ${compare === "auto" ? raw(" selected") : ""}>Auto prior published month</option>
              <option value="none" ${compare === "none" ? raw(" selected") : ""}>None</option>
            </select>
          </label>
          <label>
            Compare month
            <select name="compare_month_start" data-overview-autosubmit="true">
              ${model.compareMonthOptions}
            </select>
          </label>
        </div>
        <div class="overview-filter-row overview-filter-row-dims">
          ${overviewFilterPicker({
            name: "channel",
            label: "Channel",
            options: options.channels,
            selected: applied.channels,
            searchPlaceholder: "Search channels...",
          })}
          ${overviewFilterPicker({
            name: "filter_logic_1",
            label: "Filter Logic 1",
            options: options.filter_logic_1,
            selected: applied.filter_logic_1,
            searchPlaceholder: "Search Filter Logic 1...",
          })}
          ${overviewFilterPicker({
            name: "filter_logic_1_group",
            label: "Filter Logic 1 group",
            options: options.filter_logic_1_group,
            selected: applied.filter_logic_1_group,
            searchPlaceholder: "Search groups...",
          })}
          ${overviewFilterPicker({
            name: "campaign_id",
            label: "Campaign",
            options: options.campaigns,
            selected: applied.campaign_ids,
            searchPlaceholder: "Search campaigns",
            searchAttr: 'data-overview-campaign-search="true"',
            listAttr: 'id="overview-campaigns" data-overview-campaigns="true"',
          })}
        </div>
      </div>
      <div class="overview-filter-foot">
        ${model.chipsHtml}
        <p class="overview-filter-actions">
          <a class="btn-secondary" href="/client/overview" aria-label="Clear all filters">Clear all</a>
          <button type="submit">Apply filters</button>
        </p>
        ${
          droppedKeys.length
            ? html`<p class="banner warn" data-overview-dropped="true">Some selections are not in this period and were cleared: ${droppedKeys.join(", ")}.</p>`
            : ""
        }
      </div>
    </form>
  `;
}

const TREND_METRIC_OPTIONS = [
  ["total_cost", "Total Cost"],
  ["revenue_inr", "Revenue"],
  ["overall_roas", "Overall ROAS"],
  ["delivered", "Delivered"],
  ["unique_clicks", "Unique Clicks"],
  ["unique_conversions", "Unique Conversions"],
  ["delivery_rate", "Delivery Rate"],
  ["ctr_del_to_clicks", "CTR (Delivered → Clicks)"],
];

const TREND_GRAIN_OPTIONS = [
  ["day", "Day"],
  ["week", "Week"],
  ["month", "Month"],
];

const TREND_BREAKDOWN_OPTIONS = [
  ["", "None"],
  ["campaign_id", "Campaign"],
  ["channel", "Channel"],
  ["filter_logic_1", "Filter Logic 1"],
  ["filter_logic_1_group", "Filter Logic 1 group"],
];

function selectedQueryValue(query, key, fallback) {
  const value = query && query.get ? query.get(key) : "";
  return value || fallback;
}

function overviewPanelHead({ title, description, meta, actions }) {
  return html`
    <summary class="overview-panel-head">
      <span class="overview-panel-copy">
        <span class="overview-kicker">${title}</span>
        ${description ? html`<span class="overview-lede">${description}</span>` : ""}
      </span>
      <span class="overview-panel-head-end">
        ${meta ? html`<span class="muted overview-panel-meta">${meta}</span>` : ""}
        ${actions || ""}
      </span>
    </summary>
  `;
}

function findingGrainSelector(query) {
  const selected = selectedQueryValue(query, "finding_grain", "month");
  return html`
    <label class="finding-grain-control">
      <span class="sr-only">Finding time grain</span>
      <select data-finding-grain aria-label="Finding time grain">
        ${["day", "week", "month"].map(
          (grain) => html`<option value="${grain}"${grain === selected ? " selected" : ""}>${grain[0].toUpperCase()}${grain.slice(1)}</option>`,
        )}
      </select>
    </label>
  `;
}

function overviewEmpty({ message, actionHref, actionLabel, attr, reason }) {
  return html`
    <div class="overview-empty"${attr ? raw(` ${attr}`) : ""}>
      ${reason ? html`<p class="overview-empty-status">${reason}</p>` : ""}
      ${emptyState(message)}
      ${
        actionHref
          ? html`<p class="overview-empty-action"><a class="btn-secondary" href="${actionHref}">${actionLabel || "Clear filters"}</a></p>`
          : ""
      }
    </div>
  `;
}

function queryHasDimensionFilters(query) {
  if (!query || !query.get) return false;
  return ["channel", "campaign_id", "filter_logic_1", "filter_logic_1_group"].some((key) => query.get(key));
}

function contextChips(rows, attr) {
  const items = rows.filter((row) => row && row[1] != null && row[1] !== "");
  if (!items.length) return "";
  return html`<dl class="finding-context context-chips"${attr ? raw(` ${attr}`) : ""}>${items.map(
    ([term, value]) => html`<div><dt>${term}</dt><dd>${value}</dd></div>`,
  )}</dl>`;
}

function appliedPeriodLabel(applied, period) {
  const state = applied || {};
  const window = period || {};
  if (state.period === "all_history") return "All published history";
  return (
    window.month_label ||
    state.month_start ||
    `${state.day_from || window.day_min || ""} – ${state.day_to || window.day_max || ""}`
  );
}

function appliedFilterBits(applied) {
  const state = applied || {};
  const bits = [];
  const campaigns = (state.campaign_ids || []).length;
  const channels = (state.channels || []).length;
  if (campaigns) bits.push(`${campaigns} campaign${campaigns === 1 ? "" : "s"}`);
  if (channels) bits.push(`${channels} channel${channels === 1 ? "" : "s"}`);
  if ((state.filter_logic_1 || []).length) bits.push("Filter Logic 1");
  if ((state.filter_logic_1_group || []).length) bits.push("Filter Logic 1 group");
  return bits;
}

export function overviewTrendSection({ query, trend, trendError }) {
  const selection = (trend && trend.selection) || {};
  const metric = selectedQueryValue(query, "trend_metric", selection.metric || "total_cost");
  const secondary = selectedQueryValue(query, "trend_secondary", selection.secondary || "");
  const grain = selectedQueryValue(query, "trend_grain", selection.grain || "day");
  const breakdown = selectedQueryValue(query, "trend_breakdown", selection.breakdown || "");
  const metricLabel = (TREND_METRIC_OPTIONS.find((item) => item[0] === metric) || [metric, metric])[1];
  const secondaryLabel = secondary
    ? (TREND_METRIC_OPTIONS.find((item) => item[0] === secondary) || [secondary, secondary])[1]
    : "None";
  const grainLabel = (TREND_GRAIN_OPTIONS.find((item) => item[0] === grain) || [grain, grain])[1];
  const breakdownLabel = (TREND_BREAKDOWN_OPTIONS.find((item) => item[0] === breakdown) || [breakdown, breakdown])[1];
  const trendApplied = (trend && trend.applied) || {};
  const trendPeriod = (trend && trend.period) || {};
  const trendComparison = (trend && trend.comparison) || {};
  const trendPeriodLabel = appliedPeriodLabel(trendApplied, trendPeriod);
  const trendCompareLabel =
    trend && trend.comparison_shown
      ? trendComparison.month_label || trendComparison.month_start || "Prior period"
      : "None";
  const unsupported = trendError && trendError.status === 422;
  const failed = trendError && trendError.status !== 422;
  let body;
  if (failed) {
    body = html`<div class="trend-chart-frame is-state">${errorBanner(trendError)}</div>`;
  } else if (unsupported) {
    body = html`<div class="trend-chart-frame is-state"><p class="banner warn" data-trend-unsupported="true">${trendError.message || "This metric combination is not supported."}</p></div>`;
  } else if (!trend || trend.empty) {
    body = html`<div class="trend-chart-frame is-state">${overviewEmpty({
      message: "No published history matches this trend.",
      reason: "No data",
      actionHref: queryHasDimensionFilters(query) ? "/client/overview" : "",
      actionLabel: "Clear filters",
    })}</div>`;
  } else {
    body = html`
      <div class="trend-chart-frame">
        ${renderTrendChart(trend)}
        <div class="trend-notes">
          ${
            trend.comparison_shown
              ? html`<p class="muted" data-trend-comparison="shown">Comparison is overlaid by period offset from the start of each window.</p>`
              : html`<p class="muted" data-trend-comparison="${trend.comparison_omitted_reason || "unavailable"}">${
                  trend.comparison_omitted_reason === "comparison_not_shown_with_breakdown"
                    ? "Comparison is hidden while a breakdown is selected."
                    : trend.comparison_omitted_reason === "comparison_disabled"
                      ? "No comparison: comparison is turned off."
                      : "No comparison for this trend."
                }</p>`
          }
          ${trend.truncated ? html`<p class="muted" data-trend-truncated="true">${trend.truncated_message || "Some breakdown series were grouped into Other."}</p>` : ""}
        </div>
      </div>
    `;
  }
  return html`
    <details class="overview-panel overview-trend" data-overview-trend="true" data-overview-panel="trend" open>
      ${overviewPanelHead({
        title: "Trends",
        description: "Track how the selected metric changes over time.",
        meta: `${metricLabel} · ${grainLabel}${breakdown ? ` · ${breakdownLabel}` : ""}`,
      })}
      <div class="overview-panel-toolbar">
        ${askAboutButton({ source: "trend", metric, question: "Summarize this trend." })}
        ${generateTrendControl()}
        <a class="btn-secondary" href="${overviewHref(withExplorerFromTrend(query || new URLSearchParams()))}" data-trend-explorer="true">Open in explorer</a>
      </div>
      <form class="overview-trend-form overview-controls overview-controls-inline" data-overview-trend-form="true">
        <div class="overview-control-groups">
          <div class="overview-control-group">
            <p class="overview-control-label">Series</p>
            <div class="overview-trend-grid">
              <label>
                Primary metric
                <select name="trend_metric" data-overview-trend-autosubmit="true">
                  ${TREND_METRIC_OPTIONS.map(
                    ([value, label]) => html`<option value="${value}" ${value === metric ? raw(" selected") : ""}>${label}</option>`,
                  )}
                </select>
              </label>
              <label>
                Secondary metric
                <select name="trend_secondary" data-overview-trend-autosubmit="true" ${breakdown ? raw(" disabled") : ""}>
                  <option value="">None</option>
                  ${TREND_METRIC_OPTIONS.map(
                    ([value, label]) => html`<option value="${value}" ${value === secondary ? raw(" selected") : ""} ${value === metric ? raw(" disabled") : ""}>${label}</option>`,
                  )}
                </select>
              </label>
            </div>
          </div>
          <div class="overview-control-group">
            <p class="overview-control-label">Time and breakdown</p>
            <div class="overview-trend-grid">
              <label>
                Time grain
                <select name="trend_grain" data-overview-trend-autosubmit="true">
                  ${TREND_GRAIN_OPTIONS.map(
                    ([value, label]) => html`<option value="${value}" ${value === grain ? raw(" selected") : ""}>${label}</option>`,
                  )}
                </select>
              </label>
              <label>
                Breakdown
                <select name="trend_breakdown" data-overview-trend-autosubmit="true" ${secondary ? raw(" disabled") : ""}>
                  ${TREND_BREAKDOWN_OPTIONS.map(
                    ([value, label]) => html`<option value="${value}" ${value === breakdown ? raw(" selected") : ""}>${label}</option>`,
                  )}
                </select>
              </label>
            </div>
          </div>
        </div>
        <p class="overview-filter-actions">
          <button type="submit" class="btn-secondary">Update trend</button>
        </p>
      </form>
      ${contextChips(
        [
          ["Metric", metricLabel],
          ["Secondary", secondary ? secondaryLabel : ""],
          ["Time grain", grainLabel],
          ["Breakdown", breakdown ? breakdownLabel : ""],
          ["Period", trendPeriodLabel],
          ["Comparison", trendCompareLabel],
        ],
        'data-trend-title="true"',
      )}
      ${body}
    </details>
  `;
}

const DRILL_DIM_LABELS = {
  campaign_id: "Campaign",
  channel: "Channel",
  filter_logic_1: "Filter Logic 1",
  filter_logic_1_group: "Filter Logic 1 group",
  day: "Day",
};

const EXPLORER_DIM_OPTIONS = [
  ["campaign_id", "Campaign"],
  ["channel", "Channel"],
  ["filter_logic_1", "Filter Logic 1"],
  ["filter_logic_1_group", "Filter Logic 1 group"],
  ["day", "Day"],
];

const EXPLORER_MODE_OPTIONS = [
  ["ranking", "Ranking"],
  ["top", "Top"],
  ["bottom", "Bottom"],
  ["movers", "Movers"],
];

const EXPLORER_SORT_LABELS = {
  value: "Value",
  delta: "Delta",
  contribution: "Share",
};

const EXPLORER_DIR_LABELS = {
  desc: "High to low",
  asc: "Low to high",
};

const EXPLORER_MOVER_LABELS = {
  up: "Largest increases",
  down: "Largest decreases",
};

function explorerSortState(sort, direction, column) {
  if (sort !== column) return "none";
  return direction === "asc" ? "ascending" : "descending";
}

function explorerSortHead({ query, sort, direction, column, label, metricKey }) {
  const state = explorerSortState(sort, direction, column);
  const active = state !== "none";
  return html`<th scope="col" class="num" aria-sort="${state}"${active ? raw(' data-explorer-sort-active="true"') : ""}${metricKey ? raw(` data-explorer-metric-col="${metricKey}"`) : ""}>
    <a class="action-link explorer-sort" href="${explorerSortHref(query, column)}" data-explorer-sort="${column}">
      ${label}<span class="explorer-sort-caret" aria-hidden="true">${active ? (state === "ascending" ? "▲" : "▼") : "↕"}</span>
      <span class="sr-only">${active ? `Sorted ${state}. Activate to reverse.` : "Activate to sort by this column."}</span>
    </a>
  </th>`;
}

function explorerSortHref(query, sort) {
  return overviewHref(withExplorerSort(query || new URLSearchParams(), sort));
}

function explorerMetricLookup(row, key) {
  const list = Array.isArray(row && row.metrics) ? row.metrics : [];
  return list.find((item) => item && item.key === key) || null;
}

function explorerMetricColumns(explorer) {
  const catalog = Array.isArray(explorer && explorer.metrics) ? explorer.metrics : [];
  if (catalog.length) return catalog;
  return TREND_METRIC_OPTIONS.map(([key, label]) => ({
    key,
    label,
    kind: key === "overall_roas" ? "roas" : key === "delivery_rate" || key === "ctr_del_to_clicks" ? "rate" : key === "delivered" || key === "unique_clicks" || key === "unique_conversions" ? "count" : "money",
  }));
}

function explorerMetricHead({ query, sort, direction, spec, rankingKey }) {
  if (spec.key === rankingKey) {
    return explorerSortHead({ query, sort, direction, column: "value", label: spec.label, metricKey: spec.key });
  }
  return html`<th scope="col" class="num" data-explorer-metric-col="${spec.key}">${spec.label}</th>`;
}

function explorerMetricCell(row, spec, rankingKey, comparisonAvailable) {
  const cell = explorerMetricLookup(row, spec.key);
  const value = cell ? cell.value : spec.key === rankingKey ? row.value : null;
  const delta = cell ? cell.delta : spec.key === rankingKey ? row.delta : null;
  const deltaPct = cell ? cell.delta_pct : spec.key === rankingKey ? row.delta_pct : null;
  const ranked = spec.key === rankingKey;
  return html`<td class="num explorer-metric${ranked ? " is-rank-metric" : ""}" data-explorer-metric="${spec.key}">
    <span class="explorer-metric-value">${formatKpiValue(spec.kind, value)}</span>
    ${
      comparisonAvailable
        ? html`<span class="explorer-metric-delta">${formatDelta(spec.kind, delta, deltaPct) || "n/a"}</span>`
        : ""
    }
  </td>`;
}

export function overviewExplorerSection({ query, data, explorer, explorerError }) {
  const selection = (explorer && explorer.selection) || {};
  const metricKey = selectedQueryValue(query, "ex_metric", selection.metric || "total_cost");
  const dimension = selectedQueryValue(query, "ex_dimension", selection.dimension || "campaign_id");
  const secondary = selectedQueryValue(query, "ex_secondary", selection.secondary || "");
  const mode = selectedQueryValue(query, "ex_mode", selection.mode || "ranking");
  const mover = selectedQueryValue(query, "ex_mover", selection.mover || "up");
  const modeDirection = mode === "bottom" || (mode === "movers" && mover === "down") ? "asc" : "desc";
  const direction = mode === "ranking"
    ? selectedQueryValue(query, "ex_dir", selection.direction || "desc")
    : modeDirection;
  const sort = selectedQueryValue(query, "ex_sort", selection.sort || (mode === "movers" ? "delta" : "value"));
  const limit = selectedQueryValue(query, "ex_limit", String(selection.limit || (mode === "ranking" ? "25" : "10")));
  const minValue = selectedQueryValue(query, "ex_min", selection.min_value || "");
  const minContrib = selectedQueryValue(query, "ex_min_contrib", selection.min_contribution || "");
  const metricInfo = (explorer && explorer.metric) || {};
  const metricLabel = metricInfo.label || (TREND_METRIC_OPTIONS.find((item) => item[0] === metricKey) || [metricKey, metricKey])[1];
  const dimensionLabel = DRILL_DIM_LABELS[dimension] || dimension;
  const secondaryLabel = secondary ? DRILL_DIM_LABELS[secondary] || secondary : "None";
  const applied = (data && data.applied) || (explorer && explorer.applied) || {};
  const comparison = (explorer && explorer.comparison) || (data && data.comparison) || {};
  const period = (data && data.period) || (explorer && explorer.period) || {};
  const periodLabel = appliedPeriodLabel(applied, period);
  const filterBits = appliedFilterBits(applied);
  const modeLabel = (EXPLORER_MODE_OPTIONS.find((item) => item[0] === mode) || [mode, mode])[1];
  const sortLabel = EXPLORER_SORT_LABELS[sort] || EXPLORER_SORT_LABELS.value;
  const directionLabel = EXPLORER_DIR_LABELS[direction] || EXPLORER_DIR_LABELS.desc;
  const moverLabel = EXPLORER_MOVER_LABELS[mover] || EXPLORER_MOVER_LABELS.up;
  const unsupported = explorerError && explorerError.status === 422;
  const failed = explorerError && explorerError.status !== 422;
  const showContribution = Boolean(explorer && explorer.selection && explorer.selection.contribution_supported);
  let body;
  if (failed) {
    body = errorBanner(explorerError);
  } else if (unsupported) {
    body = html`<p class="banner warn" data-explorer-unsupported="true">${explorerError.message || "This explorer combination is not supported."}</p>`;
  } else if (!explorer || explorer.empty) {
    body = overviewEmpty({
      message: "No published history matches this explorer ranking.",
      reason: "No data",
      actionHref: queryHasDimensionFilters(query) ? "/client/overview" : "",
      actionLabel: "Clear filters",
    });
  } else {
    const rows = Array.isArray(explorer.rows) ? explorer.rows : [];
    const anyDrillable = rows.some((row) => row.drillable && row.drill_dimension);
    const metricColumns = explorerMetricColumns(explorer);
    body = html`
      <div class="explorer-notes">
        ${anyDrillable ? html`<p class="muted" data-explorer-drill-hint="true">Highlighted rows open the detail view for that dimension value.</p>` : ""}
        ${
          comparison.available
            ? html`<p class="muted" data-explorer-comparison="shown">Comparison is the D2 prior window, matched by dimension value. Missing comparison values are n/a, not zero.</p>`
            : html`<p class="muted" data-explorer-comparison="${comparison.reason || "unavailable"}">No comparison for this ranking.</p>`
        }
        ${explorer.truncated ? html`<p class="muted" data-explorer-truncated="true">${explorer.truncated_message}</p>` : ""}
      </div>
      <div class="table-wrap explorer-table-wrap" data-explorer-table-wrap="true">
        <table class="data-table explorer-table" data-explorer-table="true">
          <thead>
            <tr>
              <th scope="col" class="num explorer-sticky-rank">Rank</th>
              <th scope="col" class="explorer-sticky-name">${secondary ? `${dimensionLabel} → ${secondaryLabel}` : dimensionLabel}</th>
              ${metricColumns.map((spec) => explorerMetricHead({ query, sort, direction, spec, rankingKey: metricKey }))}
              <th scope="col" class="num">Comparison</th>
              ${explorerSortHead({ query, sort, direction, column: "delta", label: "Delta" })}
              ${showContribution ? explorerSortHead({ query, sort, direction, column: "contribution", label: "Share" }) : ""}
            </tr>
          </thead>
          <tbody>
            ${rows.map((row) => {
              const href =
                row.drillable && row.drill_dimension
                  ? overviewHref(
                      withDrill(query, {
                        origin: "kpi",
                        metric: metricKey,
                        dimension: row.drill_dimension,
                        parents: row.drill_parents || [],
                      }),
                    )
                  : "";
              const label = secondary
                ? `${row.parent_label || row.parent_key || "(blank)"} → ${row.label || "(blank)"}`
                : row.label || "(blank)";
              const share = contributionSharePct(row.contribution_pct);
              const contribution =
                row.contribution_pct == null || row.contribution_pct === ""
                  ? "n/a"
                  : `${(Number(row.contribution_pct) * 100).toLocaleString("en-US", { maximumFractionDigits: 1 })}%`;
              return html`
                <tr class="${(query && query.get && query.get("ex_row") === row.key) ? "is-selected" : ""} ${href ? "is-drillable" : ""}" data-explorer-row="${row.key}" data-drillable="${row.drillable ? "true" : "false"}" data-explorer-row-selected="${query && query.get && query.get("ex_row") === row.key ? "true" : "false"}">
                  <td class="num explorer-sticky-rank">${row.rank}</td>
                  <td class="explorer-name explorer-sticky-name">
                    ${
                      href
                        ? html`<a href="${href}" data-explorer-drill="${row.key}">${label}<span class="explorer-affordance" aria-hidden="true">→</span></a>`
                        : label
                    }
                    ${share == null ? "" : html`<span class="drill-bar-track explorer-bar"><span class="drill-bar-fill" style="width: ${share}%;"></span></span>`}
                  </td>
                  ${metricColumns.map((spec) => explorerMetricCell(row, spec, metricKey, comparison.available))}
                  <td class="num">${comparison.available ? formatKpiValue(metricInfo.kind, row.prior_value) : "n/a"}</td>
                  <td class="num">${comparison.available ? formatDelta(metricInfo.kind, row.delta, row.delta_pct) : "n/a"}</td>
                  ${showContribution ? html`<td class="num">${contribution}</td>` : ""}
                </tr>
              `;
            })}
          </tbody>
        </table>
      </div>
    `;
  }
  return html`
    <details class="overview-panel overview-trend overview-explorer" data-overview-explorer="true" data-overview-panel="explorer" open>
      ${overviewPanelHead({
        title: "Performance Explorer",
        description: "Rank dimensions to identify what drives performance.",
        meta: `${metricLabel} · ${dimensionLabel} · ${modeLabel}`,
        actions: html`<button type="button" class="btn-secondary" data-overview-explorer-export="csv">Export CSV</button>`,
      })}
      <div class="overview-panel-toolbar">
        ${askAboutButton({ source: "explorer", metric: metricKey, dimension, question: "Explain this ranking." })}
      </div>
      <form class="overview-trend-form overview-controls" data-overview-explorer-form="true">
        <input type="hidden" name="ex_sort" value="${sort}" />
        <input type="hidden" name="ex_dir" value="${direction}" />
        <div class="overview-control-groups">
          <div class="overview-control-group">
            <p class="overview-control-label">Scope</p>
            <div class="overview-trend-grid overview-explorer-grid">
              <label>
                Metric
                <select name="ex_metric" data-overview-explorer-autosubmit="true">
                  ${TREND_METRIC_OPTIONS.map(
                    ([value, label]) => html`<option value="${value}" ${value === metricKey ? raw(" selected") : ""}>${label}</option>`,
                  )}
                </select>
              </label>
              <label>
                Dimension
                <select name="ex_dimension" data-overview-explorer-autosubmit="true">
                  ${EXPLORER_DIM_OPTIONS.map(
                    ([value, label]) => html`<option value="${value}" ${value === dimension ? raw(" selected") : ""}>${label}</option>`,
                  )}
                </select>
              </label>
              <label>
                Secondary
                <select name="ex_secondary" data-overview-explorer-autosubmit="true" ${dimension === "day" ? raw(" disabled") : ""}>
                  <option value="">None</option>
                  ${EXPLORER_DIM_OPTIONS.filter(([value]) => value !== dimension).map(
                    ([value, label]) => html`<option value="${value}" ${value === secondary ? raw(" selected") : ""}>${label}</option>`,
                  )}
                </select>
              </label>
            </div>
          </div>
          <div class="overview-control-group">
            <p class="overview-control-label">Ranking</p>
            <div class="overview-trend-grid overview-explorer-grid">
              <label>
                Ranking mode
                <select name="ex_mode" data-overview-explorer-autosubmit="true">
                  ${EXPLORER_MODE_OPTIONS.map(
                    ([value, label]) => html`<option value="${value}" ${value === mode ? raw(" selected") : ""}>${label}</option>`,
                  )}
                </select>
              </label>
              <label>
                Movers
                <select name="ex_mover" data-overview-explorer-autosubmit="true" ${mode === "movers" ? "" : raw(" disabled")}>
                  <option value="up" ${mover === "up" ? raw(" selected") : ""}>Largest increases</option>
                  <option value="down" ${mover === "down" ? raw(" selected") : ""}>Largest decreases</option>
                </select>
              </label>
              <label>
                Limit
                <input name="ex_limit" type="number" min="1" max="50" value="${limit}" data-overview-explorer-autosubmit="true" />
              </label>
            </div>
          </div>
          <div class="overview-control-group">
            <p class="overview-control-label">Thresholds</p>
            <div class="overview-trend-grid overview-explorer-grid">
              <label>
                Minimum value
                <input name="ex_min" type="text" value="${minValue}" placeholder="optional" />
              </label>
              <label>
                Minimum contribution
                <input name="ex_min_contrib" type="text" value="${minContrib}" placeholder="additive only" ${showContribution || metricInfo.additive ? "" : raw(" disabled")} />
              </label>
            </div>
          </div>
        </div>
        <p class="overview-filter-actions">
          <button type="submit" class="btn-secondary">Update explorer</button>
        </p>
      </form>
      ${contextChips(
        [
          ["Metric", metricLabel],
          ["Dimension", secondary ? `${dimensionLabel} → ${secondaryLabel}` : dimensionLabel],
          ["Mode", mode === "movers" ? `${modeLabel} · ${moverLabel}` : modeLabel],
          ["Sort", `${sortLabel} · ${directionLabel}`],
          ["Period", periodLabel],
          ["Filters", filterBits.length ? filterBits.join(" · ") : "None"],
          ["Share", showContribution ? "Available" : "Not applicable"],
        ],
        'data-explorer-title="true"',
      )}
      ${body}
    </details>
  `;
}

const INSIGHT_CATEGORY_LABELS = {
  material_change: "Material change",
  dominant_driver: "Dominant driver",
  positive_signal: "Positive signal",
  negative_signal: "Negative signal",
  relationship: "Mix change",
};

function insightEmptyCopy(reason) {
  if (reason === "insufficient_comparison") {
    return "No comparison is available, so period-change insights are not generated.";
  }
  if (reason === "insufficient_history") {
    return "Not enough published history to compare periods.";
  }
  if (reason === "empty_period") {
    return "No published history matches this period.";
  }
  if (reason === "insufficient_denominator") {
    return "Denominator is too small to support a reliable insight.";
  }
  return "No material insights for this comparison. Ordinary movement below the documented thresholds is suppressed.";
}

const INSIGHT_EMPTY_STATUS = {
  insufficient_comparison: "No comparison",
  insufficient_history: "Not enough history",
  empty_period: "No data in period",
  insufficient_denominator: "Below reporting floor",
  no_material_insights: "Nothing material",
};

function findingRankLabel(rank) {
  const value = Number(rank);
  if (!Number.isFinite(value) || value < 1) return "Finding";
  return value === 1 ? "Top finding" : `Finding ${value}`;
}

function findingMetricLabel(item) {
  const label = String((item && item.metric_label) || "").trim();
  if (label) return label;
  const metric = String((item && item.metric) || "");
  const known = TREND_METRIC_OPTIONS.find((option) => option[0] === metric);
  return known ? known[1] : "";
}

function findingContribution(driver, suffix) {
  if (!driver || driver.contribution_pct == null || driver.contribution_pct === "") return "";
  const pct = Number(driver.contribution_pct) * 100;
  if (!Number.isFinite(pct)) return "";
  return ` · ${pct.toLocaleString("en-US", { maximumFractionDigits: 1 })}% ${suffix}`;
}

export function overviewInsightsSection({ query, data, insights, insightsError }) {
  const applied = (data && data.applied) || (insights && insights.applied) || {};
  const comparison = (insights && insights.comparison) || (data && data.comparison) || {};
  const period = (data && data.period) || (insights && insights.period) || {};
  const periodLabel =
    applied.period === "all_history"
      ? "All published history"
      : period.month_label || applied.month_start || `${applied.day_from || period.day_min || ""} – ${applied.day_to || period.day_max || ""}`;
  const compareLabel = comparison.available
    ? comparison.month_label || comparison.month_start || `${comparison.day_min || ""} – ${comparison.day_max || ""}`
    : "none";
  const unsupported = insightsError && insightsError.status === 422;
  const failed = insightsError && insightsError.status !== 422;
  let body;
  if (failed) {
    body = html`<div data-insights-error="true">${errorBanner(insightsError)}</div>`;
  } else if (unsupported) {
    body = html`<p class="banner warn" data-insights-unsupported="true">${insightsError.message || "This insight request is not supported."}</p>`;
  } else if (!insights || insights.empty) {
    const reason = (insights && insights.empty_reason) || "no_material_insights";
    body = html`<div data-insights-empty="${reason}">${overviewEmpty({
      message: insightEmptyCopy(reason),
      reason: INSIGHT_EMPTY_STATUS[reason] || "Nothing to report",
      actionHref: queryHasDimensionFilters(query) ? "/client/overview" : "",
      actionLabel: "Clear filters",
    })}</div>`;
  } else {
    const items = Array.isArray(insights.insights) ? insights.insights : [];
    body = html`
      <ol class="insight-grid finding-list" data-insights-list="true">
        ${items.map((item) => {
          const top = (item.drivers || [])[0];
          const href =
            top && top.drillable && top.drill_dimension
              ? overviewHref(
                  withDrill(query || new URLSearchParams(), {
                    origin: "kpi",
                    metric: item.metric,
                    dimension: top.drill_dimension,
                    parents: top.drill_parents || [],
                  }),
                )
              : "";
          const categoryClass = item.category === "negative_signal" ? "negative" : item.category === "positive_signal" ? "positive" : "";
          const selected = query && query.get && query.get("insight") === item.insight_id;
          const direction =
            item.category === "positive_signal"
              ? "up"
              : item.category === "negative_signal"
                ? "down"
                : Number(item.delta_pct || item.delta) > 0
                  ? "up"
                  : Number(item.delta_pct || item.delta) < 0
                    ? "down"
                    : "neutral";
          const metricLabel = findingMetricLabel(item);
          const lead = Number(item.rank) === 1;
          return html`
            <li class="finding-item">
              <article class="insight-card finding-card ${categoryClass} ${lead ? "is-lead" : ""} ${selected ? "is-selected" : ""}" data-insight-id="${item.insight_id}" data-insight-category="${item.category}" data-insight-rank="${item.rank}" data-insight-selected="${selected ? "true" : "false"}">
                <p class="insight-status finding-kicker is-${direction}" data-insight-kicker="true">
                  <span class="finding-rank">${findingRankLabel(item.rank)}</span>
                  <span class="finding-kind">${INSIGHT_CATEGORY_LABELS[item.category] || "Finding"}</span>
                </p>
                <h3>${item.headline}</h3>
                <dl class="finding-context" data-insight-context="true">
                  ${metricLabel ? html`<div><dt>Metric</dt><dd>${metricLabel}</dd></div>` : ""}
                  <div><dt>Period</dt><dd>${(item.period && item.period.month_label) || periodLabel}</dd></div>
                  <div><dt>Compared with</dt><dd>${(item.comparison && item.comparison.month_label) || compareLabel}</dd></div>
                </dl>
                ${
                  top
                    ? html`<p class="insight-driver"><span>Primary driver:</span> ${top.dimension_label} ${top.label}${findingContribution(top, "of change")}</p>`
                    : ""
                }
                <p class="insight-explanation">${item.explanation}</p>
                <dl class="insight-evidence is-secondary">
                  <div><dt>Current</dt><dd>${formatKpiValue(item.kind, item.current_value)}</dd></div>
                  <div><dt>Comparison</dt><dd>${formatKpiValue(item.kind, item.prior_value)}</dd></div>
                  <div><dt>Change</dt><dd>${formatDelta(item.kind, item.delta, item.delta_pct)}</dd></div>
                </dl>
                <details class="finding-rule" data-insight-rule="true">
                  <summary>Rule and thresholds</summary>
                  <p class="finding-rule-body mono">${item.threshold}</p>
                </details>
                <p class="insight-actions finding-actions">
                  ${href ? html`<a class="btn-secondary finding-drill" href="${href}" data-insight-drill="${top.key}">View details</a>` : ""}
                  <a class="action-link" href="${overviewHref(withFocus(query || new URLSearchParams(), { insight: item.insight_id, kpi: item.metric }))}" data-insight-select="${item.insight_id}">Keep this insight in context</a>
                  ${askAboutButton({ source: "insight", metric: item.metric, insightId: item.insight_id, question: "Explain this insight.", inline: true })}
                </p>
              </article>
            </li>
          `;
        })}
      </ol>
    `;
  }
  const insightCount = insights && !insights.empty && Array.isArray(insights.insights) ? insights.insights.length : 0;
  const insightSummary = insightCount
    ? `${insightCount} finding${insightCount === 1 ? "" : "s"}`
    : insightsError
      ? "Unavailable"
      : INSIGHT_EMPTY_STATUS[(insights && insights.empty_reason) || "no_material_insights"] || "Nothing to report";
  return html`
    <details class="overview-panel overview-trend overview-insights" data-overview-insights="true" data-overview-panel="insights" open>
      ${overviewPanelHead({
        title: "Key Insights",
        description: "Evidence-backed changes and drivers.",
        meta: insightSummary,
        actions: findingGrainSelector(query),
      })}
      ${body}
    </details>
  `;
}

const ANOMALY_KIND_LABELS = {
  spike: "Spike",
  drop: "Drop",
  rolling_deviation: "Rolling deviation",
};

const ANOMALY_SEVERITY_LABELS = {
  high: "High severity",
  medium: "Medium severity",
  low: "Low severity",
};

const ANOMALY_DIRECTION_LABELS = {
  spike: "Above baseline",
  drop: "Below baseline",
};

const ANOMALY_EMPTY_STATUS = {
  insufficient_history: "Not enough history",
  period_not_month: "Month view required",
  empty_period: "No data in period",
  no_published_history: "No published history",
  no_anomalies: "Nothing unusual",
};

function anomalyEmptyCopy(reason) {
  if (reason === "insufficient_history") {
    return "Not enough published months to judge whether this period is unusual. Anomalies need three prior published months under the same filters.";
  }
  if (reason === "period_not_month") {
    return "Anomaly diagnostics are defined on a published month, not a custom range or all-history window.";
  }
  if (reason === "empty_period") {
    return "No published history matches this period.";
  }
  if (reason === "no_published_history") {
    return "No published history is available for this company.";
  }
  return "No unusual movement versus the prior-month median. Ordinary period changes are shown in Insights, not here.";
}

export function overviewAnomaliesSection({ query, data, anomalies, anomaliesError }) {
  const applied = (data && data.applied) || (anomalies && anomalies.applied) || {};
  const period = (data && data.period) || (anomalies && anomalies.period) || {};
  const periodLabel = period.month_label || applied.month_start || "this period";
  const baseline = (anomalies && anomalies.baseline) || {};
  const baselineMonths = Array.isArray(baseline.month_starts) ? baseline.month_starts.join(", ") : "";
  const unsupported = anomaliesError && anomaliesError.status === 422;
  const failed = anomaliesError && anomaliesError.status !== 422;
  let body;
  if (failed) {
    body = html`<div data-anomalies-error="true">${errorBanner(anomaliesError)}</div>`;
  } else if (unsupported) {
    body = html`<p class="banner warn" data-anomalies-unsupported="true">${anomaliesError.message || "This anomaly request is not supported."}</p>`;
  } else if (!anomalies || anomalies.empty) {
    const reason = (anomalies && anomalies.empty_reason) || "no_anomalies";
    const status = ANOMALY_EMPTY_STATUS[reason] || "Unavailable";
    body = html`
      <div data-anomalies-empty="${reason}">
        ${overviewEmpty({
          message: anomalyEmptyCopy(reason),
          reason: status,
          actionHref: queryHasDimensionFilters(query) ? "/client/overview" : "",
          actionLabel: "Clear filters",
        })}
      </div>
    `;
  } else {
    const items = Array.isArray(anomalies.anomalies) ? anomalies.anomalies : [];
    body = html`
      <ol class="insight-grid finding-list" data-anomalies-list="true">
        ${items.map((item) => {
          const top = (item.drivers || [])[0];
          const href =
            top && top.drillable && top.drill_dimension
              ? overviewHref(
                  withDrill(query || new URLSearchParams(), {
                    origin: "kpi",
                    metric: item.metric,
                    dimension: top.drill_dimension,
                    parents: top.drill_parents || [],
                  }),
                )
              : "";
          const selected = query && query.get && query.get("anomaly") === item.anomaly_id;
          const metricLabel = findingMetricLabel(item);
          const direction = item.direction === "drop" ? "drop" : "spike";
          const affected = top
            ? `${top.dimension_label} ${top.label}${findingContribution(top, "of change vs median")}`
            : item.affected_label || "";
          return html`
            <li class="finding-item">
              <article class="insight-card finding-card anomaly-card severity-${item.severity} ${selected ? "is-selected" : ""}" data-anomaly-id="${item.anomaly_id}" data-anomaly-kind="${item.kind}" data-anomaly-direction="${direction}" data-anomaly-severity="${item.severity}" data-anomaly-selected="${selected ? "true" : "false"}">
                <p class="anomaly-status finding-kicker severity-${item.severity} is-${direction}" data-anomaly-kicker="true">
                  <span class="anomaly-severity">${ANOMALY_SEVERITY_LABELS[item.severity] || "Needs attention"}</span>
                  <span class="finding-kind">${ANOMALY_KIND_LABELS[item.kind] || "Unusual movement"} · ${ANOMALY_DIRECTION_LABELS[direction]}</span>
                </p>
                <h3>${item.headline}</h3>
                <dl class="finding-context" data-anomaly-context="true">
                  ${metricLabel ? html`<div><dt>Metric</dt><dd>${metricLabel}</dd></div>` : ""}
                  <div><dt>Period</dt><dd>${(item.period && item.period.month_label) || periodLabel}</dd></div>
                  ${affected ? html`<div><dt>Affected</dt><dd>${affected}</dd></div>` : ""}
                </dl>
                <p class="insight-explanation">${item.explanation}</p>
                <dl class="insight-evidence is-secondary">
                  <div><dt>Observed</dt><dd>${formatKpiValue(item.value_kind, item.current_value)}</dd></div>
                  <div><dt>Baseline median</dt><dd>${formatKpiValue(item.value_kind, item.baseline_value)}</dd></div>
                  <div><dt>Difference</dt><dd>${formatDelta(item.value_kind, item.delta, item.delta_pct)}</dd></div>
                </dl>
                <details class="finding-rule" data-anomaly-rule="true">
                  <summary>Rule and thresholds</summary>
                  <p class="finding-rule-body mono">${item.threshold}</p>
                </details>
                <p class="insight-actions finding-actions">
                  ${href ? html`<a class="btn-secondary finding-drill" href="${href}" data-anomaly-drill="${top.key}">View details</a>` : ""}
                  <a class="action-link" href="${overviewHref(withFocus(query || new URLSearchParams(), { anomaly: item.anomaly_id, kpi: item.metric }))}" data-anomaly-select="${item.anomaly_id}">Keep this anomaly in context</a>
                  ${askAboutButton({ source: "anomaly", metric: item.metric, anomalyId: item.anomaly_id, question: "Explain this anomaly.", inline: true })}
                </p>
              </article>
            </li>
          `;
        })}
      </ol>
    `;
  }
  const anomalyCount = anomalies && !anomalies.empty && Array.isArray(anomalies.anomalies) ? anomalies.anomalies.length : 0;
  const anomalySummary = anomalyCount
    ? `${anomalyCount} anomal${anomalyCount === 1 ? "y" : "ies"}`
    : anomaliesError
      ? "Unavailable"
      : ANOMALY_EMPTY_STATUS[(anomalies && anomalies.empty_reason) || "no_anomalies"] || "Unavailable";
  return html`
    <details class="overview-panel overview-trend overview-anomalies" data-overview-anomalies="true" data-overview-panel="anomalies" open>
      ${overviewPanelHead({
        title: "Anomalies",
        description: "Unusual behavior against historical baseline.",
        meta: anomalySummary,
        actions: findingGrainSelector(query),
      })}
      ${baselineMonths ? html`<p class="muted overview-chart-caption">Baseline: ${baselineMonths}.</p>` : ""}
      ${body}
    </details>
  `;
}

function generateTrendControl() {
  return html`
    <div class="generate-trend" data-generate-trend="true">
      <button
        type="button"
        class="generate-trend-open"
        data-generate-trend-open="true"
        aria-haspopup="dialog"
        aria-expanded="false"
        aria-controls="generate-trend-popover"
      >
        ${icon("spark")}<span>Generate Trend</span>
      </button>
      <div
        class="generate-trend-popover"
        id="generate-trend-popover"
        data-generate-trend-popover="true"
        hidden
        role="dialog"
        aria-labelledby="generate-trend-title"
        aria-describedby="generate-trend-hint"
      >
        <form class="generate-trend-form" data-generate-trend-form="true">
          <input type="hidden" name="source" value="overview" />
          <p class="generate-trend-title" id="generate-trend-title">Generate Trend</p>
          <p class="muted generate-trend-hint" id="generate-trend-hint">
            Describe a published metric, grain, or breakdown. Example: Show revenue and ROAS by day.
          </p>
          <label>
            Trend request
            <textarea
              name="question"
              rows="2"
              maxlength="500"
              required
              placeholder="Show revenue and ROAS by day"
              data-generate-trend-input="true"
            ></textarea>
          </label>
          <p class="overview-filter-actions">
            <button type="button" data-generate-trend-submit="true">Generate</button>
            <button type="button" class="btn-secondary" data-generate-trend-cancel="true">Cancel</button>
          </p>
          <div data-generate-trend-status="true" aria-live="polite"></div>
        </form>
      </div>
    </div>
  `;
}

function askAboutButton({ source, metric, dimension, insightId, anomalyId, question, inline }) {
  const button = html`<button type="button" class="btn-secondary" data-ask="true" data-ask-source="${source || "overview"}" data-ask-metric="${metric || ""}" data-ask-dimension="${dimension || ""}" data-ask-insight="${insightId || ""}" data-ask-anomaly="${anomalyId || ""}" data-ask-question="${question || ""}">Ask about this</button>`;
  return inline ? button : html`<p class="insight-actions">${button}</p>`;
}

const ASK_STATUS_LABELS = {
  answered: "Answer",
  unsupported: "Not supported",
  refused: "Request declined",
  insufficient: "Not enough evidence",
};

function askStatusLabel(payload) {
  const status = payload.status || "answered";
  if (status === "answered") return ASK_STATUS_LABELS.answered;
  if (status === "refused") return ASK_STATUS_LABELS.refused;
  const reason = payload.empty_reason || "";
  if (
    reason === "insufficient_evidence" ||
    reason === "empty_period" ||
    reason === "missing_comparison" ||
    reason === "insufficient_history" ||
    reason === "period_not_month" ||
    reason === "no_anomalies"
  ) {
    return ASK_STATUS_LABELS.insufficient;
  }
  return ASK_STATUS_LABELS.unsupported;
}

const ASK_RAW_LABELS = new Map(
  [...TREND_METRIC_OPTIONS, ...Object.entries(DRILL_DIM_LABELS)].filter(([key]) => key.includes("_")),
);

const ASK_RAW_TOKEN_RE = new RegExp(
  `\\b(${[...ASK_RAW_LABELS.keys()].sort((a, b) => b.length - a.length).join("|")})\\b`,
  "g",
);

const ASK_FILTER_KEYS = [
  ["channels", "channel"],
  ["campaign_ids", "campaign_id"],
  ["filter_logic_1", "filter_logic_1"],
  ["filter_logic_1_group", "filter_logic_1_group"],
];

function askHumanText(value) {
  const text = String(value == null ? "" : value);
  if (!text) return "";
  return text.replace(ASK_RAW_TOKEN_RE, (token) => ASK_RAW_LABELS.get(token) || token);
}

function askHumanLabel(value) {
  const text = String(value == null ? "" : value).trim();
  if (!text) return "";
  return ASK_RAW_LABELS.get(text) || askHumanText(text);
}

function askContextRows(payload) {
  const evidence = (payload && payload.evidence) || {};
  const filters = evidence.filters || null;
  const period = evidence.period || {};
  const comparison = evidence.comparison || null;
  const metric = evidence.metric || {};
  const rows = [];
  const metricLabel = askHumanLabel(metric.label || metric.metric);
  if (metricLabel) rows.push(["Metric", metricLabel]);
  const dayRange =
    period.day_min || period.day_max ? `${period.day_min || ""} – ${period.day_max || ""}` : "";
  const periodLabel =
    filters && filters.period === "all_history"
      ? "All published history"
      : period.month_label || period.month_start || dayRange;
  if (periodLabel) rows.push(["Period", periodLabel]);
  if (comparison) {
    rows.push([
      "Comparison",
      comparison.available
        ? comparison.month_label || comparison.month_start || "Prior period"
        : "None",
    ]);
  }
  if (filters) {
    const bits = [];
    for (const [key, dimension] of ASK_FILTER_KEYS) {
      const values = Array.isArray(filters[key]) ? filters[key] : [];
      if (values.length) bits.push(`${DRILL_DIM_LABELS[dimension] || dimension} (${values.length})`);
    }
    rows.push(["Filters", bits.length ? bits.join(" · ") : "None"]);
  }
  return rows;
}

export function overviewAskContextLine({ session, data }) {
  const company = overviewCompanyLabel(session, data);
  const applied = (data && data.applied) || {};
  const period = (data && data.period) || {};
  const comparison = (data && data.comparison) || {};
  const periodLabel =
    applied.period === "all_history"
      ? "All published history"
      : period.month_label || applied.month_start || `${applied.day_from || period.day_min || ""} – ${applied.day_to || period.day_max || ""}`;
  const compareLabel = comparison.available
    ? comparison.month_label || comparison.month_start || "prior period"
    : "none";
  const filterBits = [];
  if (applied.channels && applied.channels.length) filterBits.push(`channel ${applied.channels.join(", ")}`);
  if (applied.campaign_ids && applied.campaign_ids.length) filterBits.push(`${applied.campaign_ids.length} campaign(s)`);
  if (applied.filter_logic_1 && applied.filter_logic_1.length) filterBits.push("Filter Logic 1");
  return html`Company: <strong>${company}</strong> · Period: <strong>${periodLabel}</strong> · Comparison: <strong data-ask-comparison="${comparison.available ? "available" : comparison.reason || "none"}">${compareLabel}</strong>${filterBits.length ? ` · ${filterBits.join(" · ")}` : ""}`;
}

function askSourceFromQuery(query) {
  if (parseDrillQuery(query)) return "drill";
  if (query && query.get("insight")) return "insight";
  if (query && query.get("anomaly")) return "anomaly";
  if (query && query.get("kpi")) return "kpi";
  return "overview";
}

function askFocusHiddenInputs(query) {
  const parsed = parseDrillQuery(query);
  return html`
    <input type="hidden" name="source" value="${askSourceFromQuery(query)}" />
    <input type="hidden" name="metric" value="${(query && (query.get("kpi") || query.get("trend_metric") || query.get("ex_metric"))) || ""}" />
    <input type="hidden" name="dimension" value="${(query && (query.get("ex_dimension") || query.get("drill_dimension"))) || ""}" />
    <input type="hidden" name="insight_id" value="${(query && query.get("insight")) || ""}" />
    <input type="hidden" name="anomaly_id" value="${(query && query.get("anomaly")) || ""}" />
    <input type="hidden" name="trend_metric" value="${(query && query.get("trend_metric")) || ""}" />
    <input type="hidden" name="trend_secondary" value="${(query && query.get("trend_secondary")) || ""}" />
    <input type="hidden" name="trend_grain" value="${(query && query.get("trend_grain")) || ""}" />
    <input type="hidden" name="trend_breakdown" value="${(query && query.get("trend_breakdown")) || ""}" />
    <input type="hidden" name="explorer_metric" value="${(query && query.get("ex_metric")) || ""}" />
    <input type="hidden" name="explorer_dimension" value="${(query && query.get("ex_dimension")) || ""}" />
    <input type="hidden" name="explorer_mode" value="${(query && query.get("ex_mode")) || ""}" />
    <input type="hidden" name="drill_origin" value="${parsed ? parsed.origin : ""}" />
    <input type="hidden" name="drill_metric" value="${parsed ? parsed.metric : ""}" />
    <input type="hidden" name="drill_dimension" value="${parsed ? parsed.dimension : ""}" />
    <input type="hidden" name="drill_parents" value="${parsed && parsed.parents ? parsed.parents.join("|") : ""}" />
  `;
}

const ASK_AI_EXAMPLES = [
  "Why did revenue change?",
  "Explain the current ROAS performance.",
  "What is driving revenue?",
  "Explain this anomaly.",
];

function askAiControl(query) {
  return html`
    <div class="ask-ai" data-ask-ai="true">
      <button
        type="button"
        class="ask-ai-open"
        data-ask-ai-open="true"
        aria-label="Ask DFIP"
        aria-haspopup="dialog"
        aria-expanded="false"
        aria-controls="ask-ai-popover"
      >
        ${icon("ask")}<span>Ask DFIP</span>
      </button>
      <div
        class="ask-ai-popover"
        id="ask-ai-popover"
        data-ask-ai-popover="true"
        hidden
        role="dialog"
        aria-labelledby="ask-ai-title"
        aria-describedby="ask-ai-hint"
      >
        <form class="ask-ai-form" data-ask-ai-form="true">
          ${askFocusHiddenInputs(query)}
          <p class="ask-ai-title" id="ask-ai-title">Ask DFIP</p>
          <p class="muted ask-ai-hint" id="ask-ai-hint">
            Ask about published-history performance for the current company, period, and filters.
          </p>
          <label>
            Question
            <textarea
              name="question"
              rows="3"
              maxlength="500"
              required
              placeholder="Ask about your performance..."
              aria-label="Ask about your performance"
              data-ask-ai-input="true"
            ></textarea>
          </label>
          <ul class="ask-ai-examples">
            ${ASK_AI_EXAMPLES.map(
              (item) => html`<li><button type="button" class="action-link" data-ask-ai-example="${item}">${item}</button></li>`,
            )}
          </ul>
          <p class="overview-filter-actions">
            <button type="button" data-ask-ai-submit="true">Ask</button>
            <button type="button" class="btn-secondary" data-ask-ai-cancel="true">Cancel</button>
          </p>
        </form>
        <div data-ask-ai-result="true" aria-live="polite"></div>
      </div>
    </div>
  `;
}

export function overviewAskSection({ session, data, query }) {
  return html`
    <details class="overview-panel tool-card overview-ask" data-overview-ask="true" data-overview-panel="ask" open>
      <summary class="tool-card-head">
        ${icon("ask")}
        <span>
          <span class="overview-kicker">Ask a question</span>
          <span class="overview-lede">Explain results for the current company, period, and filters.</span>
        </span>
      </summary>
      <p class="muted" data-ask-context="true">${overviewAskContextLine({ session, data })}</p>
      <form class="overview-ask-form" data-overview-ask-form="true">
        ${askFocusHiddenInputs(query)}
        <label>
          Question
          <textarea name="question" rows="3" maxlength="500" required placeholder="Why did revenue fall?" data-ask-question-input="true"></textarea>
        </label>
        <p class="overview-filter-actions">
          <button type="submit" data-ask-submit="true">Ask</button>
        </p>
      </form>
      <div data-ask-result="true">${overviewAskEmptyResult()}</div>
    </details>
  `;
}

export function overviewAskEmptyResult() {
  return emptyState("Ask a question about the current published-history context.");
}

export function generateTrendStatusView(payload, error) {
  if (error) {
    if (error.status === 422) {
      return html`<p class="banner warn" data-generate-trend-unsupported="true">${error.message || "This request is not supported."}</p>`;
    }
    return html`<div data-generate-trend-error="true">${errorBanner(error)}</div>`;
  }
  if (!payload) {
    return html`<p class="banner warn" data-generate-trend-unsupported="true">This request is not supported.</p>`;
  }
  const status = payload.status || "unsupported";
  const answer = askHumanText(payload.answer || "");
  if (status === "answered" && payload.intent !== "generate_trend") {
    return html`<p class="banner warn" data-generate-trend-unsupported="true">${
      answer || "That request is not a trend selection."
    }</p>`;
  }
  return html`<p class="banner warn" data-generate-trend-unsupported="true" data-generate-trend-status-code="${status}">${
    answer || "This request is not supported."
  }</p>`;
}

export function overviewAskResultView(payload, error) {
  if (error) {
    if (error.status === 422) {
      return html`<p class="banner warn" data-ask-unsupported="true">${error.message || "This question is not supported."}</p>`;
    }
    return html`<div data-ask-error="true">${errorBanner(error)}</div>`;
  }
  if (!payload) {
    return emptyState("Ask a question about the current published-history context.");
  }
  const status = payload.status || "answered";
  const evidence = payload.evidence || {};
  const metric = evidence.metric || {};
  const groups = Array.isArray(evidence.groups) && evidence.groups.length
    ? evidence.groups
    : [evidence.group_a, evidence.group_b].filter(Boolean);
  const compareGroups = payload.operation === "compare_dimensions" || groups.length >= 2;
  const metricLabel = askHumanLabel(metric.label || metric.metric);
  const statusLabel = askStatusLabel(payload);
  const answer = askHumanText(payload.answer || "");
  const contextRows = askContextRows(payload);
  return html`
    <article class="ask-result" data-ask-status="${status}" data-ask-empty-reason="${payload.empty_reason || ""}" data-ask-intent="${payload.intent || ""}" data-ask-operation="${payload.operation || ""}"${payload.question ? raw(' aria-labelledby="ask-result-title"') : ""}>
      <header class="ask-result-head">
        <p class="ask-result-meta">
          <span class="ask-status-pill ask-status-${status}" data-ask-status-label="true">${statusLabel}</span>
          <span class="muted">${status === "answered" ? "Grounded in published history · " : ""}${payload.llm_used ? "Model wording" : "Calculated wording"}</span>
        </p>
        ${payload.question ? html`<h3 class="ask-result-question" id="ask-result-title" data-ask-question-echo="true">${payload.question}</h3>` : ""}
        ${
          contextRows.length
            ? html`<dl class="ask-context" data-ask-result-context="true">${contextRows.map(([term, value]) => html`<div><dt>${term}</dt><dd>${value}</dd></div>`)}</dl>`
            : ""
        }
      </header>
      <p class="ask-result-answer" data-ask-answer="true">${answer}</p>
      ${
        Array.isArray(payload.caveats) && payload.caveats.length
          ? html`<p class="muted ask-result-note" data-ask-caveats="true">${askHumanText(payload.caveats.join(" "))}</p>`
          : ""
      }
      ${payload.next_action ? html`<p class="muted ask-result-note" data-ask-next="true">${askHumanText(payload.next_action)}</p>` : ""}
      ${
        answer
          ? html`<p class="ask-result-actions"><button type="button" class="btn-secondary" data-ask-copy="true" data-copy="${answer}" data-copy-toast="Copied answer.">Copy answer</button></p>`
          : ""
      }
      <details class="ask-evidence" data-ask-evidence-details="true">
        <summary>Evidence and calculation details</summary>
        <div class="ask-evidence-body">
          <dl class="insight-evidence ask-evidence-grid" data-ask-evidence="true">
            ${metricLabel ? html`<div><dt>Metric</dt><dd>${metricLabel}</dd></div>` : ""}
            ${
              compareGroups
                ? groups.map(
                    (row) => html`<div data-ask-group="${row.key || row.label || ""}"><dt>${row.label || row.key}</dt><dd>${row.current}${row.comparison != null && row.comparison !== "" ? ` · period comparison ${row.comparison}` : ""}</dd></div>`,
                  )
                : html`${metric.current != null && metric.current !== "" ? html`<div><dt>Current</dt><dd>${metric.current}</dd></div>` : ""}${metric.comparison != null && metric.comparison !== "" ? html`<div><dt>Comparison / baseline</dt><dd>${metric.comparison}</dd></div>` : ""}${metric.delta != null && metric.delta !== "" ? html`<div><dt>Difference</dt><dd>${metric.delta}${payload.evidence && metric.delta_pct ? ` · ${metric.delta_pct}` : ""}</dd></div>` : ""}`
            }
          </dl>
          <dl class="ask-technical" data-ask-technical="true">
            ${payload.operation ? html`<div><dt>Operation</dt><dd class="mono">${payload.operation}</dd></div>` : ""}
            ${payload.intent ? html`<div><dt>Intent</dt><dd class="mono">${payload.intent}</dd></div>` : ""}
            ${payload.empty_reason ? html`<div><dt>Reason</dt><dd class="mono">${payload.empty_reason}</dd></div>` : ""}
          </dl>
        </div>
      </details>
    </article>
  `;
}

function parentToken(dimension, value) {
  return `${dimension}:${value}`;
}

function overviewKpiForMetric(data, metricId) {
  const kpis = Array.isArray(data && data.kpis) ? data.kpis : [];
  return kpis.find((item) => item.id === metricId) || null;
}

function drillFilterSummary(applied) {
  const bits = [];
  if (applied.channels && applied.channels.length) bits.push(`Channel: ${applied.channels.join(", ")}`);
  if (applied.filter_logic_1 && applied.filter_logic_1.length) bits.push("Filter Logic 1");
  if (applied.filter_logic_1_group && applied.filter_logic_1_group.length) bits.push("Filter Logic 1 group");
  if (applied.campaign_ids && applied.campaign_ids.length) bits.push(`Campaign: ${applied.campaign_ids.length} selected`);
  return bits.length ? bits.join(" · ") : "None";
}

function contributionSharePct(value) {
  if (value == null || value === "") return null;
  const pct = Number(value) * 100;
  if (!Number.isFinite(pct)) return null;
  return Math.max(0, Math.min(100, pct));
}

const DRILL_CHART_MAX_BARS = 10;
const DRILL_OTHER_KEY = "__other__";
const DRILL_CHART_MIN_BAR_PCT = 1.5;

function drillChartNumber(value) {
  if (value == null || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function drillChartRows(rows) {
  const list = Array.isArray(rows) ? rows : [];
  const visible = list.slice(0, DRILL_CHART_MAX_BARS);
  const other = list.find((row) => row && row.key === DRILL_OTHER_KEY);
  if (other && !visible.includes(other)) visible.push(other);
  return visible;
}

function drillChartPeakIn(rows) {
  let peak = null;
  rows.forEach((row) => {
    const number = drillChartNumber(row && row.value);
    if (number == null) return;
    if (peak == null || Math.abs(number) > Math.abs(drillChartNumber(peak.value))) peak = row;
  });
  return peak;
}

function drillChartPeak(rows) {
  const ranked = rows.filter((row) => row && row.key !== DRILL_OTHER_KEY);
  return drillChartPeakIn(ranked) || drillChartPeakIn(rows);
}

function drillChartBarPct(value, peakValue) {
  const number = drillChartNumber(value);
  const scale = peakValue == null ? 0 : Math.abs(peakValue);
  if (number == null || !(scale > 0)) return null;
  const pct = (Math.abs(number) / scale) * 100;
  if (!Number.isFinite(pct)) return null;
  if (pct <= 0) return 0;
  return Math.max(DRILL_CHART_MIN_BAR_PCT, Math.min(100, pct));
}

function drillChartScopeLabel(rows, chartRows) {
  const total = rows.length;
  const inHead = rows.slice(0, DRILL_CHART_MAX_BARS).some((row) => row && row.key === DRILL_OTHER_KEY);
  const appendedOther = !inHead && chartRows.some((row) => row && row.key === DRILL_OTHER_KEY);
  const ranked = appendedOther ? chartRows.length - 1 : chartRows.length;
  if (ranked >= total) return total === 1 ? "1 group" : `All ${total} groups`;
  return appendedOther ? `Top ${ranked} plus Other · of ${total}` : `Top ${ranked} of ${total}`;
}

function drillChartRowText({ row, kind, dimLabel, metricLabel, share, drillLabel, capped }) {
  const label = row.label || "(blank)";
  const parts = [row.rank == null ? `${dimLabel} ${label}` : `Rank ${row.rank}, ${label}`];
  const number = drillChartNumber(row.value);
  parts.push(number == null ? `${metricLabel} not available` : `${metricLabel} ${formatKpiValue(kind, row.value)}`);
  if (share != null) parts.push(`${share.toLocaleString("en-US", { maximumFractionDigits: 1 })}% of total`);
  if (capped) parts.push("Bar is capped because the value exceeds the chart scale");
  if (drillLabel) parts.push(`Drill into ${drillLabel}`);
  return `${parts.join(". ")}.`;
}

function drillModeKey(mode) {
  return mode === "trend" || mode === "details" ? mode : "breakdown";
}

function drillModeTabs(mode) {
  const current = drillModeKey(mode);
  const tabs = [
    ["breakdown", "Breakdown"],
    ["trend", "Trend"],
    ["details", "Details"],
  ];
  return html`
    <div class="drill-modes" role="tablist" aria-label="Drill views">
      ${tabs.map(([id, label]) => {
        const selected = current === id;
        return html`<button type="button" class="drill-mode${selected ? " is-active" : ""}" role="tab" id="drill-tab-${id}" data-drill-mode="${id}" aria-selected="${selected ? "true" : "false"}" aria-controls="drill-pane-${id}" tabindex="${selected ? "0" : "-1"}">${label}</button>`;
      })}
    </div>
  `;
}

function drillTrendPane({ trend, trendError, trendLoading, comparisonNone, metricLabel, periodLabel, compareContext }) {
  if (trendError && trendError.status === 403) {
    return html`<p class="banner warn" data-drill-trend-unauthorized="true">Not authorized to load this trend.</p>`;
  }
  if (trendError && trendError.status === 422) {
    return html`<p class="banner warn" data-drill-trend-unsupported="true">${trendError.message || "This trend combination is not supported."}</p>`;
  }
  if (trendError) {
    return html`
      <div data-drill-trend-error="true">
        ${errorBanner(trendError)}
        <p class="overview-filter-actions">
          <button type="button" data-overview-retry="drill-trend">Retry</button>
        </p>
      </div>
    `;
  }
  if (trendLoading && !trend) {
    return html`<div data-drill-trend="true" data-drill-trend-loading="true">${overviewHostLoading()}</div>`;
  }
  if (!trend || trend.empty) {
    return html`<div data-drill-trend="true">${emptyState("No published-history trend is available for this metric.")}</div>`;
  }
  const grain = (trend.selection && trend.selection.grain) || "day";
  const grainLabel = (TREND_GRAIN_OPTIONS.find((item) => item[0] === grain) || [grain, grain])[1];
  const scopeLabel =
    (trend.series && trend.series[0] && trend.series[0].label) ||
    (trend.selection && trend.selection.breakdown ? "Breakdown" : "Company total");
  const overlay = comparisonNone ? "No comparison" : trend.comparison_shown ? `vs ${compareContext}` : "No comparison";
  return html`
    <div class="drill-trend-body" data-drill-trend="true">
      <header class="drill-trend-context" data-drill-trend-context="true">
        <p class="drill-trend-metric" data-drill-trend-metric="true">${metricLabel}</p>
        <p class="drill-trend-meta">
          <span data-drill-trend-period="true">${periodLabel}</span>
          <span data-drill-trend-compare="true">${overlay}</span>
          <span data-drill-trend-grain="true">${grainLabel}</span>
          <span data-drill-trend-scope="true">${scopeLabel}</span>
        </p>
      </header>
      <p class="muted drill-hint">${scopeLabel} · ${grainLabel} · ${comparisonNone || !trend.comparison_shown ? "current series only" : "comparison overlay"}</p>
      <div class="drill-trend-frame">${renderTrendChart(trend, { interactive: false, tooltip: true })}</div>
    </div>
  `;
}

export function overviewDrillPanel({ query, data, drill, drillError, mode, trend, trendError, trendLoading }) {
  const parsed = parseDrillQuery(query);
  if (!parsed) return "";
  const comparison = (drill && !drillError && drill.comparison) || (data && data.comparison) || {};
  const selection = (drill && drill.selection) || {};
  const metric = (drill && drill.metric) || {};
  const applied = (data && data.applied) || (drill && drill.applied) || {};
  const kpi = parsed.origin === "trend" ? null : overviewKpiForMetric(data, parsed.metric);
  const metricLabel =
    (kpi && kpi.label) ||
    metric.label ||
    (TREND_METRIC_OPTIONS.find((item) => item[0] === parsed.metric) || [parsed.metric, parsed.metric])[1];
  const kind = metric.kind || (kpi && kpi.kind) || "money";
  const view = drillModeKey(mode);
  const comparisonNone = comparison.reason === "comparison_disabled";
  const headerDelta = kpi && comparison.available ? kpiDeltaPresentation(kind, kpi.delta, kpi.delta_pct) : { direction: "", text: "" };
  const headerVs = kpi && comparison.available ? kpiCompareVsText(kind, kpi.prior_value, comparison) : "";
  const headerArrow = headerDelta.direction === "up" ? "↑" : headerDelta.direction === "down" ? "↓" : headerDelta.direction === "flat" ? "→" : "";
  const periodLabel =
    applied.period === "all_history"
      ? "All published history"
      : (data && data.period && (data.period.month_label || data.period.month_start)) ||
        applied.month_start ||
        `${applied.day_from || (data && data.period && data.period.day_min) || ""} – ${applied.day_to || (data && data.period && data.period.day_max) || ""}`;
  const compareContext = comparisonNone
    ? "None"
    : comparison.available
      ? comparison.month_label || comparison.month_start || `${comparison.day_min || ""} – ${comparison.day_max || ""}`
      : "No prior period";
  const dimLabel = DRILL_DIM_LABELS[selection.dimension || parsed.dimension] || parsed.dimension;
  const unsupported = drillError && drillError.status === 422;
  const failed = drillError && drillError.status === 401;
  const forbidden = drillError && drillError.status === 403;
  const errored = drillError && !unsupported && !failed && !forbidden;
  const closeHref = overviewHref(stripDrill(query));
  const parents = selection.parents || [];
  const crumbs = [];
  crumbs.push({
    label: parsed.origin === "trend" ? "Trend slice" : "KPI",
    href: overviewHref(
      withDrill(query, {
        origin: parsed.origin,
        metric: parsed.metric,
        dimension: parents.length ? parents[0].dimension : parsed.dimension,
        slice: parsed.slice,
        sliceGrain: parsed.sliceGrain,
      }),
    ),
  });
  parents.forEach((parent, index) => {
    const nextParents = parents.slice(0, index + 1).map((item) => parentToken(item.dimension, item.value));
    const nextDim = index + 1 < parents.length ? parents[index + 1].dimension : parsed.dimension;
    crumbs.push({
      label: `${DRILL_DIM_LABELS[parent.dimension] || parent.dimension}: ${parent.label || parent.value || "(blank)"}`,
      href: overviewHref(
        withDrill(query, {
          origin: parsed.origin,
          metric: parsed.metric,
          dimension: nextDim,
          parents: nextParents,
          slice: parsed.slice,
          sliceGrain: parsed.sliceGrain,
        }),
      ),
    });
  });
  crumbs.push({
    label: dimLabel,
    href: "",
  });
  const backHref =
    parents.length === 0
      ? closeHref
      : overviewHref(
          withDrill(query, {
            origin: parsed.origin,
            metric: parsed.metric,
            dimension: parents[parents.length - 1].dimension,
            parents: parents.slice(0, -1).map((item) => parentToken(item.dimension, item.value)),
            slice: parsed.slice,
            sliceGrain: parsed.sliceGrain,
          }),
        );
  const nextDim = (selection.next_dimensions || [])[0] || "";
  const rowHref = (row) =>
    row.drillable && nextDim
      ? overviewHref(
          withDrill(query, {
            origin: parsed.origin,
            metric: parsed.metric,
            dimension: nextDim,
            parents: [...parents.map((item) => parentToken(item.dimension, item.value)), parentToken(selection.dimension, row.key)],
            slice: parsed.slice,
            sliceGrain: parsed.sliceGrain,
          }),
        )
      : "";
  let body;
  if (failed) body = errorBanner(drillError);
  else if (forbidden) body = html`<p class="banner warn" data-drill-unauthorized="true">Not authorized to open this drilldown.</p>`;
  else if (unsupported) body = html`<p class="banner warn" data-drill-unsupported="true">${drillError.message || "This drill combination is not supported."}</p>`;
  else if (errored) {
    body = html`
      <div data-drill-error="true">
        ${errorBanner(drillError)}
        <p class="overview-filter-actions">
          <button type="button" data-overview-retry="drill">Retry</button>
        </p>
      </div>
    `;
  } else if (!drill && !drillError) {
    body = html`
      <div class="drill-analysis" data-drill-loading="true">${overviewHostLoading()}</div>
    `;
  } else if (!drill || drill.empty) body = emptyState("No published history matches this drill.");
  else {
    const rows = Array.isArray(drill.rows) ? drill.rows : [];
    const chartRows = drillChartRows(rows);
    const chartPeak = drillChartPeak(chartRows);
    const chartPeakValue = drillChartNumber(chartPeak && chartPeak.value);
    const nextDimLabel = DRILL_DIM_LABELS[nextDim] || nextDim;
    const chartScope = [
      drillChartScopeLabel(rows, chartRows),
      chartPeak ? `bars scaled to ${formatKpiValue(kind, chartPeak.value)}` : "no comparable values",
    ].join(" · ");
    const chart = chartRows.length
      ? html`
          <figure class="drill-chart" data-drill-chart="true" data-drill-chart-dimension="${selection.dimension || parsed.dimension}" data-drill-chart-metric="${parsed.metric}">
            <figcaption class="drill-chart-caption">
              <span class="drill-chart-title" data-drill-chart-title="true">${dimLabel} breakdown</span>
              <span class="drill-chart-scope" data-drill-chart-scope="true">${chartScope}</span>
            </figcaption>
            <ol class="drill-chart-rows" data-drill-chart-rows="true">
              ${chartRows.map((row) => {
                const href = rowHref(row);
                const share = contributionSharePct(row.contribution_pct);
                const bar = drillChartBarPct(row.value, chartPeakValue);
                const rowValue = drillChartNumber(row.value);
                const negative = (rowValue || 0) < 0;
                const capped =
                  bar === 100 && chartPeakValue != null && Math.abs(rowValue) > Math.abs(chartPeakValue);
                const barClass =
                  bar == null
                    ? ""
                    : bar === 0
                      ? " is-zero"
                      : capped
                        ? " is-capped"
                        : negative
                          ? " is-negative"
                          : "";
                const label = row.label || "(blank)";
                const inner = html`
                  <span class="sr-only">${drillChartRowText({
                    row,
                    kind,
                    dimLabel,
                    metricLabel,
                    share,
                    capped,
                    drillLabel: href ? nextDimLabel : "",
                  })}</span>
                  <span class="drill-chart-rank" aria-hidden="true">${row.rank == null ? "—" : row.rank}</span>
                  <span class="drill-chart-label" aria-hidden="true">${label}</span>
                  <span class="drill-chart-plot" aria-hidden="true">
                    ${bar == null ? "" : html`<span class="drill-chart-bar${barClass}" style="width: ${bar}%;"></span>`}
                  </span>
                  <span class="drill-chart-value" aria-hidden="true">${formatKpiValue(kind, row.value)}</span>
                  <span class="drill-chart-share" aria-hidden="true">${share == null ? "" : `${share.toLocaleString("en-US", { maximumFractionDigits: 1 })}%`}</span>
                  ${href ? html`<span class="drill-chart-open" aria-hidden="true" data-drill-open="true">Open</span>` : html`<span class="drill-chart-open is-static" aria-hidden="true"></span>`}
                `;
                return href
                  ? html`<li class="drill-chart-row" data-drill-chart-row="${row.key}" data-drillable="true"><a class="drill-chart-item" href="${href}" data-drill-next="${row.key}" title="${label}">${inner}</a></li>`
                  : html`<li class="drill-chart-row" data-drill-chart-row="${row.key}" data-drillable="false"><div class="drill-chart-item is-static" title="${label}">${inner}</div></li>`;
              })}
            </ol>
          </figure>
        `
      : "";
    const breakdown = html`
      <section class="drill-analysis" data-drill-pane="breakdown" id="drill-pane-breakdown" role="tabpanel" aria-labelledby="drill-tab-breakdown" ${view === "breakdown" ? "" : raw(" hidden")}>
        <h3 class="drill-section-title">By ${dimLabel}</h3>
        ${
          selection.can_go_deeper
            ? html`<p class="muted drill-hint">Click a bar to open ${nextDimLabel}.</p>`
            : html`<p class="muted drill-hint" data-drill-terminal="true">Deeper grouping is not available from this level.</p>`
        }
        ${drill.truncated ? html`<p class="muted" data-drill-truncated="true">${drill.truncated_message}</p>` : ""}
        ${chart}
        <details class="drill-breakdown-all" data-drill-breakdown-details="true" ${chartRows.length ? "" : raw(" open")}>
          <summary data-drill-breakdown-toggle="true">Ranked list · all ${rows.length} ${rows.length === 1 ? "group" : "groups"}</summary>
          <ol class="drill-bars" data-drill-breakdown="true">
            ${rows.map((row) => {
              const href = rowHref(row);
              const share = contributionSharePct(row.contribution_pct);
              const inner = html`
                <span class="drill-bar-rank">${row.rank == null ? "—" : row.rank}</span>
                <span class="drill-bar-label" title="${row.label || "(blank)"}">${row.label || "(blank)"}</span>
                <span class="drill-bar-track">${share == null ? "" : html`<span class="drill-bar-fill" style="width: ${share}%;"></span>`}</span>
                <span class="drill-bar-value">${formatKpiValue(kind, row.value)}</span>
                ${share == null ? "" : html`<span class="drill-bar-share">${share.toLocaleString("en-US", { maximumFractionDigits: 1 })}%</span>`}
              `;
              return href
                ? html`<li data-drill-row="${row.key}" data-drillable="true"><a class="drill-bar-row" href="${href}" data-drill-next="${row.key}">${inner}</a></li>`
                : html`<li data-drill-row="${row.key}" data-drillable="false"><div class="drill-bar-row is-static">${inner}</div></li>`;
            })}
          </ol>
        </details>
      </section>
    `;
    const trendPane = html`
      <section class="drill-trend" data-drill-pane="trend" id="drill-pane-trend" role="tabpanel" aria-labelledby="drill-tab-trend" ${view === "trend" ? "" : raw(" hidden")}>
        ${drillTrendPane({
          trend,
          trendError,
          trendLoading: Boolean(trendLoading) || (view === "trend" && !trend && !trendError),
          comparisonNone,
          metricLabel,
          periodLabel,
          compareContext,
        })}
      </section>
    `;
    const details = html`
      <section class="drill-detail" data-drill-pane="details" id="drill-pane-details" role="tabpanel" aria-labelledby="drill-tab-details" ${view === "details" ? "" : raw(" hidden")}>
        <h3 class="drill-section-title">Detail</h3>
        <div class="table-wrap drill-table-wrap">
          <table class="data-table drill-table" data-drill-table="true">
            <thead>
              <tr>
                <th>Rank</th>
                <th>${dimLabel}</th>
                <th>Value</th>
                ${comparison.available ? html`<th>Comparison</th><th>Delta</th>` : ""}
                <th>Share</th>
              </tr>
            </thead>
            <tbody>
              ${rows.map((row) => {
                const href = rowHref(row);
                const contribution =
                  row.contribution_pct == null || row.contribution_pct === ""
                    ? "n/a"
                    : `${(Number(row.contribution_pct) * 100).toLocaleString("en-US", { maximumFractionDigits: 1 })}%`;
                return html`
                  <tr data-drill-row="${row.key}" data-drillable="${row.drillable ? "true" : "false"}">
                    <td>${row.rank == null ? "—" : row.rank}</td>
                    <td class="drill-name" title="${row.label || "(blank)"}">${href ? html`<a href="${href}" data-drill-next="${row.key}">${row.label || "(blank)"}</a>` : row.label || "(blank)"}</td>
                    <td class="num">${formatKpiValue(kind, row.value)}</td>
                    ${
                      comparison.available
                        ? html`<td class="num">${formatKpiValue(kind, row.prior_value)}</td><td class="num">${formatDelta(kind, row.delta, row.delta_pct)}</td>`
                        : ""
                    }
                    <td class="num">${contribution}</td>
                  </tr>
                `;
              })}
            </tbody>
          </table>
        </div>
      </section>
    `;
    body = html`
      ${breakdown}
      ${trendPane}
      ${details}
      <p class="sr-only" data-drill-comparison="${comparison.available ? "shown" : comparison.reason || "unavailable"}">
        ${comparison.available ? "Comparison is the D2/D3 prior window, matched by dimension value." : "No comparison for this drill."}
      </p>
    `;
  }
  const usedParents = new Set(parents.map((item) => item.dimension));
  const catalog = (drill && drill.dimensions) || Object.entries(DRILL_DIM_LABELS).map(([value, label]) => ({ value, label }));
  const dimensionOptions = catalog.filter(
    (item) => item.value === (selection.dimension || parsed.dimension) || !usedParents.has(item.value),
  );
  return html`
    <div class="drill-root" data-overview-drill="true" data-drill-layout="${view === "trend" ? "center" : "drawer"}">
      <button type="button" class="modal-backdrop" data-drill-close="true" tabindex="-1" aria-label="Close drilldown"></button>
      <section class="drill-panel" data-drill-panel="true" data-drill-view="${view}" role="dialog" aria-modal="true" aria-labelledby="drill-title" tabindex="-1">
        <header class="drill-header">
          <div class="drill-header-row">
            <h2 id="drill-title" class="drill-metric-name" data-drill-metric="${parsed.metric}">${metricLabel}</h2>
            <a class="drill-close" href="${closeHref}" data-drill-close="true" aria-label="Close drilldown">×</a>
          </div>
          <div class="drill-header-metrics">
            ${
              kpi
                ? html`<p class="drill-metric-value" data-drill-header-value="true">${formatKpiValue(kind, kpi.value)}</p>`
                : ""
            }
            ${
              comparisonNone || !comparison.available
                ? ""
                : headerDelta.text
                  ? html`<p class="drill-metric-delta is-delta-${headerDelta.direction}" data-drill-header-delta="true"><span aria-hidden="true">${headerArrow}</span> ${headerDelta.text}</p>`
                  : ""
            }
          </div>
          ${
            comparisonNone
              ? html`<p class="drill-metric-vs is-none" data-drill-header-comparison="none">No comparison</p>`
              : comparison.available
                ? html`<p class="drill-metric-vs" data-drill-header-comparison="available">${headerVs || `vs ${compareContext}`}</p>`
                : html`<p class="drill-metric-vs is-none" data-drill-header-comparison="${comparison.reason || "unavailable"}">No comparison</p>`
          }
        </header>
        <dl class="drill-context" data-drill-context="true">
          <div><dt>Period</dt><dd>${periodLabel}${parsed.origin === "trend" && parsed.slice ? ` · slice ${parsed.slice}` : ""}</dd></div>
          <div><dt>Comparison</dt><dd data-drill-context-comparison="${comparisonNone ? "none" : comparison.available ? "available" : comparison.reason || "unavailable"}">${compareContext}</dd></div>
          <div><dt>Filters</dt><dd>${drillFilterSummary(applied)}</dd></div>
          <div>
            <dt>Dimension</dt>
            <dd>
              <form class="drill-dimension-form" data-overview-drill-form="true">
                <label class="sr-only" for="drill-dimension">Breakdown dimension</label>
                <select id="drill-dimension" name="drill_dimension" data-overview-drill-autosubmit="true">
                  ${dimensionOptions.map(
                    (item) => html`<option value="${item.value}" ${item.value === (selection.dimension || parsed.dimension) ? raw(" selected") : ""}>${item.label}</option>`,
                  )}
                </select>
              </form>
            </dd>
          </div>
        </dl>
        <nav class="drill-crumbs" aria-label="Drill path">
          ${crumbs.map((crumb, index) =>
            crumb.href && index < crumbs.length - 1
              ? html`<a href="${crumb.href}">${crumb.label}</a><span aria-hidden="true"> / </span>`
              : html`<span>${crumb.label}</span>`,
          )}
        </nav>
        ${drillModeTabs(view)}
        <div class="drill-content" data-drill-content="true">${body}</div>
        <div class="drill-actions">
          <button type="button" class="btn-secondary" data-overview-export="csv" data-drill-export="true">Export CSV</button>
          ${askAboutButton({
            source: "drill",
            metric: parsed.metric,
            dimension: selection.dimension || parsed.dimension,
            question: "Explain this drilldown slice.",
          })}
          <a class="btn-secondary" href="${backHref}" data-drill-back="true">Back</a>
        </div>
      </section>
    </div>
  `;
}

export function overviewFilterCompactBar({ data }) {
  const applied = (data && data.applied) || {};
  const period = (data && data.period) || {};
  const comparison = (data && data.comparison) || {};
  const periodLabel =
    applied.period === "all_history"
      ? "All published history"
      : period.month_label || applied.month_start || `${applied.day_from || period.day_min || ""} – ${applied.day_to || period.day_max || ""}`;
  const compareLabel =
    comparison.reason === "comparison_disabled" || !comparison.available
      ? "none"
      : comparison.month_label || comparison.month_start || "prior period";
  return html`
    <a class="overview-filter-compact" href="#overview-filter-bar" data-overview-filter-compact="true">
      <span class="overview-filter-compact-action">Filters</span>
      <span data-overview-filter-compact-period="true">${periodLabel}</span>
      <span data-overview-filter-compact-compare="true">Comparison: ${compareLabel}</span>
    </a>
  `;
}

export function workspaceContextBar({ session, data, query }) {
  const company = overviewCompanyLabel(session, data);
  const applied = (data && data.applied) || {};
  const period = (data && data.period) || {};
  const comparison = (data && data.comparison) || {};
  const periodLabel =
    applied.period === "all_history"
      ? "All published history"
      : period.month_label || applied.month_start || `${applied.day_from || period.day_min || ""} – ${applied.day_to || period.day_max || ""}`;
  const compareLabel = comparison.available
    ? comparison.month_label || comparison.month_start || "prior period"
    : "none";
  const bits = [];
  if ((applied.channels || []).length) bits.push(`channel ${(applied.channels || []).join(", ")}`);
  if ((applied.campaign_ids || []).length) bits.push(`${applied.campaign_ids.length} campaign(s)`);
  if ((applied.filter_logic_1 || []).length) bits.push("Filter Logic 1");
  if ((applied.filter_logic_1_group || []).length) bits.push("Filter Logic 1 group");
  const metricKey =
    (query && (query.get("kpi") || query.get("trend_metric") || query.get("ex_metric") || query.get("drill_metric"))) ||
    "";
  const metric = metricKey
    ? (TREND_METRIC_OPTIONS.find((item) => item[0] === metricKey) || [metricKey, metricKey])[1]
    : "Total Cost (default)";
  const dimension =
    (query && (query.get("ex_dimension") || query.get("trend_breakdown") || query.get("drill_dimension"))) || "";
  const saved = query && query.get("saved");
  return html`
    <div class="workspace-context" data-workspace-context="true">
      <p>
        <strong>${company}</strong>
        · ${periodLabel}
        · Comparison: <strong data-workspace-comparison="${comparison.available ? "available" : comparison.reason || "none"}">${compareLabel}</strong>
        · Metric: ${metric}${dimension ? ` · ${dimension}` : ""}
        ${bits.length ? html` · ${bits.join(" · ")}` : ""}
        ${saved ? html` · <span data-workspace-saved-loaded="${saved}">Saved analysis loaded</span>` : ""}
      </p>
    </div>
  `;
}

export function workspaceSavedPanel({ saved, savedError, query }) {
  const items = saved && Array.isArray(saved.items) ? saved.items : [];
  const loaded = (query && query.get && query.get("saved")) || "";
  let list;
  if (savedError) {
    list = html`<div data-saved-error="true">${errorBanner(savedError)}</div>`;
  } else if (!items.length) {
    list = html`<p class="muted" data-saved-empty="true">No saved analyses for this company user yet.</p>`;
  } else {
    list = html`
      <ul class="saved-analysis-list" data-saved-list="true">
        ${items.map(
          (item) => html`
            <li class="${item.id === loaded ? "is-selected" : ""}" data-saved-id="${item.id}">
              <button type="button" class="action-link" data-saved-open="${item.id}">${item.title}</button>
              <button type="button" class="btn-secondary" data-saved-rename="${item.id}" data-saved-title="${item.title}">Rename</button>
              <button type="button" class="btn-secondary" data-saved-delete="${item.id}" data-saved-title="${item.title}">Delete</button>
            </li>
          `,
        )}
      </ul>
    `;
  }
  return html`
    <div class="tool-card workspace-toolbar" data-workspace-toolbar="true">
      <header class="tool-card-head">
        ${icon("save")}
        <span>
          <span class="overview-kicker">Save this analysis</span>
          <span class="overview-lede">Keep this view for the current company and come back to it later.</span>
        </span>
      </header>
      <form class="saved-analysis-form" data-saved-form="true">
        <label>
          Name
          <input name="title" maxlength="80" required placeholder="Name this view" data-saved-title-input="true" />
        </label>
        <button type="submit" data-saved-submit="true">Save</button>
      </form>
      ${list}
    </div>
  `;
}

export function overviewPeriodLine(data) {
  const period = (data && data.period) || {};
  const periodTitle =
    period.grain === "all_history"
      ? "All published history"
      : period.grain === "range"
        ? `${period.day_min || ""} – ${period.day_max || ""}`
        : period.month_label || period.month_start || "";
  return html`
    <p class="muted">Period: <strong>${periodTitle}</strong>${data && data.applied && data.applied.is_default ? " (latest published month)" : ""}</p>
  `;
}

export function overviewComparisonBanner(data) {
  const comparison = (data && data.comparison) || {};
  const disabled = comparison.reason === "comparison_disabled";
  const comparisonLabel = comparison.available
    ? `Compared with ${comparison.month_label || comparison.month_start || `${comparison.day_min || ""} – ${comparison.day_max || ""}`}`
    : disabled
      ? "No comparison"
      : "No prior-period comparison";
  return html`
    <p class="${comparison.available || disabled ? "muted" : "banner warn"}" data-overview-comparison="${comparison.available ? "available" : comparison.reason || "unavailable"}">
      ${comparisonLabel}
      ${comparison.available || disabled ? "" : html` — ${comparison.reason === "comparison_not_applicable" ? "auto comparison applies to a single published month." : "a comparable published period does not exist."}`}
    </p>
  `;
}

function kpiDeltaPresentation(kind, delta, deltaPct) {
  const pctNum = deltaPct == null || deltaPct === "" ? NaN : Number(deltaPct) * 100;
  const deltaNum = delta == null || delta === "" ? NaN : Number(delta);
  let direction = "";
  if (Number.isFinite(pctNum)) direction = pctNum > 0 ? "up" : pctNum < 0 ? "down" : "flat";
  else if (Number.isFinite(deltaNum)) direction = deltaNum > 0 ? "up" : deltaNum < 0 ? "down" : "flat";
  let text = "";
  if (Number.isFinite(pctNum)) {
    text = `${Math.abs(pctNum).toLocaleString("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 })}%`;
  } else if (delta != null && delta !== "") {
    text = formatKpiValue(kind, delta);
  }
  return { direction, text };
}

function kpiCompareVsText(kind, priorValue, comparison) {
  if (!comparison || !comparison.available) return "";
  const label = comparison.month_label || comparison.month_start || "";
  const prior = formatKpiValue(kind, priorValue);
  if (label && prior) return `vs ${label} ${prior}`;
  if (label) return `vs ${label}`;
  if (prior && prior !== "n/a") return `vs ${prior}`;
  return "";
}

export function overviewKpiCardsHtml({ data, query, sparkline }) {
  const comparison = (data && data.comparison) || {};
  const comparisonNone = comparison.reason === "comparison_disabled";
  const comparisonOff = !comparison.available;
  const parsed = parseDrillQuery(query);
  const kpis = Array.isArray(data && data.kpis) ? data.kpis : [];
  return kpis.map((kpi) => {
    const delta = comparison.available ? kpiDeltaPresentation(kpi.kind, kpi.delta, kpi.delta_pct) : { direction: "", text: "" };
    const selected = Boolean(
      (query && query.get && query.get("kpi") === kpi.id) ||
        (parsed && parsed.origin === "kpi" && parsed.metric === kpi.id),
    );
    return overviewKpiCard({
      id: kpi.id,
      label: kpi.label,
      value: formatKpiValue(kpi.kind, kpi.value),
      deltaText: comparison.available ? delta.text : "",
      deltaDirection: comparison.available ? delta.direction : "",
      vsText: comparison.available ? kpiCompareVsText(kpi.kind, kpi.prior_value, comparison) : "",
      definition: kpi.definition,
      comparisonNone,
      unavailableComparison: comparisonOff && !comparisonNone,
      selected,
      sparklineHtml: kpiSparklineMarkup(sparkline, kpi.id),
      trendHref: overviewHref(withTrendMetric(query || new URLSearchParams(), kpi.id)),
      detailsHref: overviewHref(
        withDrill(withFocus(query || new URLSearchParams(), { kpi: kpi.id }), {
          origin: "kpi",
          metric: kpi.id,
          dimension: "campaign_id",
        }),
      ),
    });
  });
}

export function overviewSectionRetry(name, error) {
  const copy = {
    kpis: ["Key Performance Indicators", "Published-history KPIs for the current filters."],
    trends: ["Trends", "Track how the selected metric changes over time."],
    explorer: ["Performance Explorer", "Rank dimensions to identify what drives performance."],
    insights: ["Insights", "Evidence-backed changes and drivers."],
    anomalies: ["Anomalies", "Unusual behavior against historical baseline."],
  };
  const [title, description] = copy[name] || [name, ""];
  return html`
    <section class="overview-panel">
      <header class="overview-panel-head">
        <span class="overview-panel-copy">
          <span class="overview-kicker">${title}</span>
          ${description ? html`<span class="overview-lede">${description}</span>` : ""}
        </span>
      </header>
      <div class="overview-section-error overview-empty" data-overview-section-error="${name}">
        ${errorBanner(error)}
        <p class="overview-filter-actions">
          <button type="button" data-overview-retry="${name}">Retry</button>
        </p>
      </div>
    </section>
  `;
}

export function overviewHostLoading() {
  return html`
    <div class="skeleton-stack overview-section-skeleton" role="status" aria-live="polite">
      <span class="sr-only">Updating…</span>
      <div class="skeleton skeleton-title"></div>
      <div class="skeleton skeleton-line"></div>
      <div class="skeleton skeleton-table"></div>
    </div>
  `;
}

function overviewHostOrRetry(name, error, content) {
  return error && error.status !== 422 ? overviewSectionRetry(name, error) : content;
}

export function clientOverviewView({ session, data, error, loading, query, trend, trendError, explorer, explorerError, insights, insightsError, anomalies, anomaliesError, drill, drillError, drillMode, drillTrend, drillTrendError, saved, savedError, sparkline }) {
  if (loading) {
    return html`
      <section class="panel">
        ${pageHeader({
          title: "Overview",
          description: "Published-history KPIs for the authenticated company. Latest published month is the default period.",
        })}
        ${loadingState()}
      </section>
    `;
  }
  if (error) {
    return html`
      <section class="panel">
        ${pageHeader({ title: "Overview", description: "Published-history KPIs for the authenticated company." })}
        ${errorBanner(error)}
      </section>
    `;
  }
  const company = overviewCompanyLabel(session, data);
  if (!data || !data.has_published_history) {
    return html`
      <section class="panel">
        ${pageHeader({
          title: "Overview",
          description: "KPI values come from authoritative published history only. Unpublished and processing data are not shown.",
        })}
        <p class="muted">Company: <strong>${company}</strong></p>
        ${emptyState("No published history is available for this company.")}
      </section>
    `;
  }
  return html`
    <section class="overview-workspace" data-overview-shell="true">
      <header class="overview-hero workspace-chrome" data-workspace-chrome="true">
        ${pageHeader({
          title: "Overview",
          description: "Analytical workspace for published history.",
          actions: askAiControl(query),
        })}
        <div class="overview-host" data-overview-context-host="true">${workspaceContextBar({ session, data, query })}</div>
      </header>
      <div class="overview-sticky-scope">
      <section class="overview-section overview-section-filters" data-overview-section="filters">
        <header class="overview-section-head">
          <div>
            <h2>Filters &amp; analytical context</h2>
            <p class="muted">Period, comparison, and allowlisted dimensions for this workspace.</p>
          </div>
          <div class="overview-section-meta">
            <div class="overview-host" data-overview-period-host="true">${overviewPeriodLine(data)}</div>
            <div class="overview-host" data-overview-comparison-host="true">${overviewComparisonBanner(data)}</div>
          </div>
        </header>
      </section>
      <div class="overview-filter-dock" data-overview-filter-dock="true">
        <div class="overview-host" id="overview-filter-bar" data-overview-filter-bar="true">${overviewFilterBar(data, query)}</div>
      </div>
      <div class="overview-host overview-filter-compact-host" data-overview-filter-compact-host="true">${overviewFilterCompactBar({ data, query })}</div>
      <section class="overview-section overview-section-kpis" data-overview-section="kpis">
        <header class="overview-section-head">
          <div>
            <h2>Key Performance Indicators</h2>
            <p class="muted">Published-history KPIs for the current filters.</p>
          </div>
        </header>
        <div class="metrics overview-host" data-overview-kpis-host="true">${overviewKpiCardsHtml({ data, query, sparkline })}</div>
      </section>
      <div class="overview-stack">
        <div class="overview-host" data-overview-explorer-host="true">${overviewHostOrRetry("explorer", explorerError, overviewExplorerSection({ query, data, explorer, explorerError }))}</div>
        <div class="overview-host" data-overview-trends-host="true">${overviewHostOrRetry("trends", trendError, overviewTrendSection({ query, trend, trendError }))}</div>
        <div class="overview-host" data-overview-insights-host="true">${overviewHostOrRetry("insights", insightsError, overviewInsightsSection({ query, data, insights, insightsError }))}</div>
        <div class="overview-host" data-overview-anomalies-host="true">${overviewHostOrRetry("anomalies", anomaliesError, overviewAnomaliesSection({ query, data, anomalies, anomaliesError }))}</div>
        <section class="overview-section overview-section-tools" data-overview-section="tools">
          <header class="overview-section-head">
            <div>
              <h2>Analysis Tools</h2>
              <p class="muted">Continue analysis with Ask, Saved Views and Export.</p>
            </div>
          </header>
          <div class="overview-tools-body">
            <div class="overview-host" data-overview-ask-host="true">${overviewAskSection({ session, data, query })}</div>
            <div class="overview-host" data-overview-saved-host="true">${workspaceSavedPanel({ saved, savedError, query })}</div>
            <article class="tool-card" data-overview-export-card="true">
              <header class="tool-card-head">
                ${icon("downloads")}
                <span>
                  <span class="overview-kicker">Export CSV</span>
                  <span class="overview-lede">Download the current analytical workspace as CSV.</span>
                </span>
              </header>
              <button type="button" class="btn-secondary" data-overview-export="csv">Export CSV</button>
            </article>
          </div>
        </section>
      </div>
      <div class="overview-host" data-overview-drill-host="true">${overviewDrillPanel({ query, data, drill, drillError, mode: drillMode, trend: drillTrend, trendError: drillTrendError })}</div>
      </div>
    </section>
  `;
}

export function clientHomeView({ session, currentPublication, publications }) {
  const current = currentPublication && currentPublication.publication;
  const historyItems = itemsOf(publications);
  return html`
    <section class="panel">
      ${pageHeader({
        title: "Reports",
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
              ["Current publication", publicationIdCell(current)],
              ["Published", when(current.published_at)],
              ["Period start", displayCell(current.period_start)],
              ["Period end", displayCell(current.period_end)],
              ["Snapshot", snapshotStatusCell(current)],
            ])
          : emptyState("No published data is available.")
      }
      <p>
        <a class="btn" href="/client/facts" style="display:inline-flex;align-items:center;">Open published data</a>
        <button type="button" data-download-client-report="current">Download Client Report</button>
        ${companyRefreshableWorkbookButton()}
        <button type="button" class="secondary" data-download-published="csv">Download CSV</button>
        <button type="button" class="secondary" data-download-published="xlsx">Download XLSX</button>
      </p>
      ${refreshableDownloadErrorHost()}
      <p class="muted">The Client Report is the nine-sheet static snapshot of the current publication. The refreshable workbook uses the same pivots and follows cumulative published history after Excel Refresh All. It includes the current session token for Refresh All (not a permanent secret). Historical files do not update themselves.</p>
    </section>
    ${
      historyItems.length
        ? html`
            <section class="panel">
              <h2>Earlier publications</h2>
              <p class="muted">Historical publication downloads stay bound to that publication. They are not refreshable and do not follow the current pointer.</p>
              ${dataTable(
                "client publication history",
                ["State", "Snapshot", "Published", "Period", "Report"],
                historyItems.map((item) => [
                  publicationStateCell(item, current),
                  snapshotStatusCell(item),
                  when(item.published_at),
                  html`${displayCell(item.period_start)} – ${displayCell(item.period_end)}`,
                  historicalReportButton(item, current),
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
      ${companyRefreshableWorkbookButton()}
      <button type="button" class="secondary" data-download-published="csv">Download CSV</button>
      <button type="button" class="secondary" data-download-published="xlsx">Download XLSX</button>
    </p>
    ${refreshableDownloadErrorHost()}
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
