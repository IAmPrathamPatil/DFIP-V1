import { ANALYTICS_MULTI, ANALYTICS_SINGLE, DRILL_MULTI, DRILL_SINGLE, EXPLORER_KEYS, FINDING_KEYS, FOCUS_KEYS, TREND_KEYS, drillParamsFromQuery, drillTrendParamsFromQuery, explorerParamsFromQuery, insightsParamsFromQuery, anomaliesParamsFromQuery, askPayloadFromForm, exportParamsFromQuery, generateTrendAskPayload, generatedTrendSelectionFromAsk, overviewHref, overviewParamsFromQuery, parseDrillQuery, queryFromExplorerForm, queryFromOverviewForm, queryFromTrendForm, queryFromWorkspaceState, sparklineParamsFromQuery, stripDrill, withDrill, withTrendSelection, workspaceStateFromQuery } from "./analytics-state.js";
import { contextualTrendFromSparkline } from "./sparkline.js";
import { DfipApiClient, ApiError } from "./api-client.js";
import { clearToken, getStoredToken, storeToken } from "./auth.js";
import { layout } from "./components.js";
import { confirmAction, showToast } from "./dialogs.js";
import { errorBanner, html, loadingState, MIN_PASSWORD_LENGTH, toHtml } from "./format.js";
import { currentLocation, matchRoute, navigate } from "./router.js";
import {
  allowsInactiveCompanyRoute,
  canAccessAdmin,
  canAccessClient,
  inspectorClients,
  needsCompanySelection,
  setAdminRoles,
} from "./roles.js";
import {
  adminHomeView,
  batchDetailView,
  batchListView,
  catalogView,
  clientFactDetailView,
  clientFactListView,
  clientHomeView,
  clientOverviewView,
  companySelectView,
  companiesView,
  credentialView,
  downloadsView,
  factDetailView,
  factListView,
  historyListView,
  notFoundView,
  overviewAnomaliesSection,
  generateTrendStatusView,
  overviewAskContextLine,
  overviewAskEmptyResult,
  overviewAskResultView,
  overviewComparisonBanner,
  overviewDrillPanel,
  overviewExplorerSection,
  overviewFilterBar,
  overviewFilterCompactBar,
  overviewFilterPatchModel,
  syncFilterPickerSummary,
  overviewHostLoading,
  overviewInsightsSection,
  overviewKpiCardsHtml,
  overviewPeriodLine,
  overviewSectionRetry,
  overviewTrendSection,
  pageParams,
  publicationsView,
  rawUploadBanner,
  rawUploadLifecycle,
  processingProgressPanel,
  publicationProgressPanel,
  reviewView,
  runDetailView,
  runListView,
  sourceFileDetailView,
  sourceFileListView,
  unauthorizedView,
  uploadCenterView,
  workspaceContextBar,
  workspaceSavedPanel,
} from "./views.js";

const root = document.getElementById("app");
let config = {
  apiBaseUrl: "http://127.0.0.1:8000",
  apiPrefix: "/api/v1",
  adminRoles: ["admin", "publisher"],
  uploadMaxBytes: 67108864,
  uploadMaxFiles: 5,
  uploadMaxTotalBytes: 134217728,
};
let api;
let session = null;
let publisherSetupDone = false;
const PENDING_UPLOAD_KEY = "dfip.pendingRawUpload";
const UPLOAD_POLL_MS = 2000;
let uploadPollTimer = null;
let runPollTimer = null;
let batchPollTimer = null;
let publishPollTimer = null;
let overviewRefreshSeq = 0;
let lastOverviewSearch = "";
let overviewClientId = "";
let overviewKpiData = null;
let refreshableWorkbookDownloadInFlight = false;
const REFRESHABLE_DOWNLOAD_LABEL = "Download Refreshable Workbook";
let overviewSparklineData = null;
let overviewTrendsData = null;
let overviewDrillMode = "breakdown";
let overviewDrillTrendData = null;
let overviewDrillTrendError = null;
let overviewDrillTrendKey = "";
let overviewDrillTrendSeq = 0;
let overviewDrillPayload = { drill: null, drillError: null };
let overviewSavedDirty = false;
let overviewDrillReturnFocus = "";

const routes = [
  { pattern: /^\/$/, name: "credential", access: "public" },
  { pattern: /^\/unauthorized$/, name: "unauthorized", access: "auth" },
  { pattern: /^\/sign-out$/, name: "sign-out", access: "public" },
  { pattern: /^\/admin$/, name: "admin-home", access: "admin" },
  { pattern: /^\/admin\/companies$/, name: "admin-companies", access: "admin" },
  { pattern: /^\/admin\/upload$/, name: "admin-upload", access: "admin" },
  { pattern: /^\/admin\/source-files$/, name: "admin-source-files", access: "admin" },
  { pattern: /^\/admin\/source-files\/(?<id>[0-9a-fA-F-]+)$/, name: "admin-source-file", access: "admin" },
  { pattern: /^\/admin\/batches$/, name: "admin-batches", access: "admin" },
  { pattern: /^\/admin\/batches\/(?<id>[0-9a-fA-F-]+)$/, name: "admin-batch", access: "admin" },
  { pattern: /^\/admin\/processing-runs$/, name: "admin-runs", access: "admin" },
  { pattern: /^\/admin\/processing-runs\/(?<id>[0-9a-fA-F-]+)$/, name: "admin-run", access: "admin" },
  { pattern: /^\/admin\/review$/, name: "admin-review", access: "admin" },
  { pattern: /^\/admin\/facts$/, name: "admin-facts", access: "admin" },
  { pattern: /^\/admin\/facts\/detail$/, name: "admin-fact-detail", access: "admin" },
  { pattern: /^\/admin\/history$/, name: "admin-history", access: "admin" },
  { pattern: /^\/admin\/logic$/, name: "admin-logic", access: "admin" },
  { pattern: /^\/admin\/labels$/, name: "admin-labels", access: "admin" },
  { pattern: /^\/admin\/catalogs$/, name: "admin-catalogs", access: "admin" },
  { pattern: /^\/admin\/publications$/, name: "admin-publications", access: "admin" },
  { pattern: /^\/admin\/downloads$/, name: "admin-downloads", access: "admin" },
  { pattern: /^\/client$/, name: "client-home", access: "client" },
  { pattern: /^\/client\/overview$/, name: "client-overview", access: "client" },
  { pattern: /^\/client\/facts$/, name: "client-facts", access: "client" },
  { pattern: /^\/client\/facts\/detail$/, name: "client-fact-detail", access: "client" },
];

function uploadCaps() {
  const perFile = Number(config.uploadMaxBytes) || 64 * 1024 * 1024;
  const total = Number(config.uploadMaxTotalBytes) || 2 * perFile;
  const maxFiles = Number(config.uploadMaxFiles) || 5;
  return { perFile, total, maxFiles };
}

function collectFormFiles(data, name) {
  return data.getAll(name).filter((item) => item instanceof File && item.name);
}

function rejectIfOversize(files) {
  const caps = uploadCaps();
  const list = [...files].filter((item) => item instanceof File);
  if (!list.length) return false;
  if (list.length > caps.maxFiles) {
    showToast({
      tone: "error",
      title: "Too many files.",
      message: `At most ${caps.maxFiles} workbooks per request.`,
    });
    return true;
  }
  if (list.some((item) => item.size > caps.perFile)) {
    showToast({
      tone: "error",
      title: "Workbook is too large.",
      message: `Workbook exceeds the maximum allowed size of ${caps.perFile} bytes.`,
    });
    return true;
  }
  const sum = list.reduce((total, item) => total + item.size, 0);
  if (sum > caps.total) {
    showToast({
      tone: "error",
      title: "Upload is too large.",
      message: "Upload request exceeds the maximum allowed size.",
    });
    return true;
  }
  return false;
}

function withUploadLimit(viewArgs) {
  return { ...viewArgs, uploadMaxBytes: uploadCaps().perFile };
}

function render(body, path) {
  root.innerHTML = toHtml(layout({ path, session, body }));
}

function queryObject(query) {
  const params = {};
  for (const [key, value] of query.entries()) {
    params[key] = value;
  }
  return params;
}

function isAuthError(error) {
  return (
    error instanceof ApiError &&
    (error.status === 401 ||
      error.code === "AUTHENTICATION_FAILED" ||
      error.status === 403 ||
      error.code === "AUTHORIZATION_FAILED")
  );
}

function isInactiveCompanyError(error) {
  return (
    error instanceof ApiError &&
    error.status === 403 &&
    String(error.message || "") === "This company is inactive."
  );
}

function stopUploadPoll() {
  if (uploadPollTimer) {
    clearTimeout(uploadPollTimer);
    uploadPollTimer = null;
  }
}

function stopRunPoll() {
  if (runPollTimer) {
    clearTimeout(runPollTimer);
    runPollTimer = null;
  }
}

function stopBatchPoll() {
  if (batchPollTimer) {
    clearTimeout(batchPollTimer);
    batchPollTimer = null;
  }
}

function stopPublishPoll() {
  if (publishPollTimer) {
    clearTimeout(publishPollTimer);
    publishPollTimer = null;
  }
}

function isTerminalBatch(item) {
  if (!item) return true;
  const stage = item.stage || "";
  return (
    stage === "succeeded" ||
    stage === "failed" ||
    stage === "cancelled" ||
    item.status === "failed" ||
    item.status === "cancelled"
  );
}

function scheduleBatchPoll(batchId) {
  stopBatchPoll();
  batchPollTimer = setTimeout(() => {
    pollBatchProgress(batchId);
  }, UPLOAD_POLL_MS);
}

async function pollBatchProgress(batchId) {
  const { path } = currentLocation();
  if (path !== `/admin/batches/${batchId}`) {
    stopBatchPoll();
    return;
  }
  try {
    const item = await api.getBatch(batchId);
    const host = document.querySelector("[data-batch-progress-host]");
    if (host) host.innerHTML = toHtml(processingProgressPanel(item));
    if (isTerminalBatch(item) || item.stuck === true) {
      stopBatchPoll();
      if (item.stage === "cancelled" || item.status === "cancelled") {
        window.dispatchEvent(new Event("dfip:navigate"));
      }
      return;
    }
    scheduleBatchPoll(batchId);
  } catch (error) {
    if (isAuthError(error)) {
      stopBatchPoll();
      handleError(error, path);
      return;
    }
    scheduleBatchPoll(batchId);
  }
}

async function resumeBatchProgressPoll(batchId) {
  if (!batchId) return;
  try {
    const item = await api.getBatch(batchId);
    if (isTerminalBatch(item) && item.stuck !== true) return;
    if (item.stuck === true) return;
    scheduleBatchPoll(batchId);
  } catch (error) {
    if (isAuthError(error)) throw error;
  }
}

function savePendingUpload(payload) {
  sessionStorage.setItem(PENDING_UPLOAD_KEY, JSON.stringify(payload));
}

function loadPendingUpload() {
  try {
    const raw = sessionStorage.getItem(PENDING_UPLOAD_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch (error) {
    return null;
  }
}

function clearPendingUpload() {
  sessionStorage.removeItem(PENDING_UPLOAD_KEY);
}

function pendingFromUpload(uploadResult, clientId) {
  if (uploadResult && Array.isArray(uploadResult.items)) {
    return {
      group: true,
      clientId,
      items: uploadResult.items.map((item) => ({
        batchId: item.batch && item.batch.batch_id,
        sourceFileId: item.source_file_id,
        originalFilename: item.original_filename,
        sha256: item.sha256,
        byteSize: item.byte_size,
        clientId,
      })),
    };
  }
  return {
    batchId: uploadResult.batch && uploadResult.batch.batch_id,
    sourceFileId: uploadResult.source_file_id,
    originalFilename: uploadResult.original_filename,
    sha256: uploadResult.sha256,
    byteSize: uploadResult.byte_size,
    clientId,
  };
}

function uploadRunOf(uploadResult) {
  if (uploadResult && uploadResult.processing_run) return uploadResult.processing_run;
  if (uploadResult && Array.isArray(uploadResult.items) && uploadResult.items.length) {
    return uploadResult.items[uploadResult.items.length - 1].processing_run;
  }
  return null;
}

function isTerminalUpload(uploadResult) {
  const life = rawUploadLifecycle(uploadResult);
  return life === "succeeded" || life === "failed" || life === "cancelled";
}

async function fetchUploadSnapshot(pending) {
  if (pending && pending.group && Array.isArray(pending.items)) {
    const items = [];
    for (const part of pending.items) {
      items.push(await fetchUploadSnapshot({ ...part, clientId: pending.clientId }));
    }
    return {
      file_count: items.length,
      client_id: pending.clientId,
      replayed: items.every((item) => item.replayed),
      published: false,
      accepted: true,
      items,
    };
  }
  const batch = await api.getBatch(pending.batchId);
  const runs = await api.listProcessingRuns({ batch_id: pending.batchId, limit: 8, offset: 0 });
  const items = (runs && runs.items) || [];
  const processing_run = items.length ? items[items.length - 1] : null;
  return {
    source_file_id: pending.sourceFileId,
    original_filename: pending.originalFilename || "",
    sha256: pending.sha256 || "",
    byte_size: pending.byteSize || 0,
    client_id: pending.clientId,
    replayed: false,
    published: false,
    accepted: true,
    batch,
    processing_run,
    transform: null,
    rejections: [],
  };
}

function paintUploadBanner(uploadResult) {
  const host = document.querySelector("[data-upload-status-host]");
  if (!host) return false;
  host.innerHTML = toHtml(rawUploadBanner(uploadResult));
  return true;
}

function scheduleUploadPoll(pending) {
  stopUploadPoll();
  uploadPollTimer = setTimeout(() => {
    pollPendingUpload(pending);
  }, UPLOAD_POLL_MS);
}

function scheduleRunPoll(runId) {
  stopRunPoll();
  runPollTimer = setTimeout(() => {
    pollPendingRun(runId);
  }, UPLOAD_POLL_MS);
}

async function resumePendingRunPoll(runId) {
  if (!runId) return;
  try {
    const item = await api.getProcessingRun(runId);
    if (item.status === "pending" || item.status === "running") {
      scheduleRunPoll(runId);
    }
  } catch (error) {
    if (isAuthError(error)) throw error;
  }
}

async function pollPendingRun(runId) {
  const { path } = currentLocation();
  if (path !== `/admin/processing-runs/${runId}`) {
    stopRunPoll();
    return;
  }
  try {
    const item = await api.getProcessingRun(runId);
    if (item.status === "succeeded" || item.status === "failed" || item.status === "cancelled") {
      stopRunPoll();
      showToast({
        tone: item.status === "succeeded" ? "success" : item.status === "cancelled" ? "info" : "error",
        title:
          item.status === "succeeded"
            ? "Re-process completed."
            : item.status === "cancelled"
              ? "Processing cancelled."
              : "Re-process failed.",
        message:
          item.status === "succeeded"
            ? "A new processing run succeeded. Publication required."
            : item.status === "cancelled"
              ? "Temporary work was discarded. No publication was created."
              : "The new processing run failed. The previous run is unchanged.",
      });
      window.dispatchEvent(new Event("dfip:navigate"));
      return;
    }
    scheduleRunPoll(runId);
  } catch (error) {
    stopRunPoll();
    if (isAuthError(error)) {
      handleError(error, path);
      return;
    }
    handleError(error, path);
  }
}

async function pollPendingUpload(pending) {
  if (currentLocation().path !== "/admin/upload") {
    stopUploadPoll();
    return;
  }
  try {
    const uploadResult = await fetchUploadSnapshot(pending);
    if (isTerminalUpload(uploadResult)) {
      clearPendingUpload();
      stopUploadPoll();
      const life = rawUploadLifecycle(uploadResult);
      if (life === "succeeded") {
        showToast({
          tone: "success",
          title: "Upload completed successfully.",
          message: uploadRunOf(uploadResult)
            ? "Processing completed. Publication required."
            : "Upload completed. Processing did not produce a succeeded run.",
        });
      } else if (life === "cancelled") {
        showToast({
          tone: "info",
          title: "Processing cancelled.",
          message: "Temporary work was discarded. No publication was created.",
        });
      } else {
        const summary =
          (uploadResult.batch && uploadResult.batch.error_summary) ||
          "The workbook could not be processed.";
        showToast({ tone: "error", title: "Processing failed.", message: summary });
      }
      await reloadUpload({ uploadResult, clientId: pending.clientId });
      return;
    }
    paintUploadBanner(uploadResult);
    scheduleUploadPoll(pending);
  } catch (error) {
    if (isAuthError(error)) {
      stopUploadPoll();
      handleError(error, "/admin/upload");
      return;
    }
    scheduleUploadPoll(pending);
  }
}

function resumePendingUploadPoll() {
  const pending = loadPendingUpload();
  if (!pending) return;
  if (pending.group && Array.isArray(pending.items) && pending.items.length) {
    pollPendingUpload(pending);
    return;
  }
  if (!pending.batchId) return;
  pollPendingUpload(pending);
}

async function safeRead(loader) {
  try {
    return await loader();
  } catch (error) {
    if (isAuthError(error)) throw error;
    return null;
  }
}

async function latestBundle(listFn, extra = {}) {
  const first = await safeRead(() => listFn({ limit: 1, offset: 0, ...extra }));
  const total = first && first.pagination ? first.pagination.total : 0;
  if (!total) {
    return { meta: first, latest: null, recent: first || { items: [], pagination: { total: 0, limit: 1, offset: 0 } } };
  }
  const latestPage = await safeRead(() => listFn({ limit: 1, offset: total - 1, ...extra }));
  const start = Math.max(0, total - 8);
  const recent = await safeRead(() => listFn({ limit: Math.min(8, total), offset: start, ...extra }));
  return {
    meta: first,
    latest: latestPage && latestPage.items && latestPage.items[0] ? latestPage.items[0] : null,
    recent: recent || first,
  };
}

async function loadSession() {
  if (!getStoredToken()) {
    session = null;
    return;
  }
  try {
    const refreshed = await api.refresh();
    if (refreshed && refreshed.access_token) {
      storeToken(refreshed.access_token);
    }
    session = refreshed && refreshed.session ? refreshed.session : await api.session();
  } catch (error) {
    if (isInactiveCompanyError(error)) {
      session = await api.session();
      return;
    }
    throw error;
  }
}

async function bindSelectedCompany(clientId) {
  const nextId = String(clientId || "").trim();
  if (!nextId) return;
  if (session && session.client_id === nextId) return;
  const { path } = currentLocation();
  try {
    stopUploadPoll();
    stopRunPoll();
    clearPendingUpload();
    const result = await api.selectClient(nextId);
    storeToken(result.access_token);
    session = result.session || (await api.session());
    showToast({
      tone: "success",
      title: "Company selected.",
      message: "Upload, process, QA, and publish now use this company.",
    });
    if (path === "/") {
      navigate("/admin");
      return;
    }
    window.dispatchEvent(new Event("dfip:navigate"));
  } catch (error) {
    if (session && canAccessAdmin(session.role) && inspectorClients(session).length) {
      render(companySelectView({ session, error }), path.startsWith("/admin") ? path : "/admin");
      return;
    }
    handleError(error, path);
  }
}

function overviewShellMounted() {
  return Boolean(root && root.querySelector("[data-overview-shell]"));
}

function searchKey(query) {
  const keys = [...new Set([...query.keys()])].sort();
  return keys.map((key) => `${key}=${query.getAll(key).slice().sort().join("\0")}`).join("&");
}

function queryKeysChanged(prev, next, keys) {
  const before = prev instanceof URLSearchParams ? prev : new URLSearchParams();
  const after = next instanceof URLSearchParams ? next : new URLSearchParams();
  for (const key of keys) {
    if (before.getAll(key).slice().sort().join("\0") !== after.getAll(key).slice().sort().join("\0")) return true;
  }
  return false;
}

const OVERVIEW_BUSY_HOSTS = [
  "[data-overview-kpis-host]",
  "[data-overview-trends-host]",
  "[data-overview-explorer-host]",
  "[data-overview-insights-host]",
  "[data-overview-anomalies-host]",
  "[data-overview-drill-host]",
];

function setOverviewHost(selector, content) {
  const host = root && root.querySelector(selector);
  if (!host) return null;
  host.innerHTML = toHtml(content);
  return host;
}

function setOverviewBusy(selector, busy, token) {
  const host = root && root.querySelector(selector);
  if (!host) return;
  if (busy) {
    host.setAttribute("data-overview-busy-token", String(token));
    host.setAttribute("aria-busy", "true");
    host.setAttribute("data-overview-loading", "true");
    return;
  }
  if (token != null && host.getAttribute("data-overview-busy-token") !== String(token)) return;
  host.removeAttribute("aria-busy");
  host.removeAttribute("data-overview-loading");
  host.removeAttribute("data-overview-busy-token");
}

function clearAllOverviewBusy() {
  for (const selector of OVERVIEW_BUSY_HOSTS) {
    setOverviewBusy(selector, false);
  }
}

function readDetailsOpen(selector) {
  const host = root && root.querySelector(selector);
  const details = host && host.querySelector("details");
  return details instanceof HTMLDetailsElement ? details.open : true;
}

function writeDetailsOpen(selector, open) {
  const host = root && root.querySelector(selector);
  const details = host && host.querySelector("details");
  if (details instanceof HTMLDetailsElement) details.open = open;
}

function focusSelector(node) {
  if (!(node instanceof Element) || !root.contains(node)) return "";
  if (node.getAttribute("data-saved-title-input") != null) return "[data-saved-title-input]";
  if (node.getAttribute("data-ask-question-input") != null) return "[data-ask-question-input]";
  if (node.getAttribute("data-ask-ai-input") != null) return "[data-ask-ai-input]";
  if (node.getAttribute("data-ask-ai-open") != null) return "[data-ask-ai-open]";
  if (node.getAttribute("data-generate-trend-input") != null) return "[data-generate-trend-input]";
  if (node.getAttribute("data-generate-trend-open") != null) return "[data-generate-trend-open]";
  const pickerOpen = node.closest("[data-filter-picker-open]");
  if (pickerOpen) {
    const pickerName = pickerOpen.getAttribute("data-filter-picker-open") || "";
    return pickerName ? `[data-filter-picker-open="${pickerName}"]` : "";
  }
  const pickerSearch = node.closest("[data-filter-picker-search]");
  if (pickerSearch) {
    const pickerName = pickerSearch.getAttribute("data-filter-picker-search") || "";
    return pickerName ? `[data-filter-picker-search="${pickerName}"]` : "";
  }
  const kpiCard = node.closest("[data-overview-kpi]");
  if (kpiCard) {
    const kpiId = kpiCard.getAttribute("data-overview-kpi") || "";
    if (kpiId) return `[data-overview-kpi="${kpiId}"]`;
  }
  const name = node.getAttribute("name");
  if (!name) return "";
  const form = node.closest("form");
  if (form && form.dataset.overviewFilters === "true") return `[data-overview-filters] [name="${name}"]`;
  if (form && form.dataset.overviewTrendForm === "true") return `[data-overview-trend-form] [name="${name}"]`;
  if (form && form.dataset.overviewExplorerForm === "true") return `[data-overview-explorer-form] [name="${name}"]`;
  if (form && form.dataset.overviewDrillForm === "true") return `[data-overview-drill-form] [name="${name}"]`;
  if (form && form.dataset.overviewAskForm === "true") return `[data-overview-ask-form] [name="${name}"]`;
  if (form && form.dataset.askAiForm === "true") return `[data-ask-ai-form] [name="${name}"]`;
  if (form && form.dataset.generateTrendForm === "true") return `[data-generate-trend-form] [name="${name}"]`;
  return "";
}

function restoreOverviewFocus(selector) {
  if (!selector) return;
  const node = root && root.querySelector(selector);
  if (node instanceof HTMLElement && typeof node.focus === "function") node.focus();
}

function focusDrillPanel() {
  const panel = root && root.querySelector("[data-drill-panel]");
  if (panel instanceof HTMLElement && typeof panel.focus === "function") {
    panel.focus({ preventScroll: true });
  }
}

function cssEscape(value) {
  if (typeof CSS !== "undefined" && typeof CSS.escape === "function") return CSS.escape(value);
  return String(value).replace(/\\/g, "\\\\").replace(/"/g, '\\"');
}

function rememberKpiDrillReturn(el) {
  if (!el || !el.closest) return;
  const card = el.closest("[data-overview-kpi]");
  if (card && card.classList.contains("overview-kpi")) {
    const kpiId = card.getAttribute("data-overview-kpi") || card.getAttribute("data-overview-drill-kpi") || "";
    overviewDrillReturnFocus = kpiId ? `[data-overview-kpi="${cssEscape(kpiId)}"]` : "";
    return;
  }
  const explorer = el.closest("[data-explorer-drill]");
  if (explorer) {
    const key = explorer.getAttribute("data-explorer-drill") || "";
    overviewDrillReturnFocus = key ? `[data-explorer-drill="${cssEscape(key)}"]` : "";
    return;
  }
  const insight = el.closest("[data-insight-drill]");
  if (insight) {
    const key = insight.getAttribute("data-insight-drill") || "";
    overviewDrillReturnFocus = key ? `[data-insight-drill="${cssEscape(key)}"]` : "";
    return;
  }
  const anomaly = el.closest("[data-anomaly-drill]");
  if (anomaly) {
    const key = anomaly.getAttribute("data-anomaly-drill") || "";
    overviewDrillReturnFocus = key ? `[data-anomaly-drill="${cssEscape(key)}"]` : "";
  }
}

function revealOverviewFilters() {
  const bar = root && root.querySelector("[data-overview-filter-bar]");
  if (!(bar instanceof HTMLElement)) return;
  bar.scrollIntoView({ block: "start", behavior: "smooth" });
  const control = bar.querySelector("select, input, button");
  if (control instanceof HTMLElement) control.focus({ preventScroll: true });
}

let stickyOffsetObserver = null;
let stickyOffsetNode = null;

function observeStickyOffset() {
  const topbar = root && root.querySelector(".topbar");
  if (!(topbar instanceof HTMLElement)) return;
  if (stickyOffsetNode === topbar) {
    applyStickyOffset(topbar);
    return;
  }
  if (stickyOffsetObserver) stickyOffsetObserver.disconnect();
  stickyOffsetNode = topbar;
  applyStickyOffset(topbar);
  if (typeof ResizeObserver !== "function") return;
  stickyOffsetObserver = new ResizeObserver(() => applyStickyOffset(topbar));
  stickyOffsetObserver.observe(topbar);
}

function applyStickyOffset(topbar) {
  const height = Math.round(topbar.getBoundingClientRect().height);
  if (!height) return;
  document.documentElement.style.setProperty("--sticky-offset", `${height}px`);
}

function syncOverviewDrillInert() {
  const open = Boolean(root && root.querySelector("[data-drill-panel]"));
  const chrome = [".skip-link", ".sidebar", ".nav-backdrop", ".topbar"];
  for (const selector of chrome) {
    const node = root && root.querySelector(selector);
    if (!node) continue;
    if (open) node.setAttribute("inert", "");
    else node.removeAttribute("inert");
  }
  const shell = root && root.querySelector("[data-overview-shell]");
  if (!shell) return;
  syncOverviewInertBranch(shell, open);
}

function syncOverviewInertBranch(container, open) {
  for (const child of container.children) {
    if (child.hasAttribute("data-overview-drill-host")) {
      child.removeAttribute("inert");
      continue;
    }
    if (child.querySelector("[data-overview-drill-host]")) {
      child.removeAttribute("inert");
      syncOverviewInertBranch(child, open);
      continue;
    }
    if (open) child.setAttribute("inert", "");
    else child.removeAttribute("inert");
  }
}

function trapOverviewDrillFocus(event) {
  const panel = root && root.querySelector("[data-drill-panel]");
  if (!(panel instanceof HTMLElement)) return false;
  const nodes = [...panel.querySelectorAll("a[href], button:not([disabled]), select:not([disabled]), textarea:not([disabled]), input:not([disabled])")].filter(
    (el) => el.tabIndex >= 0 && !el.hasAttribute("inert"),
  );
  if (!nodes.length) {
    event.preventDefault();
    panel.focus({ preventScroll: true });
    return true;
  }
  const first = nodes[0];
  const last = nodes[nodes.length - 1];
  const active = document.activeElement;
  if (!panel.contains(active)) {
    event.preventDefault();
    (event.shiftKey ? last : first).focus();
    return true;
  }
  if (event.shiftKey && (active === first || active === panel)) {
    event.preventDefault();
    last.focus();
    return true;
  }
  if (!event.shiftKey && active === last) {
    event.preventDefault();
    first.focus();
    return true;
  }
  return false;
}

function rememberOverviewState(query, data) {
  lastOverviewSearch = searchKey(query);
  overviewClientId = (session && session.client_id) || "";
  if (data) overviewKpiData = data;
}

function resetOverviewDrillTrendCache() {
  overviewDrillTrendSeq += 1;
  overviewDrillTrendData = null;
  overviewDrillTrendError = null;
  overviewDrillTrendKey = "";
}

function drillTrendCacheKey(query) {
  return JSON.stringify(drillTrendParamsFromQuery(query, publishedParams({})));
}

function drillComparisonIsNone(query, data) {
  if ((query.get("compare") || "") === "none") return true;
  const comparison = (data && data.comparison) || {};
  return comparison.reason === "comparison_disabled";
}

function trendsMatchDrillContext(trend, query) {
  if (!trend || !trend.selection) return false;
  const params = drillTrendParamsFromQuery(query, publishedParams({}));
  const sel = trend.selection;
  if (sel.metric !== params.metric) return false;
  if (sel.grain !== params.grain) return false;
  if (sel.breakdown) return false;
  if (sel.secondary) return false;
  return true;
}

function paintOverviewDrill(query, { trendLoading, data } = {}) {
  const active = document.activeElement;
  const restoreMode = active && active.closest && active.closest("[data-drill-mode]");
  const restoreClose = active && active.closest && active.closest("[data-drill-panel] [data-drill-close]");
  setOverviewHost(
    "[data-overview-drill-host]",
    overviewDrillPanel({
      query,
      data: data !== undefined ? data : overviewKpiData,
      drill: overviewDrillPayload.drill,
      drillError: overviewDrillPayload.drillError,
      mode: overviewDrillMode,
      trend: overviewDrillTrendData,
      trendError: overviewDrillTrendError,
      trendLoading: Boolean(trendLoading),
    }),
  );
  if (restoreMode) {
    const tab = root && root.querySelector(`[data-drill-mode="${overviewDrillMode}"]`);
    if (tab instanceof HTMLElement) tab.focus({ preventScroll: true });
  } else if (restoreClose) {
    const close = root && root.querySelector("[data-drill-panel] a[data-drill-close], [data-drill-panel] [data-drill-close]");
    if (close instanceof HTMLElement) close.focus({ preventScroll: true });
  }
}

function setOverviewDrillMode(mode) {
  overviewDrillMode = mode === "trend" || mode === "details" ? mode : "breakdown";
  const query = currentLocation().query;
  if (overviewDrillMode === "trend") ensureOverviewDrillTrend(query);
  else paintOverviewDrill(query);
  syncOverviewDrillInert();
  const tab = root && root.querySelector(`[data-drill-mode="${overviewDrillMode}"]`);
  if (tab instanceof HTMLElement) tab.focus({ preventScroll: true });
}

function ensureOverviewDrillTrend(query, { force = false } = {}) {
  const parsed = parseDrillQuery(query);
  if (!parsed) return;
  const key = drillTrendCacheKey(query);
  if (!force && overviewDrillTrendKey === key && (overviewDrillTrendData || overviewDrillTrendError)) {
    paintOverviewDrill(query);
    return;
  }
  if (!force && drillComparisonIsNone(query, overviewKpiData) && overviewSparklineData) {
    const adapted = contextualTrendFromSparkline(overviewSparklineData, parsed.metric);
    if (adapted) {
      overviewDrillTrendData = adapted;
      overviewDrillTrendError = null;
      overviewDrillTrendKey = key;
      paintOverviewDrill(query);
      return;
    }
  }
  if (!force && trendsMatchDrillContext(overviewTrendsData, query)) {
    overviewDrillTrendData = overviewTrendsData;
    overviewDrillTrendError = null;
    overviewDrillTrendKey = key;
    paintOverviewDrill(query);
    return;
  }
  const token = ++overviewDrillTrendSeq;
  overviewDrillTrendKey = key;
  paintOverviewDrill(query, { trendLoading: true });
  api
    .getOverviewTrends(drillTrendParamsFromQuery(query, publishedParams({})))
    .then((body) => {
      if (token !== overviewDrillTrendSeq) return;
      overviewDrillTrendData = body;
      overviewDrillTrendError = null;
      if (overviewShellMounted() && parseDrillQuery(currentLocation().query)) {
        paintOverviewDrill(currentLocation().query);
      }
    })
    .catch((error) => {
      if (token !== overviewDrillTrendSeq) return;
      if (error && (error.status === 401 || error.code === "AUTHENTICATION_FAILED")) {
        handleError(error, currentLocation().path);
        return;
      }
      if (isInactiveCompanyError(error)) {
        handleError(error, currentLocation().path);
        return;
      }
      overviewDrillTrendData = null;
      overviewDrillTrendError = error;
      if (overviewShellMounted() && parseDrillQuery(currentLocation().query)) {
        paintOverviewDrill(currentLocation().query);
      }
    });
}

function applyFilterPickerSearch(picker, needle) {
  if (!picker) return;
  const query = String(needle || "").trim().toLowerCase();
  for (const option of picker.querySelectorAll(".filter-picker-option")) {
    const input = option.querySelector("input");
    const label = option.querySelector("span");
    const hay = `${label ? label.textContent : ""} ${input ? input.value : ""}`.toLowerCase();
    option.hidden = Boolean(query) && !hay.includes(query);
  }
}

function filterPickerVisibleBoxes(picker) {
  return [...picker.querySelectorAll(".filter-picker-option:not([hidden]) input[type='checkbox']")];
}

function closeOverviewFilterPicker(except, { restoreFocus = false } = {}) {
  if (!root) return;
  for (const picker of root.querySelectorAll(".filter-picker.is-open")) {
    if (picker === except) continue;
    picker.classList.remove("is-open");
    const pop = picker.querySelector("[data-filter-picker-popover]");
    if (pop) pop.hidden = true;
    const trigger = picker.querySelector("[data-filter-picker-open]");
    if (trigger) trigger.setAttribute("aria-expanded", "false");
    if (restoreFocus && trigger instanceof HTMLElement) trigger.focus();
  }
}

function positionFilterPickerPopover(picker) {
  const pop = picker && picker.querySelector("[data-filter-picker-popover]");
  if (!(pop instanceof HTMLElement)) return;
  pop.style.left = "0px";
  pop.style.right = "auto";
  const rect = pop.getBoundingClientRect();
  const pad = 8;
  let shift = 0;
  if (rect.right > window.innerWidth - pad) shift = rect.right - (window.innerWidth - pad);
  if (rect.left - shift < pad) shift = rect.left - pad;
  if (shift) pop.style.left = `${-shift}px`;
}

function openOverviewFilterPicker(picker) {
  if (!picker) return;
  closeGenerateTrendPopover();
  closeOverviewFilterPicker(picker);
  picker.classList.add("is-open");
  const pop = picker.querySelector("[data-filter-picker-popover]");
  if (pop) pop.hidden = false;
  const trigger = picker.querySelector("[data-filter-picker-open]");
  if (trigger) trigger.setAttribute("aria-expanded", "true");
  positionFilterPickerPopover(picker);
  const search = picker.querySelector("[data-filter-picker-search]");
  const first = filterPickerVisibleBoxes(picker)[0];
  if (search instanceof HTMLElement) search.focus();
  else if (first instanceof HTMLElement) first.focus();
}

function toggleOverviewFilterPicker(picker) {
  if (!picker) return;
  if (picker.classList.contains("is-open")) closeOverviewFilterPicker(null, { restoreFocus: true });
  else openOverviewFilterPicker(picker);
}

function syncFilterPickerList(form, name, optionsHtml) {
  const picker = form.querySelector(`[data-filter-picker="${name}"]`);
  if (!picker) return;
  const list = picker.querySelector("[data-filter-picker-list]");
  if (list) list.innerHTML = toHtml(optionsHtml);
  const search = picker.querySelector("[data-filter-picker-search]");
  applyFilterPickerSearch(picker, search instanceof HTMLInputElement ? search.value : "");
  syncFilterPickerSummary(picker);
}

function closeGenerateTrendPopover({ restoreFocus = false } = {}) {
  if (!root) return;
  const host = root.querySelector("[data-generate-trend]");
  if (!host) return;
  host.classList.remove("is-open");
  const pop = host.querySelector("[data-generate-trend-popover]");
  if (pop) pop.hidden = true;
  const trigger = host.querySelector("[data-generate-trend-open]");
  if (trigger) trigger.setAttribute("aria-expanded", "false");
  if (restoreFocus && trigger instanceof HTMLElement) trigger.focus();
}

function openGenerateTrendPopover() {
  if (!root) return;
  closeOverviewFilterPicker();
  closeAskAiPopover();
  const host = root.querySelector("[data-generate-trend]");
  if (!host) return;
  host.classList.add("is-open");
  const pop = host.querySelector("[data-generate-trend-popover]");
  if (pop) pop.hidden = false;
  const trigger = host.querySelector("[data-generate-trend-open]");
  if (trigger) trigger.setAttribute("aria-expanded", "true");
  const input = host.querySelector("[data-generate-trend-input]");
  if (input instanceof HTMLElement) input.focus();
}

function toggleGenerateTrendPopover() {
  const host = root && root.querySelector("[data-generate-trend]");
  if (!host) return;
  if (host.classList.contains("is-open")) closeGenerateTrendPopover({ restoreFocus: true });
  else openGenerateTrendPopover();
}

function applyGeneratedTrendSelection(body) {
  const selection = generatedTrendSelectionFromAsk(body);
  if (!selection) return false;
  navigate(overviewHref(withTrendSelection(currentLocation().query, selection)));
  return true;
}

function generateTrendPayloadFromHost(host, query) {
  const input = host.querySelector("[data-generate-trend-input]");
  const typed = String(input && "value" in input ? input.value : "").trim();
  const form = host.querySelector("[data-generate-trend-form]");
  if (form instanceof HTMLFormElement) {
    const payload = askPayloadFromForm(form, query);
    payload.question = typed || payload.question;
    payload.source = payload.source || "overview";
    return payload;
  }
  return generateTrendAskPayload(typed, query);
}

function submitGenerateTrendFrom(el) {
  const host = el && el.closest ? el.closest("[data-generate-trend]") : null;
  if (!host) return;
  const status = host.querySelector("[data-generate-trend-status]");
  const submit = host.querySelector("[data-generate-trend-submit]");
  if (submit && submit.disabled) return;
  const payload = generateTrendPayloadFromHost(host, currentLocation().query);
  if (!payload.question) {
    if (status) {
      status.innerHTML = toHtml(
        html`<p class="banner warn" data-generate-trend-unsupported="true">Enter a trend request.</p>`,
      );
    }
    return;
  }
  if (status) {
    status.innerHTML = toHtml(
      html`<p class="muted" data-generate-trend-loading="true" role="status">Generating…</p>`,
    );
  }
  if (submit) submit.disabled = true;
  api
    .postOverviewAsk(payload)
    .then((body) => {
      if (applyGeneratedTrendSelection(body)) return;
      if (status) status.innerHTML = toHtml(generateTrendStatusView(body));
    })
    .catch((error) => {
      if (isAuthError(error)) {
        handleError(error, currentLocation().path);
        return;
      }
      if (status) status.innerHTML = toHtml(generateTrendStatusView(null, error));
    })
    .finally(() => {
      if (submit) submit.disabled = false;
    });
}

function handleGenerateTrendKeydown(event) {
  const host = event.target.closest?.("[data-generate-trend].is-open") || (root && root.querySelector("[data-generate-trend].is-open"));
  if (event.key === "Escape" && host) {
    event.preventDefault();
    closeGenerateTrendPopover({ restoreFocus: true });
    return true;
  }
  return false;
}

function closeAskAiPopover({ restoreFocus = false } = {}) {
  if (!root) return;
  const host = root.querySelector("[data-ask-ai]");
  if (!host) return;
  host.classList.remove("is-open");
  const pop = host.querySelector("[data-ask-ai-popover]");
  if (pop) pop.hidden = true;
  const trigger = host.querySelector("[data-ask-ai-open]");
  if (trigger) trigger.setAttribute("aria-expanded", "false");
  if (restoreFocus && trigger instanceof HTMLElement) trigger.focus();
}

function openAskAiPopover() {
  if (!root) return;
  closeOverviewFilterPicker();
  closeGenerateTrendPopover();
  const host = root.querySelector("[data-ask-ai]");
  if (!host) return;
  host.classList.add("is-open");
  const pop = host.querySelector("[data-ask-ai-popover]");
  if (pop) pop.hidden = false;
  const trigger = host.querySelector("[data-ask-ai-open]");
  if (trigger) trigger.setAttribute("aria-expanded", "true");
  const input = host.querySelector("[data-ask-ai-input]");
  if (input instanceof HTMLElement) input.focus();
}

function toggleAskAiPopover() {
  const host = root && root.querySelector("[data-ask-ai]");
  if (!host) return;
  if (host.classList.contains("is-open")) closeAskAiPopover({ restoreFocus: true });
  else openAskAiPopover();
}

function askAiPayloadFromHost(host, query) {
  const input = host.querySelector("[data-ask-ai-input]");
  const typed = String(input && "value" in input ? input.value : "").trim();
  const form = host.querySelector("[data-ask-ai-form]");
  if (form instanceof HTMLFormElement) {
    const payload = askPayloadFromForm(form, query);
    payload.question = typed || payload.question;
    payload.source = payload.source || "overview";
    return payload;
  }
  return generateTrendAskPayload(typed, query);
}

function submitAskAiFrom(el) {
  const host = el && el.closest ? el.closest("[data-ask-ai]") : null;
  if (!host) return;
  const result = host.querySelector("[data-ask-ai-result]");
  const submit = host.querySelector("[data-ask-ai-submit]");
  if (submit && submit.disabled) return;
  const payload = askAiPayloadFromHost(host, currentLocation().query);
  if (!payload.question) {
    if (result) {
      result.innerHTML = toHtml(
        html`<p class="banner warn" data-ask-unsupported="true">Enter a question.</p>`,
      );
    }
    return;
  }
  if (result) result.innerHTML = toHtml(html`<div data-ask-loading="true">${loadingState()}</div>`);
  if (submit) submit.disabled = true;
  api
    .postOverviewAsk(payload)
    .then((body) => {
      if (result) result.innerHTML = toHtml(overviewAskResultView(body));
      applyGeneratedTrendSelection(body);
    })
    .catch((error) => {
      if (isAuthError(error)) {
        handleError(error, currentLocation().path);
        return;
      }
      if (result) result.innerHTML = toHtml(overviewAskResultView(null, error));
    })
    .finally(() => {
      if (submit) submit.disabled = false;
    });
}

function handleAskAiKeydown(event) {
  const host = event.target.closest?.("[data-ask-ai].is-open") || (root && root.querySelector("[data-ask-ai].is-open"));
  if (event.key === "Escape" && host) {
    event.preventDefault();
    closeAskAiPopover({ restoreFocus: true });
    return true;
  }
  return false;
}

function handleOverviewFilterPickerKeydown(event) {
  const openPicker = event.target.closest?.(".filter-picker.is-open") || (root && root.querySelector(".filter-picker.is-open"));
  if (event.key === "Escape" && openPicker) {
    event.preventDefault();
    closeOverviewFilterPicker(null, { restoreFocus: true });
    return true;
  }
  const picker = event.target.closest?.("[data-filter-picker]");
  if (!picker) return false;
  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    event.preventDefault();
    if (!picker.classList.contains("is-open")) openOverviewFilterPicker(picker);
    const boxes = filterPickerVisibleBoxes(picker);
    if (!boxes.length) return true;
    const current = event.target.matches?.("input[type='checkbox']") ? event.target : null;
    const delta = event.key === "ArrowDown" ? 1 : -1;
    const index = boxes.indexOf(current);
    const next = index < 0 ? (delta > 0 ? 0 : boxes.length - 1) : (index + delta + boxes.length) % boxes.length;
    boxes[next].focus();
    return true;
  }
  return false;
}

function syncOverviewFilterBar(form, data, query) {
  const model = overviewFilterPatchModel(data, query);
  form.dataset.latestMonth = model.latest;
  const period = form.elements.period;
  const month = form.elements.month_start;
  const dayFrom = form.elements.day_from;
  const dayTo = form.elements.day_to;
  const compare = form.elements.compare;
  const compareMonth = form.elements.compare_month_start;
  if (period instanceof HTMLSelectElement) period.value = model.grain === "range" || model.grain === "all_history" ? model.grain : "month";
  if (month instanceof HTMLSelectElement) {
    month.innerHTML = toHtml(model.monthOptions);
    month.disabled = model.grain !== "month";
  }
  if (dayFrom instanceof HTMLInputElement) {
    dayFrom.disabled = model.grain !== "range";
    dayFrom.value = model.applied.day_from || model.options.published_day_min || "";
    dayFrom.min = model.options.published_day_min || "";
    dayFrom.max = model.options.published_day_max || "";
  }
  if (dayTo instanceof HTMLInputElement) {
    dayTo.disabled = model.grain !== "range";
    dayTo.value = model.applied.day_to || model.options.published_day_max || "";
    dayTo.min = model.options.published_day_min || "";
    dayTo.max = model.options.published_day_max || "";
  }
  if (compare instanceof HTMLSelectElement) compare.value = model.compare === "none" ? "none" : "auto";
  if (compareMonth instanceof HTMLSelectElement) {
    compareMonth.innerHTML = toHtml(model.compareMonthOptions);
    compareMonth.disabled = false;
  }
  syncFilterPickerList(form, "channel", model.channelOptions);
  syncFilterPickerList(form, "filter_logic_1", model.logic1Options);
  syncFilterPickerList(form, "filter_logic_1_group", model.logic1GroupOptions);
  syncFilterPickerList(form, "campaign_id", model.campaignOptions);
  const chips = form.querySelector("[data-overview-active-filters]");
  if (chips) chips.outerHTML = toHtml(model.chipsHtml);
  const dropped = form.querySelector("[data-overview-dropped]");
  if (model.droppedKeys.length) {
    const text = `Some selections are not in this period and were cleared: ${model.droppedKeys.join(", ")}.`;
    if (dropped) dropped.textContent = text;
    else form.insertAdjacentHTML("beforeend", toHtml(html`<p class="banner warn" data-overview-dropped="true">${text}</p>`));
  } else if (dropped) {
    dropped.remove();
  }
}

function patchAskFocusHidden(form, query) {
  if (!(form instanceof HTMLFormElement)) return;
  const parsed = parseDrillQuery(query);
  const setHidden = (name, value) => {
    const input = form.elements[name];
    if (input) input.value = value || "";
  };
  setHidden("source", parsed ? "drill" : query.get("insight") ? "insight" : query.get("anomaly") ? "anomaly" : query.get("kpi") ? "kpi" : "overview");
  setHidden("metric", query.get("kpi") || query.get("trend_metric") || query.get("ex_metric") || "");
  setHidden("dimension", query.get("ex_dimension") || query.get("drill_dimension") || "");
  setHidden("insight_id", query.get("insight") || "");
  setHidden("anomaly_id", query.get("anomaly") || "");
  setHidden("trend_metric", query.get("trend_metric") || "");
  setHidden("trend_secondary", query.get("trend_secondary") || "");
  setHidden("trend_grain", query.get("trend_grain") || "");
  setHidden("trend_breakdown", query.get("trend_breakdown") || "");
  setHidden("explorer_metric", query.get("ex_metric") || "");
  setHidden("explorer_dimension", query.get("ex_dimension") || "");
  setHidden("explorer_mode", query.get("ex_mode") || "");
  setHidden("drill_origin", parsed ? parsed.origin : "");
  setHidden("drill_metric", parsed ? parsed.metric : "");
  setHidden("drill_dimension", parsed ? parsed.dimension : "");
  setHidden("drill_parents", parsed && parsed.parents ? parsed.parents.join("|") : "");
}

function patchOverviewAsk(query, data) {
  const context = root && root.querySelector("[data-ask-context]");
  if (context) {
    context.innerHTML = toHtml(data ? overviewAskContextLine({ session, data }) : kpiContextUnavailable());
  }
  if (!root) return;
  patchAskFocusHidden(root.querySelector("[data-overview-ask-form]"), query);
  patchAskFocusHidden(root.querySelector("[data-ask-ai-form]"), query);
}

function throwIfOverviewJobAuth(name, error) {
  if (!error) return;
  if (error.status === 401 || error.code === "AUTHENTICATION_FAILED") throw error;
  if (isInactiveCompanyError(error)) throw error;
  if (name === "saved" || name === "drill") return;
  if (isAuthError(error)) throw error;
}

function paintOverviewKpis(query, data, error) {
  const host = root.querySelector("[data-overview-kpis-host]");
  if (!host) return;
  if (error) {
    host.innerHTML = toHtml(overviewSectionRetry("kpis", error));
    return;
  }
  if (!data || !data.has_published_history) {
    host.innerHTML = toHtml(html`<p class="muted">No published history is available for this company.</p>`);
    return;
  }
  host.innerHTML = toHtml(overviewKpiCardsHtml({ data, query, sparkline: overviewSparklineData }));
}

function paintOverviewSparklines(query) {
  const host = root.querySelector("[data-overview-kpis-host]");
  if (!host || host.querySelector("[data-overview-section-error]")) return;
  if (!overviewKpiData || !overviewKpiData.has_published_history) return;
  host.innerHTML = toHtml(overviewKpiCardsHtml({ data: overviewKpiData, query, sparkline: overviewSparklineData }));
}

function paintOverviewFilters(data, keepForm, query) {
  const bar = root.querySelector("[data-overview-filter-bar]");
  const compact = root.querySelector("[data-overview-filter-compact-host]");
  if (compact) compact.innerHTML = toHtml(overviewFilterCompactBar({ data, query }));
  const form = bar && bar.querySelector("[data-overview-filters]");
  if (keepForm && form instanceof HTMLFormElement) {
    syncOverviewFilterBar(form, data, query);
    return;
  }
  if (bar) bar.innerHTML = toHtml(overviewFilterBar(data, query));
}

function clearOverviewAskResult() {
  const host = root && root.querySelector("[data-ask-result]");
  if (host) {
    host.innerHTML = toHtml(overviewAskEmptyResult());
    host.setAttribute("data-ask-cleared", "true");
  }
  const askAi = root && root.querySelector("[data-ask-ai-result]");
  if (askAi) {
    askAi.innerHTML = "";
    askAi.setAttribute("data-ask-cleared", "true");
  }
}

function kpiContextUnavailable() {
  return html`<p class="muted" data-overview-kpi-context-error="true">Period and comparison context is unavailable until KPI Overview reloads.</p>`;
}

async function refreshOverviewInPlace(query, { force = [] } = {}) {
  const token = ++overviewRefreshSeq;
  const previousQuery = refreshOverviewInPlace.previousQuery instanceof URLSearchParams
    ? refreshOverviewInPlace.previousQuery
    : new URLSearchParams();
  const scrollY = window.scrollY;
  const activeSelector = focusSelector(document.activeElement);
  const form = root.querySelector("[data-overview-filters]");
  const formFocused = form instanceof HTMLFormElement && form.contains(document.activeElement);
  const forced = new Set(force);
  const filterChanged = queryKeysChanged(previousQuery, query, [...ANALYTICS_SINGLE, ...ANALYTICS_MULTI]);
  const trendChanged = queryKeysChanged(previousQuery, query, TREND_KEYS);
  const findingChanged = queryKeysChanged(previousQuery, query, FINDING_KEYS);
  const explorerChanged = queryKeysChanged(previousQuery, query, EXPLORER_KEYS);
  const drillChanged = queryKeysChanged(previousQuery, query, [...DRILL_SINGLE, ...DRILL_MULTI]);
  const focusChanged = queryKeysChanged(previousQuery, query, FOCUS_KEYS);
  const savedChanged = (previousQuery.get("saved") || "") !== (query.get("saved") || "");
  const identical = searchKey(previousQuery) === searchKey(query) && lastOverviewSearch !== "";
  const drillQuery = parseDrillQuery(query);
  const hadDrill = Boolean(parseDrillQuery(previousQuery));
  if (drillQuery && !hadDrill) {
    overviewDrillMode = "breakdown";
    resetOverviewDrillTrendCache();
    overviewDrillPayload = { drill: null, drillError: null };
  }
  if (filterChanged) resetOverviewDrillTrendCache();
  const needKpis = forced.has("kpis") || forced.has("all") || filterChanged;
  const needSparklines = forced.has("sparklines") || forced.has("all") || filterChanged;
  const needTrends = forced.has("trends") || forced.has("all") || filterChanged || trendChanged;
  const needExplorer = forced.has("explorer") || forced.has("all") || filterChanged || explorerChanged;
  const needInsights = forced.has("insights") || forced.has("all") || filterChanged || findingChanged;
  const needAnomalies = forced.has("anomalies") || forced.has("all") || filterChanged || findingChanged;
  const needDrill = Boolean(drillQuery) && (forced.has("drill") || forced.has("all") || drillChanged || filterChanged);
  const closeDrill = !drillQuery && (drillChanged || forced.has("drill"));
  const needSaved = overviewSavedDirty || identical || (savedChanged && Boolean(query.get("saved")) && !root.querySelector(`[data-saved-id="${query.get("saved")}"]`));
  overviewSavedDirty = false;
  const askGroundingChanged = filterChanged || drillChanged || trendChanged || explorerChanged;

  const stillCurrent = () => token === overviewRefreshSeq && overviewShellMounted();

  const trendOpen = readDetailsOpen("[data-overview-trends-host]");
  const explorerOpen = readDetailsOpen("[data-overview-explorer-host]");
  const insightsOpen = readDetailsOpen("[data-overview-insights-host]");
  const anomaliesOpen = readDetailsOpen("[data-overview-anomalies-host]");
  const askOpen = readDetailsOpen("[data-overview-ask-host]");

  if (needKpis) setOverviewBusy("[data-overview-kpis-host]", true, token);
  if (needTrends) setOverviewBusy("[data-overview-trends-host]", true, token);
  if (needExplorer) setOverviewBusy("[data-overview-explorer-host]", true, token);
  if (needInsights) setOverviewBusy("[data-overview-insights-host]", true, token);
  if (needAnomalies) setOverviewBusy("[data-overview-anomalies-host]", true, token);
  if (needDrill) {
    const drillHost = root.querySelector("[data-overview-drill-host]");
    const existing = drillHost && drillHost.querySelector(".drill-root, [data-overview-drill='true']");
    if (drillHost && !existing) {
      drillHost.innerHTML = toHtml(
        overviewDrillPanel({
          query,
          data: overviewKpiData,
          drill: null,
          drillError: null,
          mode: overviewDrillMode,
          trend: overviewDrillTrendData,
          trendError: overviewDrillTrendError,
        }),
      );
    } else if (existing) {
      const content = drillHost.querySelector("[data-drill-content]");
      if (content) {
        content.setAttribute("data-drill-loading", "true");
        content.setAttribute("aria-busy", "true");
        content.innerHTML = toHtml(overviewHostLoading());
      }
    }
  }

  const params = overviewParamsFromQuery(query, publishedParams({}));
  const jobs = [];
  if (needKpis) jobs.push(["kpis", api.getOverviewKpis(params)]);
  if (needSparklines) jobs.push(["sparklines", api.getOverviewKpiSparklines(sparklineParamsFromQuery(query, publishedParams({})))]);
  if (needTrends) jobs.push(["trends", api.getOverviewTrends(params)]);
  if (needExplorer) jobs.push(["explorer", api.getOverviewExplorer(explorerParamsFromQuery(query, publishedParams({})))]);
  if (needInsights) jobs.push(["insights", api.getOverviewInsights(insightsParamsFromQuery(query, publishedParams({})))]);
  if (needAnomalies) jobs.push(["anomalies", api.getOverviewAnomalies(anomaliesParamsFromQuery(query, publishedParams({})))]);
  if (needDrill) jobs.push(["drill", api.getOverviewDrilldown(drillParamsFromQuery(query, publishedParams({})))]);
  if (needSaved) jobs.push(["saved", api.listSavedAnalyses()]);

  const results = {};
  if (jobs.length) {
    const settled = await Promise.allSettled(jobs.map((item) => item[1]));
    for (let i = 0; i < jobs.length; i += 1) {
      const name = jobs[i][0];
      const item = settled[i];
      if (item.status === "fulfilled") results[name] = { value: item.value, error: null };
      else {
        throwIfOverviewJobAuth(name, item.reason);
        results[name] = { value: null, error: item.reason };
      }
    }
  }
  if (!stillCurrent()) return;
  clearAllOverviewBusy();

  let data = overviewKpiData;
  let kpiFailed = false;
  if (needSparklines) {
    const spark = results.sparklines || {};
    overviewSparklineData = spark.error ? null : spark.value;
  }
  if (needKpis) {
    const kpi = results.kpis || {};
    if (kpi.error) {
      kpiFailed = true;
      data = null;
      overviewKpiData = null;
      paintOverviewKpis(query, null, kpi.error);
      setOverviewHost("[data-overview-period-host]", kpiContextUnavailable());
      setOverviewHost("[data-overview-comparison-host]", kpiContextUnavailable());
      setOverviewHost("[data-overview-context-host]", workspaceContextBar({ session, data: null, query }));
    } else {
      data = kpi.value;
      overviewKpiData = data;
      paintOverviewKpis(query, data, null);
      setOverviewHost("[data-overview-period-host]", overviewPeriodLine(data));
      setOverviewHost("[data-overview-comparison-host]", overviewComparisonBanner(data));
      paintOverviewFilters(data, formFocused, query);
      setOverviewHost("[data-overview-context-host]", workspaceContextBar({ session, data, query }));
    }
  } else if (needSparklines && !kpiFailed) {
    paintOverviewSparklines(query);
  } else if (focusChanged || explorerChanged || savedChanged) {
    setOverviewHost("[data-overview-context-host]", workspaceContextBar({ session, data: overviewKpiData, query }));
    const kpiHost = root.querySelector("[data-overview-kpis-host]");
    if (kpiHost && overviewKpiData && !kpiHost.querySelector("[data-overview-section-error]")) {
      kpiHost.innerHTML = toHtml(overviewKpiCardsHtml({ data: overviewKpiData, query, sparkline: overviewSparklineData }));
    }
  }

  const siblingContext = kpiFailed ? null : data || overviewKpiData;

  if (needTrends) {
    const trend = results.trends || {};
    overviewTrendsData = trend.error ? null : trend.value;
    setOverviewHost(
      "[data-overview-trends-host]",
      trend.error && trend.error.status !== 422
        ? overviewSectionRetry("trends", trend.error)
        : overviewTrendSection({ query, trend: trend.value, trendError: trend.error }),
    );
    writeDetailsOpen("[data-overview-trends-host]", trendOpen);
  }
  if (needExplorer) {
    const explorer = results.explorer || {};
    setOverviewHost(
      "[data-overview-explorer-host]",
      explorer.error && explorer.error.status !== 422
        ? overviewSectionRetry("explorer", explorer.error)
        : overviewExplorerSection({
            query,
            data: siblingContext,
            explorer: explorer.value,
            explorerError: explorer.error,
          }),
    );
    writeDetailsOpen("[data-overview-explorer-host]", explorerOpen);
  }
  if (needInsights) {
    const insights = results.insights || {};
    setOverviewHost(
      "[data-overview-insights-host]",
      insights.error && insights.error.status !== 422
        ? overviewSectionRetry("insights", insights.error)
        : overviewInsightsSection({
            query,
            data: siblingContext,
            insights: insights.value,
            insightsError: insights.error,
          }),
    );
    writeDetailsOpen("[data-overview-insights-host]", insightsOpen);
  }
  if (needAnomalies) {
    const anomalies = results.anomalies || {};
    setOverviewHost(
      "[data-overview-anomalies-host]",
      anomalies.error && anomalies.error.status !== 422
        ? overviewSectionRetry("anomalies", anomalies.error)
        : overviewAnomaliesSection({
            query,
            data: siblingContext,
            anomalies: anomalies.value,
            anomaliesError: anomalies.error,
          }),
    );
    writeDetailsOpen("[data-overview-anomalies-host]", anomaliesOpen);
  }
  if (needDrill) {
    const drill = results.drill || {};
    overviewDrillPayload = { drill: drill.value, drillError: drill.error };
    paintOverviewDrill(query, { data: siblingContext });
    if (overviewDrillMode === "trend") ensureOverviewDrillTrend(query);
  } else if (closeDrill) {
    overviewDrillMode = "breakdown";
    resetOverviewDrillTrendCache();
    overviewDrillPayload = { drill: null, drillError: null };
    setOverviewHost("[data-overview-drill-host]", "");
  }
  if (needSaved) {
    const saved = results.saved || { value: { items: [] }, error: null };
    setOverviewHost(
      "[data-overview-saved-host]",
      workspaceSavedPanel({ saved: saved.value || { items: [] }, savedError: saved.error, query }),
    );
  } else if (savedChanged) {
    const items = root.querySelectorAll("[data-saved-id]");
    const loaded = query.get("saved") || "";
    for (const item of items) {
      item.classList.toggle("is-selected", item.getAttribute("data-saved-id") === loaded);
    }
  }

  if (askGroundingChanged) clearOverviewAskResult();
  patchOverviewAsk(query, siblingContext);
  writeDetailsOpen("[data-overview-ask-host]", askOpen);
  rememberOverviewState(query, siblingContext);
  refreshOverviewInPlace.previousQuery = new URLSearchParams(query);
  window.scrollTo(0, scrollY);
  syncOverviewDrillInert();
  if (needDrill) {
    focusDrillPanel();
  } else {
    const returnToKpi = closeDrill ? overviewDrillReturnFocus : "";
    if (closeDrill) overviewDrillReturnFocus = "";
    restoreOverviewFocus(returnToKpi || activeSelector);
  }
}

async function renderRoute() {
  stopUploadPoll();
  stopRunPoll();
  const { path, query } = currentLocation();
  if (path === "/sign-out") {
    try {
      if (getStoredToken()) {
        await api.logout();
      }
    } catch (_error) {
      // Local session is still cleared below.
    }
    clearToken();
    session = null;
    navigate("/");
    return;
  }
  const matched = matchRoute(path, routes);
  if (!matched) {
    render(notFoundView(), path);
    return;
  }
  const { route, params } = matched;
  try {
    if (route.name === "credential") {
      if (getStoredToken()) {
        try {
          await loadSession();
          if (session) {
            if (canAccessAdmin(session.role)) {
              navigate("/admin");
              return;
            }
            navigate("/client/overview");
            return;
          }
        } catch (error) {
          clearToken();
          session = null;
        }
      }
      root.innerHTML = toHtml(await viewFor(route.name, { path, query, params }));
      return;
    }
    if (
      route.name === "client-overview" &&
      overviewShellMounted() &&
      session &&
      getStoredToken() &&
      session.client_id === overviewClientId
    ) {
      try {
        await refreshOverviewInPlace(query);
        return;
      } catch (error) {
        if (!isInactiveCompanyError(error)) throw error;
        overviewClientId = "";
      }
    }
    overviewRefreshSeq += 1;
    if (route.name !== "client-overview") {
      lastOverviewSearch = "";
      overviewClientId = "";
      overviewKpiData = null;
      refreshOverviewInPlace.previousQuery = null;
    }
    if (route.access !== "public") {
      await loadSession();
      if (!session) {
        render(credentialView({ nextPath: `${path}${window.location.search}` }), "/");
        return;
      }
      if (route.access === "admin" && !canAccessAdmin(session.role)) {
        render(unauthorizedView(), path);
        return;
      }
      if (
        needsCompanySelection(session) &&
        !allowsInactiveCompanyRoute(route.name) &&
        (route.access === "admin" || route.access === "client")
      ) {
        render(companySelectView({ session }), path);
        return;
      }
      if (route.access === "client" && !canAccessClient(session.role)) {
        render(unauthorizedView(), path);
        return;
      }
    }
    if (route.name !== "credential") {
      render(loadingState(), path);
    }
    const body = await viewFor(route.name, { path, query, params });
    if (route.access === "public" && route.name === "credential") {
      root.innerHTML = toHtml(body);
      return;
    }
    render(body, path);
    if (route.name === "client-overview") {
      observeStickyOffset();
      syncOverviewDrillInert();
      const drillQuery = parseDrillQuery(query);
      if (drillQuery) {
        const kpiId = query.get("kpi") || "";
        if (kpiId && !overviewDrillReturnFocus) overviewDrillReturnFocus = `[data-overview-kpi="${cssEscape(kpiId)}"]`;
        focusDrillPanel();
      }
    }
    if (route.name === "admin-upload") {
      resumePendingUploadPoll();
    }
    if (route.name === "admin-run" && body) {
      resumePendingRunPoll(params.id);
    }
    if (route.name === "admin-batch" && params.id) {
      resumeBatchProgressPoll(params.id);
    }
  } catch (error) {
    handleError(error, path);
  }
}

async function viewFor(name, ctx) {
  const { query, params } = ctx;
  const paging = pageParams(query);
  const filters = { ...queryObject(query), ...paging };
  const scoped = clientScoped({});
  if (name === "credential") {
    let setupRequired = false;
    try {
      const status = await api.setupStatus();
      setupRequired = Boolean(status && status.publisher_setup_required);
    } catch (_error) {
      setupRequired = false;
    }
    return credentialView({
      nextPath: query.get("next") || "/client/overview",
      setupRequired,
      setupDone: publisherSetupDone && !setupRequired,
    });
  }
  if (name === "unauthorized") return unauthorizedView();
  if (name === "admin-companies") {
    const page = await api.listClients({ include_inactive: true });
    return companiesView({ session, companies: page.items || [] });
  }
  if (name === "admin-home") {
    const health = await safeRead(() => api.health());
    const ready = await safeRead(() => api.ready());
    const runBundle = await latestBundle((opts) => api.listProcessingRuns(opts));
    const fileBundle = await latestBundle((opts) => api.listSourceFiles(opts));
    const currentPublication = await safeRead(() => api.getCurrentPublication(scoped));
    const publications = await safeRead(() => api.listPublications({ ...scoped, limit: 8, offset: 0 }));
    const facts = await safeRead(() => api.listFacts(clientScoped({ limit: 1, offset: 0 })));
    const findings = runBundle.latest
      ? await safeRead(() => api.listQaFindings(runBundle.latest.processing_run_id, { limit: 1, offset: 0 }))
      : null;
    return adminHomeView({
      health,
      ready,
      session,
      runs: runBundle.recent,
      latestRun: runBundle.latest,
      currentPublication,
      publications,
      files: fileBundle.recent,
      facts,
      findings,
      loading: false,
    });
  }
  if (name === "admin-upload") {
    const scope = clientScoped({ client_id: query.get("client_id") || undefined });
    const logicPage = scope.client_id
      ? await safeRead(() => api.listCatalogs("logic", scope))
      : null;
    const labelsPage = scope.client_id
      ? await safeRead(() => api.listCatalogs("labels", scope))
      : null;
    const pending = loadPendingUpload();
    let uploadResult = null;
    if (pending && pending.batchId) {
      try {
        uploadResult = await fetchUploadSnapshot(pending);
        if (isTerminalUpload(uploadResult)) {
          clearPendingUpload();
        }
      } catch (error) {
        if (isAuthError(error)) throw error;
      }
    }
    return uploadCenterView(
      withUploadLimit({ session, query, logicPage, labelsPage, uploadResult, loading: false }),
    );
  }
  if (name === "admin-source-files") {
    const page = await api.listSourceFiles(filters);
    return sourceFileListView({ page, query, loading: false });
  }
  if (name === "admin-source-file") {
    const item = await api.getSourceFile(params.id);
    return sourceFileDetailView({ item, loading: false });
  }
  if (name === "admin-batches") {
    const page = await api.listBatches(filters);
    return batchListView({ page, query, loading: false });
  }
  if (name === "admin-batch") {
    const item = await api.getBatch(params.id);
    const rows = await api.listStagedRows(params.id, filters);
    return batchDetailView({ item, rows, query, loading: false });
  }
  if (name === "admin-runs") {
    const page = await api.listProcessingRuns(filters);
    return runListView({ page, query, loading: false });
  }
  if (name === "admin-run") {
    const item = await api.getProcessingRun(params.id);
    const batch = await safeRead(() => api.getBatch(item.batch_id));
    const findings = await safeRead(() => api.listQaFindings(params.id, { limit: 200, offset: 0 }));
    const facts = await safeRead(() =>
      api.listFacts(clientScoped({ processing_run_id: params.id, limit: 1, offset: 0 })),
    );
    const currentPublication = await safeRead(() => api.getCurrentPublication(scoped));
    const catalogScope = clientScoped({
      client_id: (batch && batch.client_id) || query.get("client_id") || undefined,
    });
    const logicVersion =
      item.campaign_label_version_id && catalogScope.client_id
        ? await safeRead(() =>
            api.getCatalogVersion("logic", item.campaign_label_version_id, catalogScope),
          )
        : null;
    const labelsVersion =
      item.label_group_version_id && catalogScope.client_id
        ? await safeRead(() =>
            api.getCatalogVersion("labels", item.label_group_version_id, catalogScope),
          )
        : null;
    return runDetailView({
      item,
      batch,
      findings,
      facts,
      currentPublication,
      session,
      logicVersion,
      labelsVersion,
      loading: false,
    });
  }
  if (name === "admin-review") {
    const runId = query.get("processing_run_id");
    if (!runId) {
      const runs = await api.listProcessingRuns({ limit: 50, offset: 0 });
      return reviewView({ runs, session, loading: false });
    }
    const item = await api.getProcessingRun(runId);
    const findings = await safeRead(() => api.listQaFindings(runId, { limit: 200, offset: 0 }));
    const facts = await safeRead(() =>
      api.listFacts(clientScoped({ processing_run_id: runId, limit: 10, offset: 0 })),
    );
    const currentPublication = await safeRead(() => api.getCurrentPublication(scoped));
    return reviewView({ item, findings, facts, currentPublication, session, loading: false });
  }
  if (name === "admin-facts") {
    const page = await api.listFacts(clientScoped(filters));
    return factListView({ page, query, loading: false });
  }
  if (name === "admin-fact-detail") {
    const item = await loadFact(query);
    return factDetailView({ item, loading: false });
  }
  if (name === "admin-history") {
    const page = await api.listFactHistory(clientScoped(filters));
    return historyListView({ page, query, loading: false });
  }
  if (name === "admin-logic" || name === "admin-labels" || name === "admin-catalogs") {
    const kind =
      name === "admin-labels" || query.get("kind") === "labels" ? "labels" : "logic";
    const scope = clientScoped({ client_id: query.get("client_id") || undefined });
    let page = { items: [], processing_active: null, packaged_fallback: null, kind };
    let error = null;
    if (scope.client_id) {
      try {
        page = await api.listCatalogs(kind, scope);
      } catch (err) {
        if (isAuthError(err)) throw err;
        error = err;
      }
    }
    let selected = null;
    if (query.get("version") && scope.client_id) {
      selected = await safeRead(() => api.getCatalogVersion(kind, query.get("version"), scope));
    }
    return catalogView({ kind, page, selected, session, query, error, loading: false });
  }
  if (name === "admin-publications") {
    const runs = await api.listProcessingRuns({ status: "succeeded", limit: 200, offset: 0 });
    const currentPublication = await safeRead(() => api.getCurrentPublication(scoped));
    const publications = await api.listPublications({ ...scoped, ...paging });
    const selectedId = query.get("processing_run_id");
    const selectedRun = selectedId
      ? itemsFind(runs, selectedId) || (await safeRead(() => api.getProcessingRun(selectedId)))
      : null;
    const findings = selectedRun
      ? await safeRead(() =>
          api.listQaFindings(selectedRun.processing_run_id, { limit: 1, offset: 0 }),
        )
      : null;
    const facts = selectedRun
      ? await safeRead(() =>
          api.listFacts(
            clientScoped({ processing_run_id: selectedRun.processing_run_id, limit: 1, offset: 0 }),
          ),
        )
      : null;
    return publicationsView({
      session,
      runs,
      currentPublication,
      publications,
      findings,
      facts,
      selectedRun,
      loading: false,
    });
  }
  if (name === "admin-downloads") {
    const scope = clientScoped({ client_id: query.get("client_id") || undefined });
    const logicPage = scope.client_id
      ? await safeRead(() => api.listCatalogs("logic", scope))
      : null;
    const labelsPage = scope.client_id
      ? await safeRead(() => api.listCatalogs("labels", scope))
      : null;
    const currentPublication = await safeRead(() => api.getCurrentPublication(scope));
    return downloadsView({
      session,
      query,
      logicPage,
      labelsPage,
      currentPublication,
      loading: false,
    });
  }
  if (name === "client-overview") {
    try {
      const params = overviewParamsFromQuery(query, publishedParams({}));
      const jobs = [
        api.getOverviewKpis(params),
        api.getOverviewTrends(params),
        api.getOverviewExplorer(explorerParamsFromQuery(query, publishedParams({}))),
        api.getOverviewInsights(insightsParamsFromQuery(query, publishedParams({}))),
        api.getOverviewAnomalies(anomaliesParamsFromQuery(query, publishedParams({}))),
        api.listSavedAnalyses(),
        api.getOverviewKpiSparklines(sparklineParamsFromQuery(query, publishedParams({}))),
      ];
      const drillQuery = parseDrillQuery(query);
      if (drillQuery) jobs.push(api.getOverviewDrilldown(drillParamsFromQuery(query, publishedParams({}))));
      const results = await Promise.allSettled(jobs);
      const [kpiResult, trendResult, explorerResult, insightsResult, anomaliesResult, savedResult, sparklineResult, drillResult] = results;
      if (kpiResult.status === "rejected") {
        if (isAuthError(kpiResult.reason)) throw kpiResult.reason;
        return clientOverviewView({ session, error: kpiResult.reason, loading: false, query });
      }
      let trend = null;
      let trendError = null;
      if (trendResult.status === "fulfilled") {
        trend = trendResult.value;
      } else {
        if (isAuthError(trendResult.reason)) throw trendResult.reason;
        trendError = trendResult.reason;
      }
      let explorer = null;
      let explorerError = null;
      if (explorerResult.status === "fulfilled") {
        explorer = explorerResult.value;
      } else {
        if (isAuthError(explorerResult.reason)) throw explorerResult.reason;
        explorerError = explorerResult.reason;
      }
      let insights = null;
      let insightsError = null;
      if (insightsResult.status === "fulfilled") {
        insights = insightsResult.value;
      } else {
        if (isAuthError(insightsResult.reason)) throw insightsResult.reason;
        insightsError = insightsResult.reason;
      }
      let anomalies = null;
      let anomaliesError = null;
      if (anomaliesResult.status === "fulfilled") {
        anomalies = anomaliesResult.value;
      } else {
        if (isAuthError(anomaliesResult.reason)) throw anomaliesResult.reason;
        anomaliesError = anomaliesResult.reason;
      }
      let saved = { items: [] };
      let savedError = null;
      if (savedResult && savedResult.status === "fulfilled") {
        saved = savedResult.value;
      } else if (savedResult && savedResult.status === "rejected") {
        if (savedResult.reason instanceof ApiError && savedResult.reason.status === 401) throw savedResult.reason;
        savedError = savedResult.reason;
      }
      let drill = null;
      let drillError = null;
      if (drillQuery) {
        if (drillResult.status === "fulfilled") drill = drillResult.value;
        else {
          if (drillResult.reason instanceof ApiError && drillResult.reason.status === 401) throw drillResult.reason;
          drillError = drillResult.reason;
        }
      }
      let sparkline = null;
      if (sparklineResult && sparklineResult.status === "fulfilled") {
        sparkline = sparklineResult.value;
      } else if (sparklineResult && sparklineResult.status === "rejected") {
        if (isAuthError(sparklineResult.reason)) throw sparklineResult.reason;
        sparkline = null;
      }
      overviewSparklineData = sparkline;
      overviewTrendsData = trendError ? null : trend;
      overviewDrillPayload = { drill, drillError };
      if (!drillQuery) {
        overviewDrillMode = "breakdown";
        resetOverviewDrillTrendCache();
      }
      rememberOverviewState(query, kpiResult.value);
      refreshOverviewInPlace.previousQuery = new URLSearchParams(query);
      return clientOverviewView({
        session,
        data: kpiResult.value,
        trend,
        trendError,
        explorer,
        explorerError,
        insights,
        insightsError,
        anomalies,
        anomaliesError,
        drill,
        drillError,
        drillMode: overviewDrillMode,
        drillTrend: overviewDrillTrendData,
        drillTrendError: overviewDrillTrendError,
        saved,
        savedError,
        sparkline,
        loading: false,
        query,
      });
    } catch (error) {
      if (isAuthError(error)) throw error;
      return clientOverviewView({ session, error, loading: false, query });
    }
  }
  if (name === "client-home") {
    const currentPublication = await safeRead(() => api.getCurrentPublication(publishedParams({})));
    const publications = await safeRead(() =>
      api.listPublications({ ...publishedParams({}), limit: 50, offset: 0 }),
    );
    return clientHomeView({ session, currentPublication, publications });
  }
  if (name === "client-facts") {
    const page = await api.listPublishedFacts(publishedParams(filters));
    return clientFactListView({ page, query, loading: false });
  }
  if (name === "client-fact-detail") {
    const item = await loadPublishedFact(query);
    return clientFactDetailView({ item, loading: false });
  }
  return notFoundView();
}

function itemsFind(page, id) {
  const items = page && page.items ? page.items : [];
  return items.find((item) => item.processing_run_id === id) || null;
}

function clientScoped(filters) {
  const next = { ...filters };
  if (session && session.client_id && !next.client_id) {
    next.client_id = session.client_id;
  }
  return next;
}

function publishedParams(filters) {
  const next = {};
  if (filters.limit !== undefined && filters.limit !== null && filters.limit !== "") {
    next.limit = filters.limit;
  }
  if (filters.offset !== undefined && filters.offset !== null && filters.offset !== "") {
    next.offset = filters.offset;
  }
  if (session && session.client_id) {
    next.client_id = session.client_id;
  } else if (filters.client_id) {
    next.client_id = filters.client_id;
  }
  return next;
}

function matchesPublishedFact(item, query) {
  if (query.get("client_id") && item.client_id !== query.get("client_id")) return false;
  if (query.get("campaign_id") && item.campaign_id !== query.get("campaign_id")) return false;
  if (query.get("day") && String(item.day) !== query.get("day")) return false;
  if (query.get("variation_null") === "1") return item.variation_id === null;
  if (query.has("variation_id")) return item.variation_id === query.get("variation_id");
  return true;
}

async function loadFact(query) {
  const params = {
    client_id: query.get("client_id") || undefined,
    campaign_id: query.get("campaign_id") || undefined,
    day: query.get("day") || undefined,
    limit: 50,
    offset: 0,
  };
  if (query.get("variation_null") !== "1" && query.has("variation_id")) {
    params.variation_id = query.get("variation_id");
  }
  const page = await api.listFacts(clientScoped(params));
  const items = page.items || [];
  if (query.get("variation_null") === "1") {
    const match = items.find((item) => item.variation_id === null);
    if (!match) throw new ApiError(404, "NOT_FOUND", "Resource not found.");
    return match;
  }
  if (!items.length) {
    throw new ApiError(404, "NOT_FOUND", "Resource not found.");
  }
  return items[0];
}

async function loadPublishedFact(query) {
  const pageSize = 200;
  const hasGrain =
    Boolean(query.get("campaign_id")) ||
    Boolean(query.get("day")) ||
    query.has("variation_id") ||
    query.get("variation_null") === "1";
  let offset = 0;
  let total = Infinity;
  while (offset < total) {
    const page = await api.listPublishedFacts(
      publishedParams({
        limit: pageSize,
        offset,
        client_id: query.get("client_id") || undefined,
      }),
    );
    const items = page.items || [];
    total = page.pagination && typeof page.pagination.total === "number" ? page.pagination.total : 0;
    if (!hasGrain) {
      if (!items.length) {
        throw new ApiError(404, "NOT_FOUND", "Resource not found.");
      }
      return items[0];
    }
    const match = items.find((item) => matchesPublishedFact(item, query));
    if (match) return match;
    if (!items.length) break;
    offset += pageSize;
  }
  throw new ApiError(404, "NOT_FOUND", "Resource not found.");
}

function handleError(error, path) {
  if (error instanceof ApiError && (error.status === 401 || error.code === "AUTHENTICATION_FAILED")) {
    clearToken();
    session = null;
    root.innerHTML = toHtml(
      credentialView({
        error,
        nextPath: `${path}${window.location.search}`,
      }),
    );
    return;
  }
  if (error instanceof ApiError && (error.status === 403 || error.code === "AUTHORIZATION_FAILED")) {
    if (needsCompanySelection(session) || (session && canAccessAdmin(session.role) && !session.client_id)) {
      render(companySelectView({ session, error }), path.startsWith("/admin") ? path : "/admin");
      return;
    }
    render(unauthorizedView(), path);
    return;
  }
  render(html`${errorBanner(error)}`, path);
}

function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.rel = "noopener";
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function refreshableDownloadButtons() {
  return document.querySelectorAll("[data-download-refreshable-client-report]");
}

function setRefreshableDownloadBusy(phase) {
  const busy = phase === "generating" || phase === "downloading";
  const label =
    phase === "generating" ? "Generating…" : phase === "downloading" ? "Downloading…" : REFRESHABLE_DOWNLOAD_LABEL;
  for (const button of refreshableDownloadButtons()) {
    button.disabled = busy;
    button.setAttribute("aria-busy", busy ? "true" : "false");
    button.textContent = label;
  }
}

function clearRefreshableDownloadError() {
  for (const host of document.querySelectorAll("[data-refreshable-download-error]")) {
    host.hidden = true;
    host.textContent = "";
  }
}

function refreshableDownloadErrorMessage(error) {
  if (error instanceof ApiError && error.status === 429) {
    return "A workbook is already being generated. Wait for it to finish, then try again.";
  }
  if (error instanceof ApiError && error.status === 404) {
    return error.message || "This company has no current published report.";
  }
  if (error instanceof ApiError && error.status === 422) {
    return error.message || "The published slice is too large to download as a workbook.";
  }
  if (error instanceof ApiError && (error.status === 0 || error.code === "NETWORK_FAILURE")) {
    return "The download was interrupted. Stay on this page and try again.";
  }
  return error && error.message ? error.message : "Refreshable workbook download failed.";
}

function showRefreshableDownloadError(error) {
  const message = refreshableDownloadErrorMessage(error);
  const hosts = document.querySelectorAll("[data-refreshable-download-error]");
  if (!hosts.length) {
    showToast({ tone: "warning", title: "Refreshable workbook download failed.", message });
    return;
  }
  for (const host of hosts) {
    host.hidden = false;
    host.textContent = message;
  }
}

function reloadOverview() {
  navigate(currentLocation().path + (currentLocation().query.toString() ? `?${currentLocation().query}` : ""));
}

async function openSavedAnalysis(id) {
  const record = await api.getSavedAnalysis(id);
  const query = queryFromWorkspaceState(record.state || {});
  query.set("saved", record.id);
  navigate(overviewHref(query));
}

function catalogPath(kind) {
  const path = currentLocation().path;
  if (path === "/admin/catalogs") return `/admin/catalogs?kind=${kind}`;
  if (path === "/admin/upload") return "/admin/upload";
  if (path === "/admin/downloads") return "/admin/downloads";
  return kind === "labels" ? "/admin/labels" : "/admin/logic";
}

async function loadCatalogView(kind, extras = {}) {
  const path = catalogPath(kind);
  const scope = clientScoped({ client_id: currentLocation().query.get("client_id") || undefined });
  const page = await api.listCatalogs(kind, scope);
  if (currentLocation().path === "/admin/upload") {
    const logicPage = kind === "logic" ? page : await api.listCatalogs("logic", scope);
    const labelsPage = kind === "labels" ? page : await api.listCatalogs("labels", scope);
    const pending = loadPendingUpload();
    let uploadResult = extras.rawUploadResult;
    if (!uploadResult && pending && pending.batchId) {
      try {
        uploadResult = await fetchUploadSnapshot(pending);
      } catch (error) {
        if (isAuthError(error)) throw error;
      }
    }
    render(
      uploadCenterView(
        withUploadLimit({
          session,
          query: currentLocation().query,
          logicPage,
          labelsPage,
          uploadResult,
          catalogResult: extras.uploadResult,
          catalogKind: kind,
          error: extras.error,
          loading: false,
        }),
      ),
      path,
    );
    if (pending && pending.batchId && !isTerminalUpload(uploadResult)) {
      resumePendingUploadPoll();
    }
    return;
  }
  render(
    catalogView({
      kind,
      page,
      session,
      query: currentLocation().query,
      ...extras,
      loading: false,
    }),
    path,
  );
}

async function runCatalogAction(action, button) {
  const kind = button.getAttribute("data-catalog-kind") || "logic";
  const clientId =
    button.getAttribute("data-catalog-client") ||
    (session && session.client_id) ||
    currentLocation().query.get("client_id") ||
    "";
  const label = button.getAttribute("data-catalog-label") || "this version";
  const confirmed = await confirmAction({
    title: action === "activate" ? "Activate this version?" : "Deactivate this version?",
    message:
      action === "activate"
        ? `Activate ${label}. The previous active version is superseded. Processing will use this overlay. This does not publish.`
        : `Deactivate ${label}. Processing will use the packaged fallback. Historical runs keep their original versions. This does not publish.`,
    confirmLabel: action === "activate" ? "Activate Version" : "Deactivate Version",
    tone: action === "deactivate" ? "danger" : "primary",
  });
  if (!confirmed) return;
  const id =
    action === "activate"
      ? button.getAttribute("data-catalog-activate")
      : button.getAttribute("data-catalog-deactivate");
  const path = catalogPath(kind);
  try {
    const actionResult =
      action === "activate"
        ? await api.activateCatalog(kind, id, clientId ? { client_id: clientId } : {})
        : await api.deactivateCatalog(kind, id, clientId ? { client_id: clientId } : {});
    const title =
      action === "deactivate"
        ? `${kind === "labels" ? "Labels" : "Logic"} version deactivated.`
        : `${kind === "labels" ? "Labels" : "Logic"} version activated.`;
    showToast({
      tone: "success",
      title,
      message: `${label} is now ${actionResult.status}.`,
    });
    await loadCatalogView(kind, { actionResult });
  } catch (error) {
    handleError(error, path);
  }
}

async function runCompanyLifecycle(action, clientId) {
  if (!clientId) return;
  const confirmed = await confirmAction({
    title: action === "deactivate" ? "Deactivate this company?" : "Reactivate this company?",
    message:
      action === "deactivate"
        ? "New uploads, processing, publishing, and client-portal access stop. History, memberships, and publication_current stay. Delete Company is a separate permanent action after deactivation."
        : "This company becomes active again. Existing memberships, publications, catalogs, and archives are unchanged.",
    confirmLabel: action === "deactivate" ? "Deactivate" : "Reactivate",
    tone: action === "deactivate" ? "danger" : "primary",
  });
  if (!confirmed) return;
  try {
    if (action === "deactivate") {
      await api.deactivateClient(clientId);
    } else {
      await api.reactivateClient(clientId);
    }
    const refreshed = await api.session();
    session = refreshed;
    showToast({
      tone: "success",
      title: action === "deactivate" ? "Company deactivated." : "Company reactivated.",
      message:
        action === "deactivate"
          ? "Live operations are blocked. Historical publisher recovery remains available."
          : "Live operations are restored.",
    });
    window.dispatchEvent(new Event("dfip:navigate"));
  } catch (error) {
    handleError(error, "/admin/companies");
  }
}

async function runCompanyDelete(button) {
  const clientId = button && button.getAttribute("data-company-delete");
  if (!clientId) return;
  const name = button.getAttribute("data-company-delete-name") || "this company";
  const confirmed = await confirmAction({
    title: "Delete this company permanently?",
    message: `This permanently deletes ${name}, including users scoped only to this company, uploads, batches, processing runs, facts, publication history, Excel workbook grants, and stored files. This cannot be undone.`,
    confirmLabel: "Delete Company",
    tone: "danger",
  });
  if (!confirmed) return;
  try {
    await api.deleteClient(clientId);
    const refreshed = await api.session();
    session = refreshed;
    showToast({
      tone: "success",
      title: "Company deleted.",
      message: "The company and its application data are gone.",
    });
    window.dispatchEvent(new Event("dfip:navigate"));
  } catch (error) {
    handleError(error, "/admin/companies");
  }
}

async function runBatchReprocess(button) {
  const batchId = button.getAttribute("data-batch-reprocess");
  const clientId =
    button.getAttribute("data-batch-client") ||
    (session && session.client_id) ||
    currentLocation().query.get("client_id") ||
    "";
  const kind = button.getAttribute("data-batch-retry-kind") || "reprocess";
  const retrying = kind === "retry";
  const confirmed = await confirmAction({
    title: retrying ? "Retry this batch?" : "Re-process this batch?",
    message: retrying
      ? "Retry processing from archived source bytes or existing staged rows. A new processing run is created. This does not require choosing the file again and does not publish."
      : "Create a new processing run from the existing staged rows using the currently active Logic and Labels. The previous run is kept. This does not upload Raw again and does not publish.",
    confirmLabel: retrying ? "Retry" : "Re-process",
  });
  if (!confirmed) return;
  const path = currentLocation().path;
  try {
    const result = await api.processBatch(batchId, clientId ? { client_id: clientId } : {});
    const run = result && result.processing_run;
    showToast({
      tone: "success",
      title: "Re-process accepted.",
      message: run
        ? `Processing run ${run.processing_run_id} is ${run.status}.`
        : "A new processing run was created.",
    });
    if (run && run.processing_run_id) {
      navigate(`/admin/processing-runs/${run.processing_run_id}`);
    }
  } catch (error) {
    handleError(error, path);
  }
}

async function runBatchCancel(button) {
  if (!button || button.disabled) return;
  const batchId = button.getAttribute("data-batch-cancel");
  const clientId =
    button.getAttribute("data-batch-client") ||
    (session && session.client_id) ||
    currentLocation().query.get("client_id") ||
    "";
  const confirmed = await confirmAction({
    title: "Cancel this upload?",
    message:
      "Processing stops as soon as it is safe. Temporary staging and this run's facts are discarded. No publication is created.",
    confirmLabel: "Cancel Processing",
    tone: "danger",
  });
  if (!confirmed) return;
  button.disabled = true;
  button.textContent = "Cancelling...";
  const path = currentLocation().path;
  try {
    await api.cancelBatch(batchId, clientId ? { client_id: clientId } : {});
    showToast({
      tone: "info",
      title: "Cancelling...",
      message: "Processing will stop at the next safe boundary.",
    });
    if (path === "/admin/upload") {
      resumePendingUploadPoll();
    } else if (path === `/admin/batches/${batchId}`) {
      scheduleBatchPoll(batchId);
    }
    window.dispatchEvent(new Event("dfip:navigate"));
  } catch (error) {
    handleError(error, path);
  }
}

async function runBatchDelete(button) {
  if (!button || button.disabled) return;
  const batchId = button.getAttribute("data-batch-delete");
  const clientId =
    button.getAttribute("data-batch-client") ||
    (session && session.client_id) ||
    currentLocation().query.get("client_id") ||
    "";
  const confirmed = await confirmAction({
    title: "Delete this upload?",
    message:
      "This removes the batch, its staging, and this run's unpublished facts. Published history is not changed. This cannot be undone.",
    confirmLabel: "Delete Upload",
    tone: "danger",
  });
  if (!confirmed) return;
  button.disabled = true;
  const path = currentLocation().path;
  try {
    await api.deleteBatch(batchId, clientId ? { client_id: clientId } : {});
    showToast({ tone: "success", title: "Upload deleted." });
    if (path === `/admin/batches/${batchId}`) {
      navigate("/admin/batches");
      return;
    }
    window.dispatchEvent(new Event("dfip:navigate"));
  } catch (error) {
    button.disabled = false;
    handleError(error, path);
  }
}

function paintPublishProgress(item) {
  const host = document.querySelector("[data-publish-progress-host]");
  if (!host) return;
  host.innerHTML = toHtml(publicationProgressPanel(item));
}

function startPublicationProgressPoll(runId, clientId) {
  stopPublishPoll();
  paintPublishProgress({
    stage: "preparing",
    status: "running",
    message: "Preparing publication",
  });
  const tick = async () => {
    try {
      const item = await api.getPublicationProgress({
        processing_run_id: runId,
        client_id: clientId,
      });
      paintPublishProgress(item);
      if (
        item.status === "succeeded" ||
        item.status === "failed" ||
        item.stage === "published"
      ) {
        stopPublishPoll();
        return;
      }
    } catch (error) {
      if (isAuthError(error)) {
        stopPublishPoll();
        return;
      }
    }
    publishPollTimer = setTimeout(tick, 400);
  };
  publishPollTimer = setTimeout(tick, 400);
}

async function reloadPublications({ publishResult, error, clientId } = {}) {
  const scoped = clientId ? { client_id: clientId } : clientScoped({});
  const runs = await api.listProcessingRuns({ status: "succeeded", limit: 200, offset: 0 });
  const currentPublication = await safeRead(() => api.getCurrentPublication(scoped));
  const publications = await api.listPublications({ ...scoped, limit: 50, offset: 0 });
  render(
    publicationsView({
      session,
      runs,
      currentPublication,
      publications,
      publishResult,
      error,
      loading: false,
    }),
    "/admin/publications",
  );
}

async function reloadUpload({ uploadResult, error, clientId } = {}) {
  const scoped = clientId ? { client_id: clientId } : clientScoped({});
  const logicPage = await safeRead(() => api.listCatalogs("logic", scoped));
  const labelsPage = await safeRead(() => api.listCatalogs("labels", scoped));
  render(
    uploadCenterView(
      withUploadLimit({
        session,
        query: currentLocation().query,
        logicPage,
        labelsPage,
        uploadResult,
        error,
        loading: false,
      }),
    ),
    "/admin/upload",
  );
}

function openTrendDrill(el) {
  const query = currentLocation().query;
  const trendMetric = query.get("trend_metric") || "total_cost";
  const trendGrain = query.get("trend_grain") || "day";
  const trendBreakdown = query.get("trend_breakdown") || "";
  const seriesKey = el.getAttribute("data-series-key") || "";
  const bucket = el.getAttribute("data-bucket") || "";
  const parents = [];
  let dimension = "campaign_id";
  if (trendBreakdown && seriesKey && seriesKey !== "total" && seriesKey !== "__other__") {
    parents.push(`${trendBreakdown}:${seriesKey}`);
    dimension = trendBreakdown === "campaign_id" ? "channel" : "campaign_id";
    if (dimension === trendBreakdown) dimension = "day";
  }
  navigate(
    overviewHref(
      withDrill(query, {
        origin: "trend",
        metric: trendMetric,
        dimension,
        parents,
        slice: bucket,
        sliceGrain: trendGrain,
      }),
    ),
  );
}

root.addEventListener("click", (event) => {
  const navToggle = event.target.closest("[data-nav-toggle]");
  if (navToggle) {
    event.preventDefault();
    document.querySelector(".shell")?.classList.toggle("nav-open");
    return;
  }
  const navClose = event.target.closest("[data-nav-close]");
  if (navClose) {
    event.preventDefault();
    document.querySelector(".shell")?.classList.remove("nav-open");
    return;
  }
  const pickerOpen = event.target.closest("[data-filter-picker-open]");
  if (pickerOpen) {
    event.preventDefault();
    toggleOverviewFilterPicker(pickerOpen.closest("[data-filter-picker]"));
    return;
  }
  const pickerAll = event.target.closest("[data-filter-picker-all]");
  if (pickerAll) {
    event.preventDefault();
    const picker = pickerAll.closest("[data-filter-picker]");
    for (const box of filterPickerVisibleBoxes(picker)) box.checked = true;
    syncFilterPickerSummary(picker);
    return;
  }
  const pickerClear = event.target.closest("[data-filter-picker-clear]");
  if (pickerClear) {
    event.preventDefault();
    const picker = pickerClear.closest("[data-filter-picker]");
    if (picker) {
      for (const box of picker.querySelectorAll("input[type='checkbox']")) box.checked = false;
      syncFilterPickerSummary(picker);
    }
    return;
  }
  if (!event.target.closest("[data-filter-picker]")) closeOverviewFilterPicker();
  const generateOpen = event.target.closest("[data-generate-trend-open]");
  if (generateOpen) {
    event.preventDefault();
    toggleGenerateTrendPopover();
    return;
  }
  const generateCancel = event.target.closest("[data-generate-trend-cancel]");
  if (generateCancel) {
    event.preventDefault();
    closeGenerateTrendPopover({ restoreFocus: true });
    return;
  }
  const generateSubmit = event.target.closest("[data-generate-trend-submit]");
  if (generateSubmit) {
    event.preventDefault();
    event.stopPropagation();
    submitGenerateTrendFrom(generateSubmit);
    return;
  }
  if (!event.target.closest("[data-generate-trend]")) closeGenerateTrendPopover();
  const askAiOpen = event.target.closest("[data-ask-ai-open]");
  if (askAiOpen) {
    event.preventDefault();
    toggleAskAiPopover();
    return;
  }
  const askAiCancel = event.target.closest("[data-ask-ai-cancel]");
  if (askAiCancel) {
    event.preventDefault();
    closeAskAiPopover({ restoreFocus: true });
    return;
  }
  const askAiExample = event.target.closest("[data-ask-ai-example]");
  if (askAiExample) {
    event.preventDefault();
    event.stopPropagation();
    const host = askAiExample.closest("[data-ask-ai]");
    const input = host && host.querySelector("[data-ask-ai-input]");
    if (input) {
      input.value = askAiExample.getAttribute("data-ask-ai-example") || "";
      input.focus();
    }
    return;
  }
  const askAiSubmit = event.target.closest("[data-ask-ai-submit]");
  if (askAiSubmit) {
    event.preventDefault();
    event.stopPropagation();
    submitAskAiFrom(askAiSubmit);
    return;
  }
  if (!event.target.closest("[data-ask-ai]")) closeAskAiPopover();
  const copy = event.target.closest("[data-copy]");
  if (copy) {
    event.preventDefault();
    const value = copy.getAttribute("data-copy") || "";
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(value).then(
        () => showToast({ tone: "info", title: copy.getAttribute("data-copy-toast") || "Copied identifier." }),
        () => {},
      );
    }
    return;
  }
  const chipClear = event.target.closest("[data-filter-chip-clear]");
  if (chipClear) {
    event.preventDefault();
    const key = chipClear.getAttribute("data-filter-chip-clear") || "";
    const value = chipClear.getAttribute("data-filter-chip-value") || "";
    const query = new URLSearchParams(currentLocation().query);
    if (key === "compare_month_start") {
      query.delete("compare_month_start");
      query.delete("compare_from");
      query.delete("compare_to");
    } else if (key && !value) {
      query.delete(key);
    } else if (key) {
      const kept = query.getAll(key).filter((item) => item !== value);
      query.delete(key);
      for (const item of kept) query.append(key, item);
    }
    navigate(overviewHref(query));
    return;
  }
  const retry = event.target.closest("[data-overview-retry]");
  if (retry) {
    event.preventDefault();
    const name = retry.getAttribute("data-overview-retry") || "all";
    if (name === "drill-trend") {
      ensureOverviewDrillTrend(currentLocation().query, { force: true });
      return;
    }
    refreshOverviewInPlace(currentLocation().query, { force: [name] }).catch((error) => {
      if (isInactiveCompanyError(error)) {
        overviewClientId = "";
        window.dispatchEvent(new Event("dfip:navigate"));
        return;
      }
      handleError(error, currentLocation().path);
    });
    return;
  }
  const askAbout = event.target.closest("[data-ask]");
  if (askAbout && askAbout.tagName === "BUTTON") {
    event.preventDefault();
    const form = document.querySelector("[data-overview-ask-form]");
    if (!(form instanceof HTMLFormElement)) return;
    const set = (name, value) => {
      if (form.elements[name]) form.elements[name].value = value || "";
    };
    set("source", askAbout.getAttribute("data-ask-source") || "overview");
    set("metric", askAbout.getAttribute("data-ask-metric") || "");
    set("dimension", askAbout.getAttribute("data-ask-dimension") || "");
    set("insight_id", askAbout.getAttribute("data-ask-insight") || "");
    set("anomaly_id", askAbout.getAttribute("data-ask-anomaly") || "");
    const suggested = askAbout.getAttribute("data-ask-question") || "";
    if (form.elements.question && suggested) form.elements.question.value = suggested;
    const context = document.querySelector("[data-ask-context]");
    if (context && suggested) {
      const source = askAbout.getAttribute("data-ask-source") || "overview";
      context.setAttribute("data-ask-focus", source);
    }
    document.querySelector("details[data-overview-ask]")?.scrollIntoView({ behavior: "smooth", block: "start" });
    const askPanel = document.querySelector("details[data-overview-ask]");
    if (askPanel instanceof HTMLDetailsElement) askPanel.open = true;
    if (form.elements.question) form.elements.question.focus();
    return;
  }
  const savedOpen = event.target.closest("[data-saved-open]");
  if (savedOpen) {
    event.preventDefault();
    openSavedAnalysis(savedOpen.getAttribute("data-saved-open")).catch((error) => {
      if (isAuthError(error) && error.status === 401) {
        handleError(error, currentLocation().path);
        return;
      }
      showToast({ tone: "warning", title: "Could not open saved analysis.", message: error.message || "" });
    });
    return;
  }
  const savedRename = event.target.closest("[data-saved-rename]");
  if (savedRename) {
    event.preventDefault();
    const currentTitle = savedRename.getAttribute("data-saved-title") || "";
    const nextTitle = window.prompt("Rename saved analysis", currentTitle);
    if (!nextTitle || nextTitle.trim() === currentTitle) return;
    api
      .updateSavedAnalysis(savedRename.getAttribute("data-saved-rename"), { title: nextTitle.trim() })
      .then(() => {
        showToast({ tone: "success", title: "Saved analysis renamed." });
        overviewSavedDirty = true;
        reloadOverview();
      })
      .catch((error) => {
        if (isAuthError(error) && error.status === 401) {
          handleError(error, currentLocation().path);
          return;
        }
        showToast({ tone: "warning", title: "Could not rename saved analysis.", message: error.message || "" });
      });
    return;
  }
  const savedDelete = event.target.closest("[data-saved-delete]");
  if (savedDelete) {
    event.preventDefault();
    const title = savedDelete.getAttribute("data-saved-title") || "this analysis";
    confirmAction({
      title: "Delete saved analysis?",
      message: `${title} will be removed. Published data is not deleted.`,
      confirmLabel: "Delete",
      tone: "danger",
    }).then((ok) => {
      if (!ok) return;
      api
        .deleteSavedAnalysis(savedDelete.getAttribute("data-saved-delete"))
        .then(() => {
          showToast({ tone: "success", title: "Saved analysis deleted." });
          overviewSavedDirty = true;
          const query = currentLocation().query;
          if (query.get("saved") === savedDelete.getAttribute("data-saved-delete")) {
            query.delete("saved");
            navigate(overviewHref(query));
            return;
          }
          reloadOverview();
        })
        .catch((error) => {
          if (isAuthError(error) && error.status === 401) {
            handleError(error, currentLocation().path);
            return;
          }
          showToast({ tone: "warning", title: "Could not delete saved analysis.", message: error.message || "" });
        });
    });
    return;
  }
  const explorerExportCsv = event.target.closest("[data-overview-explorer-export]");
  if (explorerExportCsv) {
    event.preventDefault();
    event.stopPropagation();
    api
      .downloadExplorerExport(explorerParamsFromQuery(currentLocation().query, publishedParams({})))
      .then((payload) => {
        saveBlob(payload.blob, payload.filename || "dfip-explorer.csv");
        showToast({ tone: "success", title: "Explorer CSV downloaded." });
      })
      .catch((error) => {
        if (isAuthError(error) && error.status === 401) {
          handleError(error, currentLocation().path);
          return;
        }
        showToast({ tone: "warning", title: "Could not export explorer.", message: error.message || "" });
      });
    return;
  }
  const exportCsv = event.target.closest("[data-overview-export]");
  if (exportCsv) {
    event.preventDefault();
    api
      .downloadOverviewExport(exportParamsFromQuery(currentLocation().query, publishedParams({})))
      .then((payload) => {
        saveBlob(payload.blob, payload.filename || "dfip-analysis.csv");
        showToast({ tone: "success", title: "Analysis CSV downloaded." });
      })
      .catch((error) => {
        if (isAuthError(error) && error.status === 401) {
          handleError(error, currentLocation().path);
          return;
        }
        showToast({ tone: "warning", title: "Could not export analysis.", message: error.message || "" });
      });
    return;
  }
  const download = event.target.closest("[data-download-published]");
  if (download) {
    event.preventDefault();
    const kind = download.getAttribute("data-download-published");
    const path = currentLocation().path;
    api
      .downloadPublishedFacts(kind, publishedParams(queryObject(currentLocation().query)))
      .then((payload) => {
        saveBlob(payload.blob, payload.filename);
        showToast({ tone: "success", title: kind === "xlsx" ? "Published XLSX downloaded." : "Published CSV downloaded." });
      })
      .catch((error) => handleError(error, path));
    return;
  }
  const clientReport = event.target.closest("[data-download-client-report]");
  if (clientReport) {
    event.preventDefault();
    const raw = clientReport.getAttribute("data-download-client-report") || "";
    const publicationId = raw && raw !== "current" ? raw : null;
    const path = currentLocation().path;
    api
      .downloadClientReport(publicationId, publishedParams(queryObject(currentLocation().query)))
      .then((payload) => {
        saveBlob(payload.blob, payload.filename || "Client_Report.xlsx");
        showToast({
          tone: "success",
          title: publicationId ? "Client report downloaded." : "Company workbook downloaded.",
        });
      })
      .catch((error) => handleError(error, path));
    return;
  }
  const refreshableReport = event.target.closest("[data-download-refreshable-client-report]");
  if (refreshableReport) {
    event.preventDefault();
    if (refreshableWorkbookDownloadInFlight || refreshableReport.disabled) return;
    const path = currentLocation().path;
    refreshableWorkbookDownloadInFlight = true;
    clearRefreshableDownloadError();
    setRefreshableDownloadBusy("generating");
    api
      .downloadRefreshableClientReport(publishedParams(queryObject(currentLocation().query)), {
        onHeaders(response) {
          if (response.ok) setRefreshableDownloadBusy("downloading");
        },
      })
      .then((payload) => {
        saveBlob(payload.blob, payload.filename || "Client_Report_Refreshable.xlsm");
        showToast({
          tone: "success",
          title: "Refreshable company workbook downloaded.",
        });
      })
      .catch((error) => {
        if (isAuthError(error)) {
          handleError(error, path);
          return;
        }
        showRefreshableDownloadError(error);
      })
      .finally(() => {
        refreshableWorkbookDownloadInFlight = false;
        setRefreshableDownloadBusy(null);
      });
    return;
  }
  const catalogDownload = event.target.closest("[data-catalog-download]");
  if (catalogDownload) {
    event.preventDefault();
    const kind = catalogDownload.getAttribute("data-catalog-kind") || "logic";
    const id = catalogDownload.getAttribute("data-catalog-download");
    const clientId = catalogDownload.getAttribute("data-catalog-client") || "";
    const path = currentLocation().path;
    api
      .downloadCatalog(kind, id, clientId ? { client_id: clientId } : {})
      .then((payload) => {
        saveBlob(payload.blob, payload.filename);
        showToast({ tone: "success", title: `${kind === "labels" ? "Labels" : "Logic"} downloaded.` });
      })
      .catch((error) => handleError(error, path));
    return;
  }
  const catalogActivate = event.target.closest("[data-catalog-activate]");
  if (catalogActivate) {
    event.preventDefault();
    runCatalogAction("activate", catalogActivate);
    return;
  }
  const catalogDeactivate = event.target.closest("[data-catalog-deactivate]");
  if (catalogDeactivate) {
    event.preventDefault();
    runCatalogAction("deactivate", catalogDeactivate);
    return;
  }
  const companyDeactivate = event.target.closest("[data-company-deactivate]");
  if (companyDeactivate) {
    event.preventDefault();
    runCompanyLifecycle("deactivate", companyDeactivate.getAttribute("data-company-deactivate"));
    return;
  }
  const companyReactivate = event.target.closest("[data-company-reactivate]");
  if (companyReactivate) {
    event.preventDefault();
    runCompanyLifecycle("reactivate", companyReactivate.getAttribute("data-company-reactivate"));
    return;
  }
  const companyDelete = event.target.closest("[data-company-delete]");
  if (companyDelete) {
    event.preventDefault();
    runCompanyDelete(companyDelete);
    return;
  }
  const batchReprocess = event.target.closest("[data-batch-reprocess]");
  if (batchReprocess) {
    event.preventDefault();
    runBatchReprocess(batchReprocess);
    return;
  }
  const batchCancel = event.target.closest("[data-batch-cancel]");
  if (batchCancel) {
    event.preventDefault();
    runBatchCancel(batchCancel);
    return;
  }
  const batchDelete = event.target.closest("[data-batch-delete]");
  if (batchDelete) {
    event.preventDefault();
    runBatchDelete(batchDelete);
    return;
  }
  const trendDrill = event.target.closest("[data-trend-drill]");
  if (trendDrill && !trendDrill.closest("[data-drill-panel]")) {
    event.preventDefault();
    overviewDrillReturnFocus = "";
    openTrendDrill(trendDrill);
    return;
  }
  const drillModeTab = event.target.closest("[data-drill-mode]");
  if (drillModeTab && drillModeTab.closest("[data-drill-panel]")) {
    event.preventDefault();
    setOverviewDrillMode(drillModeTab.getAttribute("data-drill-mode") || "breakdown");
    return;
  }
  const drillClose = event.target.closest("[data-drill-close]");
  if (drillClose && !(drillClose instanceof HTMLAnchorElement)) {
    event.preventDefault();
    navigate(overviewHref(stripDrill(currentLocation().query)));
    return;
  }
  const filterCompact = event.target.closest("[data-overview-filter-compact]");
  if (filterCompact) {
    event.preventDefault();
    revealOverviewFilters();
    return;
  }
  const drillOrigin = event.target.closest(
    "a[data-overview-drill-kpi].overview-kpi, [data-explorer-drill], [data-insight-drill], [data-anomaly-drill]",
  );
  if (drillOrigin) rememberKpiDrillReturn(drillOrigin);
  const link = event.target.closest("a");
  if (!link || link.target === "_blank" || link.origin !== window.location.origin) return;
  const rawHref = link.getAttribute("href") || "";
  if (rawHref.startsWith("#")) return;
  if (link.hash && !link.pathname) return;
  event.preventDefault();
  document.querySelector(".shell")?.classList.remove("nav-open");
  navigate(`${link.pathname}${link.search}`);
});

document.addEventListener("mousedown", (event) => {
  const open = root && root.querySelector(".filter-picker.is-open");
  if (!open) return;
  if (open.contains(event.target)) return;
  closeOverviewFilterPicker();
});

root.addEventListener("focusin", (event) => {
  const open = root.querySelector(".filter-picker.is-open");
  if (!open) return;
  if (open.contains(event.target)) return;
  closeOverviewFilterPicker();
});

let trendTooltipChart = null;

function hideTrendTooltip(chart) {
  const host = chart || trendTooltipChart;
  if (!host) return;
  const tip = host.querySelector("[data-trend-tooltip]");
  if (tip) {
    tip.hidden = true;
    tip.replaceChildren();
  }
  const mark = host.querySelector("[data-trend-hover-mark]");
  if (mark) mark.setAttribute("visibility", "hidden");
  const line = host.querySelector("[data-trend-hover-line]");
  if (line) line.setAttribute("visibility", "hidden");
  if (host === trendTooltipChart) trendTooltipChart = null;
}

function fillTrendTooltip(tip, model) {
  tip.replaceChildren();
  const date = document.createElement("p");
  date.className = "trend-tooltip-date";
  date.textContent = model.bucket_label || model.bucket || "";
  tip.appendChild(date);
  for (const row of Array.isArray(model.rows) ? model.rows : []) {
    const line = document.createElement("p");
    line.className = "trend-tooltip-row";
    const label = document.createElement("span");
    label.className = "trend-tooltip-label";
    label.textContent = row.label || "";
    const value = document.createElement("span");
    value.className = "trend-tooltip-value";
    value.textContent = row.value || "n/a";
    line.append(label, value);
    tip.appendChild(line);
  }
}

function positionTrendTooltip(chart, tip, event) {
  const box = chart.getBoundingClientRect();
  const width = tip.offsetWidth;
  const height = tip.offsetHeight;
  let x = event.clientX - box.left + 14;
  let y = event.clientY - box.top - height - 12;
  if (x + width > box.width - 8) x = Math.max(8, box.width - width - 8);
  if (x < 8) x = 8;
  if (y < 8) y = event.clientY - box.top + 18;
  if (y + height > box.height - 8) y = Math.max(8, box.height - height - 8);
  tip.style.left = `${x}px`;
  tip.style.top = `${y}px`;
}

function showTrendTooltip(chart, hit, event) {
  const tip = chart.querySelector("[data-trend-tooltip]");
  if (!(tip instanceof HTMLElement)) return;
  let model = null;
  try {
    model = JSON.parse(decodeURIComponent(hit.getAttribute("data-trend-point") || ""));
  } catch (_error) {
    return;
  }
  if (!model) return;
  fillTrendTooltip(tip, model);
  tip.hidden = false;
  positionTrendTooltip(chart, tip, event);
  const mark = chart.querySelector("[data-trend-hover-mark]");
  const line = chart.querySelector("[data-trend-hover-line]");
  const x = hit.getAttribute("data-trend-x") || "0";
  const y = hit.getAttribute("data-trend-y") || "0";
  if (mark) {
    mark.setAttribute("cx", x);
    mark.setAttribute("cy", y);
    mark.setAttribute("visibility", "visible");
  }
  if (line) {
    line.setAttribute("x1", x);
    line.setAttribute("x2", x);
    line.setAttribute("visibility", "visible");
  }
  trendTooltipChart = chart;
}

root.addEventListener("pointermove", (event) => {
  const chart = event.target.closest("[data-trend-chart][data-trend-tooltip-enabled]");
  if (!chart) {
    if (trendTooltipChart) hideTrendTooltip(trendTooltipChart);
    return;
  }
  const hit = event.target.closest("[data-trend-hover]");
  if (!hit || !chart.contains(hit)) {
    hideTrendTooltip(chart);
    return;
  }
  showTrendTooltip(chart, hit, event);
});

root.addEventListener("pointerleave", () => {
  if (trendTooltipChart) hideTrendTooltip(trendTooltipChart);
});

document.addEventListener("keydown", (event) => {
  if (event.defaultPrevented) return;
  if (handleAskAiKeydown(event)) return;
  if (handleGenerateTrendKeydown(event)) return;
});

root.addEventListener("keydown", (event) => {
  if (handleAskAiKeydown(event)) return;
  if (handleGenerateTrendKeydown(event)) return;
  if (handleOverviewFilterPickerKeydown(event)) return;
  if (event.key === "Escape" && parseDrillQuery(currentLocation().query)) {
    event.preventDefault();
    navigate(overviewHref(stripDrill(currentLocation().query)));
    return;
  }
  if (event.key === "Tab" && trapOverviewDrillFocus(event)) return;
  const modeTab = event.target.closest("[data-drill-mode]");
  if (modeTab && modeTab.closest("[data-drill-panel]") && (event.key === "ArrowRight" || event.key === "ArrowLeft" || event.key === "Home" || event.key === "End")) {
    event.preventDefault();
    const tabs = [...(root.querySelectorAll("[data-drill-panel] [data-drill-mode]") || [])];
    const index = tabs.indexOf(modeTab);
    if (index >= 0 && tabs.length) {
      let nextIndex = index;
      if (event.key === "ArrowRight") nextIndex = (index + 1) % tabs.length;
      else if (event.key === "ArrowLeft") nextIndex = (index - 1 + tabs.length) % tabs.length;
      else if (event.key === "Home") nextIndex = 0;
      else if (event.key === "End") nextIndex = tabs.length - 1;
      const next = tabs[nextIndex];
      if (next) setOverviewDrillMode(next.getAttribute("data-drill-mode") || "breakdown");
    }
    return;
  }
  if (event.key !== "Enter" && event.key !== " ") return;
  const kpiCard = event.target.closest("a[data-overview-drill-kpi].overview-kpi");
  if (kpiCard) {
    rememberKpiDrillReturn(kpiCard);
    if (event.key === " ") {
      event.preventDefault();
      kpiCard.click();
    }
    return;
  }
  const trendDrill = event.target.closest("[data-trend-drill]");
  if (!trendDrill || trendDrill.closest("[data-drill-panel]")) return;
  event.preventDefault();
  overviewDrillReturnFocus = "";
  openTrendDrill(trendDrill);
});

root.addEventListener("input", (event) => {
  const pickerSearch = event.target.closest("[data-filter-picker-search]");
  if (pickerSearch) {
    applyFilterPickerSearch(pickerSearch.closest("[data-filter-picker]"), pickerSearch.value);
    return;
  }
  const search = event.target.closest("[data-overview-campaign-search]");
  if (!search) return;
  applyFilterPickerSearch(search.closest("[data-filter-picker]"), search.value);
});

root.addEventListener("change", (event) => {
  const pickerOption = event.target.closest("[data-filter-picker-option]");
  if (pickerOption) {
    syncFilterPickerSummary(pickerOption.closest("[data-filter-picker]"));
    return;
  }
  const overviewAuto = event.target.closest("[data-overview-autosubmit]");
  if (overviewAuto) {
    const form = overviewAuto.closest("[data-overview-filters]");
    if (form instanceof HTMLFormElement) {
      const name = overviewAuto.getAttribute("name");
      const compare = form.elements.compare;
      const compareMonth = form.elements.compare_month_start;
      if (name === "compare" && compareMonth instanceof HTMLSelectElement) {
        if (overviewAuto.value === "none") compareMonth.value = "none";
        else if (compareMonth.value === "none") compareMonth.value = "";
      }
      if (name === "compare_month_start" && compare instanceof HTMLSelectElement) {
        if (overviewAuto.value === "none") compare.value = "none";
        else compare.value = "auto";
      }
      form.requestSubmit();
    }
    return;
  }
  const trendAuto = event.target.closest("[data-overview-trend-autosubmit]");
  if (trendAuto) {
    const form = trendAuto.closest("[data-overview-trend-form]");
    if (form instanceof HTMLFormElement) form.requestSubmit();
    return;
  }
  const findingGrain = event.target.closest("[data-finding-grain]");
  if (findingGrain) {
    const query = new URLSearchParams(window.location.search);
    const value = String(findingGrain.value || "month");
    if (value === "month") query.delete("finding_grain");
    else query.set("finding_grain", value);
    setOverviewHost("[data-overview-insights-host]", overviewHostLoading());
    setOverviewHost("[data-overview-anomalies-host]", overviewHostLoading());
    navigate(overviewHref(query));
    return;
  }
  const drillAuto = event.target.closest("[data-overview-drill-autosubmit]");
  if (drillAuto) {
    const form = drillAuto.closest("[data-overview-drill-form]");
    if (form instanceof HTMLFormElement) form.requestSubmit();
    return;
  }
  const explorerAuto = event.target.closest("[data-overview-explorer-autosubmit]");
  if (explorerAuto) {
    const form = explorerAuto.closest("[data-overview-explorer-form]");
    if (form instanceof HTMLFormElement) form.requestSubmit();
    return;
  }
  const companySelect = event.target.closest("[data-company-select]");
  if (companySelect) {
    bindSelectedCompany(companySelect.value);
    return;
  }
  const input = event.target.closest("[data-file-input]");
  if (!input || input.type !== "file") return;
  const meta = input.closest("form")?.querySelector("[data-file-chosen]");
  if (!meta) return;
  const file = input.files && input.files[0];
  if (!input.files || !input.files.length) {
    meta.textContent = "No file selected.";
    return;
  }
  if (input.files.length > 1) {
    meta.textContent = `${input.files.length} files selected.`;
    return;
  }
  const size = file.size < 1024 ? `${file.size} B` : `${Math.round(file.size / 1024)} KB`;
  meta.textContent = `${file.name} · ${file.type || "xlsx"} · ${size}`;
});

root.addEventListener("submit", async (event) => {
  const form = event.target;
  if (!(form instanceof HTMLFormElement)) return;
  if (form.dataset.overviewFilters === "true") {
    event.preventDefault();
    navigate(overviewHref(queryFromOverviewForm(form)));
    return;
  }
  if (form.dataset.overviewTrendForm === "true") {
    event.preventDefault();
    navigate(overviewHref(queryFromTrendForm(form, currentLocation().query)));
    return;
  }
  if (form.dataset.overviewExplorerForm === "true") {
    event.preventDefault();
    navigate(overviewHref(queryFromExplorerForm(form, currentLocation().query)));
    return;
  }
  if (form.dataset.savedForm === "true") {
    event.preventDefault();
    const title = String(new FormData(form).get("title") || "").trim();
    const submit = form.querySelector("[data-saved-submit]");
    if (submit) submit.disabled = true;
    api
      .createSavedAnalysis({ title, state: workspaceStateFromQuery(currentLocation().query) })
      .then((record) => {
        showToast({ tone: "success", title: "Analysis saved." });
        overviewSavedDirty = true;
        const query = currentLocation().query;
        query.set("saved", record.id);
        navigate(overviewHref(query));
      })
      .catch((error) => {
        if (isAuthError(error) && error.status === 401) {
          handleError(error, currentLocation().path);
          return;
        }
        showToast({ tone: "warning", title: "Could not save analysis.", message: error.message || "" });
      })
      .finally(() => {
        if (submit) submit.disabled = false;
      });
    return;
  }
  if (form.dataset.generateTrendForm === "true") {
    event.preventDefault();
    submitGenerateTrendFrom(form);
    return;
  }
  if (form.dataset.askAiForm === "true") {
    event.preventDefault();
    submitAskAiFrom(form);
    return;
  }
  if (form.dataset.overviewAskForm === "true") {
    event.preventDefault();
    const host = document.querySelector("[data-ask-result]");
    const submit = form.querySelector("[data-ask-submit]");
    if (host) host.innerHTML = toHtml(html`<div data-ask-loading="true">${loadingState()}</div>`);
    if (submit) submit.disabled = true;
    const payload = askPayloadFromForm(form, currentLocation().query);
    api
      .postOverviewAsk(payload)
      .then((body) => {
        if (host) host.innerHTML = toHtml(overviewAskResultView(body));
        applyGeneratedTrendSelection(body);
      })
      .catch((error) => {
        if (isAuthError(error)) {
          handleError(error, currentLocation().path);
          return;
        }
        if (host) host.innerHTML = toHtml(overviewAskResultView(null, error));
      })
      .finally(() => {
        if (submit) submit.disabled = false;
      });
    return;
  }
  if (form.dataset.overviewDrillForm === "true") {
    event.preventDefault();
    const parsed = parseDrillQuery(currentLocation().query);
    if (!parsed) return;
    const dimension = String(new FormData(form).get("drill_dimension") || parsed.dimension);
    const parents = [];
    for (const token of parsed.parents) {
      const dim = String(token).split(":")[0];
      if (dim === dimension) break;
      parents.push(token);
    }
    navigate(
      overviewHref(
        withDrill(currentLocation().query, {
          origin: parsed.origin,
          metric: parsed.metric,
          dimension,
          parents,
          slice: parsed.slice,
          sliceGrain: parsed.sliceGrain,
        }),
      ),
    );
    return;
  }
  if (form.dataset.publisherSetupForm === "true") {
    event.preventDefault();
    const data = new FormData(form);
    const username = String(data.get("username") || "").trim();
    const password = String(data.get("password") || "");
    const confirmPassword = String(data.get("confirm_password") || "");
    const next = String(data.get("next") || "/admin");
    if (password !== confirmPassword) {
      root.innerHTML = toHtml(
        credentialView({
          error: { message: "Password confirmation does not match." },
          nextPath: next,
          setupRequired: true,
        }),
      );
      return;
    }
    try {
      const created = await api.setupPublisher(username, password, confirmPassword);
      publisherSetupDone = true;
      showToast({
        tone: "success",
        title: "Publisher account created.",
        message: `${created.username} can now sign in. DFIP stored a password hash only.`,
      });
      window.dispatchEvent(new Event("dfip:navigate"));
    } catch (error) {
      handleError(error, "/");
    }
    return;
  }
  if (form.dataset.credentialForm === "true") {
    event.preventDefault();
    const username = String(new FormData(form).get("username") || "").trim();
    const password = String(new FormData(form).get("password") || "");
    const next = String(new FormData(form).get("next") || "/client/overview");
    try {
      const result = await api.login(username, password);
      storeToken(result.access_token);
      session = result.session || (await api.session());
      if (canAccessAdmin(session.role) && (!next || next === "/client" || next === "/client/overview")) {
        navigate("/admin");
      } else {
        navigate(next.startsWith("/") ? next : "/client/overview");
      }
    } catch (error) {
      clearToken();
      session = null;
      root.innerHTML = toHtml(credentialView({ error, nextPath: next }));
    }
    return;
  }
  if (form.dataset.companySelectForm === "true") {
    event.preventDefault();
    const clientId = String(new FormData(form).get("client_id") || "").trim();
    await bindSelectedCompany(clientId);
    return;
  }
  if (form.dataset.companyCreateForm === "true") {
    event.preventDefault();
    const name = String(new FormData(form).get("name") || "").trim();
    if (!name) {
      showToast({
        tone: "error",
        title: "Company name is required.",
        message: "Enter a company display name.",
      });
      return;
    }
    const confirmed = await confirmAction({
      title: "Add this company?",
      message: "This creates a new company with a new company id. Existing companies are not changed.",
      confirmLabel: "Add Company",
    });
    if (!confirmed) return;
    try {
      const created = await api.createClient(name);
      const selected = await api.selectClient(created.client_id);
      storeToken(selected.access_token);
      const refreshed = await api.session();
      session = refreshed;
      showToast({
        tone: "success",
        title: "Company added.",
        message: "This company is now selected. You can create a client account or upload without switching first.",
      });
      window.dispatchEvent(new Event("dfip:navigate"));
    } catch (error) {
      handleError(error, "/admin/companies");
    }
    return;
  }
  if (form.dataset.companyClientForm === "true") {
    event.preventDefault();
    const data = new FormData(form);
    const clientId = String(data.get("client_id") || "").trim();
    const username = String(data.get("username") || "").trim();
    const password = String(data.get("password") || "");
    const confirmPassword = String(data.get("confirm_password") || "");
    if (password !== confirmPassword) {
      showToast({
        tone: "error",
        title: "Passwords do not match.",
        message: "Enter the same client password in both fields. DFIP does not generate credentials.",
      });
      return;
    }
    if (password.length < MIN_PASSWORD_LENGTH) {
      showToast({
        tone: "error",
        title: "Password is too short.",
        message: `Client passwords must be at least ${MIN_PASSWORD_LENGTH} characters.`,
      });
      return;
    }
    const confirmed = await confirmAction({
      title: "Create a client account?",
      message: "This creates a client-portal user for this company only. The password is chosen by you and is stored as a hash.",
      confirmLabel: "Create Client Account",
    });
    if (!confirmed) return;
    try {
      const created = await api.createClientUser(clientId, username, password, confirmPassword);
      const usernameInput = form.querySelector('[name="username"]');
      const passwordInput = form.querySelector('[name="password"]');
      const confirmInput = form.querySelector('[name="confirm_password"]');
      if (usernameInput instanceof HTMLInputElement) usernameInput.value = "";
      if (passwordInput instanceof HTMLInputElement) passwordInput.value = "";
      if (confirmInput instanceof HTMLInputElement) confirmInput.value = "";
      showToast({
        tone: "success",
        title: "Client account created.",
        message: `${created.username} can sign in to the client portal for this company. DFIP stored a password hash only.`,
      });
    } catch (error) {
      handleError(error, "/admin/companies");
    }
    return;
  }
  if (form.dataset.companyRenameForm === "true") {
    event.preventDefault();
    const data = new FormData(form);
    const clientId = String(data.get("client_id") || "").trim();
    const name = String(data.get("name") || "").trim();
    const confirmed = await confirmAction({
      title: "Rename this company?",
      message: "This changes the display name only. The company id, publications, and facts stay the same.",
      confirmLabel: "Rename",
    });
    if (!confirmed) return;
    try {
      await api.renameClient(clientId, name);
      const refreshed = await api.session();
      session = refreshed;
      showToast({
        tone: "success",
        title: "Company renamed.",
        message: "The display name was updated. The company id is unchanged.",
      });
      window.dispatchEvent(new Event("dfip:navigate"));
    } catch (error) {
      handleError(error, "/admin/companies");
    }
    return;
  }
  if (form.dataset.filterForm === "true") {
    event.preventDefault();
    const data = new FormData(form);
    const params = new URLSearchParams();
    for (const [key, value] of data.entries()) {
      if (value === "") continue;
      params.set(key, String(value));
    }
    params.set("offset", "0");
    navigate(`${form.getAttribute("action")}?${params.toString()}`);
    return;
  }
  if (form.dataset.uploadForm === "true") {
    event.preventDefault();
    const data = new FormData(form);
    const input = form.querySelector("[data-file-input]");
    const picked = input && input.files ? Array.from(input.files) : [];
    const file = picked.length > 1 ? picked : picked[0] || data.get("file") || data.get("files");
    const clientId = String(data.get("client_id") || "").trim();
    const force = data.get("force") === "on";
    const submit = form.querySelector("button[type=submit]");
    const sized = picked.length ? picked : collectFormFiles(data, "files");
    if (rejectIfOversize(sized.length ? sized : file instanceof File ? [file] : [])) {
      return;
    }
    if (submit) submit.disabled = true;
    try {
      const uploadResult = await api.uploadWorkbook(file, { clientId, force });
      const life = rawUploadLifecycle(uploadResult);
      if (life === "queued" || life === "processing") {
        savePendingUpload(pendingFromUpload(uploadResult, clientId));
        showToast({
          tone: "info",
          title: "Upload accepted.",
          message: "Processing continues in the background. You can navigate away.",
        });
        await reloadUpload({ uploadResult, clientId });
        resumePendingUploadPoll();
        return;
      }
      clearPendingUpload();
      showToast({
        tone: life === "failed" ? "error" : "success",
        title:
          life === "failed" ? "Processing failed." : "Upload completed successfully.",
        message: uploadRunOf(uploadResult)
          ? "Processing completed. Publication required."
          : life === "failed"
            ? (uploadResult.batch && uploadResult.batch.error_summary) ||
              "The workbook could not be processed."
            : "Upload completed. Processing did not produce a succeeded run.",
      });
      await reloadUpload({ uploadResult, clientId });
    } catch (error) {
      if (isAuthError(error)) {
        handleError(error, "/admin/upload");
        return;
      }
      try {
        await reloadUpload({ error, clientId });
      } catch (inner) {
        handleError(inner, "/admin/upload");
      }
    } finally {
      if (submit) submit.disabled = false;
    }
    return;
  }
  if (form.dataset.publishForm === "true") {
    event.preventDefault();
    const data = new FormData(form);
    const payload = {
      client_id: String(data.get("client_id") || "").trim(),
      processing_run_id: String(data.get("processing_run_id") || "").trim(),
    };
    const confirmed = await confirmAction({
      title: "Publish this run?",
      message: `This sets the current publication pointer to run ${payload.processing_run_id}. Published downloads and Excel follow this pointer. Prior publications remain in history. This is not reversible by another automatic step.`,
      confirmLabel: "Publish Run",
    });
    if (!confirmed) return;
    const periodStart = String(data.get("period_start") || "").trim();
    const periodEnd = String(data.get("period_end") || "").trim();
    const notes = String(data.get("notes") || "").trim();
    if (periodStart) payload.period_start = periodStart;
    if (periodEnd) payload.period_end = periodEnd;
    if (notes) payload.notes = notes;
    const factScope = String(data.get("fact_scope") || "processing_run").trim();
    if (factScope) payload.fact_scope = factScope;
    const submit = form.querySelector("button[type=submit]");
    if (submit) submit.disabled = true;
    startPublicationProgressPoll(payload.processing_run_id, payload.client_id);
    try {
      const publishResult = await api.createPublication(payload);
      paintPublishProgress({
        stage: "published",
        status: "succeeded",
        message: "Published",
        progress_percent: 100,
        publication_id: publishResult.publication && publishResult.publication.publication_id,
      });
      showToast({ tone: "success", title: "Publication created successfully." });
      await reloadPublications({ publishResult, clientId: payload.client_id });
    } catch (error) {
      stopPublishPoll();
      paintPublishProgress({
        stage: "failed",
        status: "failed",
        message: "Publication failed",
        error_summary: error && error.message ? error.message : "Publication failed.",
      });
      if (isAuthError(error)) {
        handleError(error, "/admin/publications");
        return;
      }
      try {
        await reloadPublications({ error, clientId: payload.client_id });
      } catch (inner) {
        handleError(inner, "/admin/publications");
      }
    } finally {
      stopPublishPoll();
      if (submit) submit.disabled = false;
    }
    return;
  }
  if (form.dataset.catalogUploadForm === "true") {
    event.preventDefault();
    const data = new FormData(form);
    const file = data.get("file");
    const kind = String(data.get("kind") || "logic");
    const clientId = String(data.get("client_id") || "").trim();
    const path = catalogPath(kind);
    if (file instanceof File && rejectIfOversize([file])) {
      return;
    }
    try {
      const uploadResult = await api.uploadCatalog(kind, file, { clientId });
      showToast({
        tone: "success",
        title: kind === "labels" ? "Labels upload completed successfully." : "Logic upload completed successfully.",
      });
      await loadCatalogView(kind, { uploadResult });
    } catch (error) {
      if (isAuthError(error)) {
        handleError(error, path);
        return;
      }
      try {
        await loadCatalogView(kind, { error });
      } catch (inner) {
        handleError(inner, path);
      }
    }
  }
});

window.addEventListener("popstate", () => {
  window.dispatchEvent(new Event("dfip:navigate"));
});
window.addEventListener("dfip:navigate", () => {
  renderRoute();
});

async function boot() {
  try {
    const response = await fetch("/config.json", { credentials: "omit" });
    if (response.ok) {
      const loaded = await response.json();
      config = { ...config, ...loaded };
    }
  } catch (error) {
    root.innerHTML = toHtml(
      errorBanner({
        code: "NETWORK_FAILURE",
        message: "The web application could not load /config.json.",
      }),
    );
    return;
  }
  setAdminRoles(config.adminRoles);
  api = new DfipApiClient({
    baseUrl: config.apiBaseUrl,
    prefix: config.apiPrefix,
    getToken: getStoredToken,
  });
  await renderRoute();
}

boot();
