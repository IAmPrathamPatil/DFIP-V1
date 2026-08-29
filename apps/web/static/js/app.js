import { DfipApiClient, ApiError } from "./api-client.js";
import { clearToken, getStoredToken, storeToken } from "./auth.js";
import { layout } from "./components.js";
import { confirmAction, showToast } from "./dialogs.js";
import { errorBanner, html, loadingState, toHtml } from "./format.js";
import { currentLocation, matchRoute, navigate } from "./router.js";
import { canAccessAdmin, canAccessClient, setAdminRoles } from "./roles.js";
import {
  adminHomeView,
  batchDetailView,
  batchListView,
  catalogView,
  clientFactDetailView,
  clientFactListView,
  clientHomeView,
  credentialView,
  downloadsView,
  factDetailView,
  factListView,
  historyListView,
  notFoundView,
  pageParams,
  publicationsView,
  rawUploadBanner,
  rawUploadLifecycle,
  reviewView,
  runDetailView,
  runListView,
  sourceFileDetailView,
  sourceFileListView,
  unauthorizedView,
  uploadCenterView,
} from "./views.js";

const root = document.getElementById("app");
let config = { apiBaseUrl: "http://127.0.0.1:8000", apiPrefix: "/api/v1", adminRoles: ["admin", "publisher"] };
let api;
let session = null;
const PENDING_UPLOAD_KEY = "dfip.pendingRawUpload";
const UPLOAD_POLL_MS = 2000;
let uploadPollTimer = null;
let runPollTimer = null;

const routes = [
  { pattern: /^\/$/, name: "credential", access: "public" },
  { pattern: /^\/unauthorized$/, name: "unauthorized", access: "auth" },
  { pattern: /^\/sign-out$/, name: "sign-out", access: "public" },
  { pattern: /^\/admin$/, name: "admin-home", access: "admin" },
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
  { pattern: /^\/client\/facts$/, name: "client-facts", access: "client" },
  { pattern: /^\/client\/facts\/detail$/, name: "client-fact-detail", access: "client" },
];

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
  return life === "succeeded" || life === "failed";
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
    if (item.status === "succeeded" || item.status === "failed") {
      stopRunPoll();
      showToast({
        tone: item.status === "succeeded" ? "success" : "error",
        title: item.status === "succeeded" ? "Re-process completed." : "Re-process failed.",
        message:
          item.status === "succeeded"
            ? "A new processing run succeeded. Publication required."
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
      if (rawUploadLifecycle(uploadResult) === "succeeded") {
        showToast({
          tone: "success",
          title: "Upload completed successfully.",
          message: uploadRunOf(uploadResult)
            ? "Processing completed. Publication required."
            : "Upload completed. Processing did not produce a succeeded run.",
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
  const refreshed = await api.refresh();
  if (refreshed && refreshed.access_token) {
    storeToken(refreshed.access_token);
  }
  session = refreshed && refreshed.session ? refreshed.session : await api.session();
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
            navigate("/client");
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
    if (route.name === "admin-upload") {
      resumePendingUploadPoll();
    }
    if (route.name === "admin-run" && body) {
      resumePendingRunPoll(params.id);
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
  if (name === "credential") return credentialView({ nextPath: query.get("next") || "/client" });
  if (name === "unauthorized") return unauthorizedView();
  if (name === "admin-home") {
    const health = await safeRead(() => api.health());
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
    return uploadCenterView({ session, query, logicPage, labelsPage, uploadResult, loading: false });
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
  if (name === "client-home") {
    const currentPublication = await safeRead(() => api.getCurrentPublication(publishedParams({})));
    const publications = await safeRead(() =>
      api.listPublications({ ...publishedParams({}), limit: 8, offset: 0 }),
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
      uploadCenterView({
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

async function runBatchReprocess(button) {
  const batchId = button.getAttribute("data-batch-reprocess");
  const clientId =
    button.getAttribute("data-batch-client") ||
    (session && session.client_id) ||
    currentLocation().query.get("client_id") ||
    "";
  const confirmed = await confirmAction({
    title: "Re-process this batch?",
    message:
      "Create a new processing run from the existing staged rows using the currently active Logic and Labels. The previous run is kept. This does not upload Raw again and does not publish.",
    confirmLabel: "Re-process",
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
    uploadCenterView({
      session,
      query: currentLocation().query,
      logicPage,
      labelsPage,
      uploadResult,
      error,
      loading: false,
    }),
    "/admin/upload",
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
  const copy = event.target.closest("[data-copy]");
  if (copy) {
    event.preventDefault();
    const value = copy.getAttribute("data-copy") || "";
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(value).then(
        () => showToast({ tone: "info", title: "Copied identifier." }),
        () => {},
      );
    }
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
        showToast({ tone: "success", title: "Client report downloaded." });
      })
      .catch((error) => handleError(error, path));
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
  const batchReprocess = event.target.closest("[data-batch-reprocess]");
  if (batchReprocess) {
    event.preventDefault();
    runBatchReprocess(batchReprocess);
    return;
  }
  const link = event.target.closest("a");
  if (!link || link.target === "_blank" || link.origin !== window.location.origin) return;
  if (link.hash && !link.pathname) return;
  event.preventDefault();
  document.querySelector(".shell")?.classList.remove("nav-open");
  navigate(`${link.pathname}${link.search}`);
});

root.addEventListener("change", (event) => {
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
  if (form.dataset.credentialForm === "true") {
    event.preventDefault();
    const username = String(new FormData(form).get("username") || "").trim();
    const password = String(new FormData(form).get("password") || "");
    const next = String(new FormData(form).get("next") || "/client");
    try {
      const result = await api.login(username, password);
      storeToken(result.access_token);
      session = result.session || (await api.session());
      if (canAccessAdmin(session.role) && (!next || next === "/client")) {
        navigate("/admin");
      } else {
        navigate(next.startsWith("/") ? next : "/client");
      }
    } catch (error) {
      clearToken();
      session = null;
      root.innerHTML = toHtml(credentialView({ error, nextPath: next }));
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
    try {
      const publishResult = await api.createPublication(payload);
      showToast({ tone: "success", title: "Publication created successfully." });
      await reloadPublications({ publishResult, clientId: payload.client_id });
    } catch (error) {
      if (isAuthError(error)) {
        handleError(error, "/admin/publications");
        return;
      }
      try {
        await reloadPublications({ error, clientId: payload.client_id });
      } catch (inner) {
        handleError(inner, "/admin/publications");
      }
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
      config = await response.json();
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
