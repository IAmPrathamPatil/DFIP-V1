export class ApiError extends Error {
  constructor(status, code, message, details) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details || null;
  }
}

export const PUBLISHER_SESSION_REPLACED = "PUBLISHER_SESSION_REPLACED";

export class DfipApiClient {
  constructor({ baseUrl, prefix, getToken, getPublisherSession, onPublisherSessionReplaced }) {
    this.baseUrl = String(baseUrl || "").replace(/\/$/, "");
    this.prefix = "/" + String(prefix || "/api/v1").replace(/^\/+|\/+$/g, "");
    this.getToken = getToken;
    // Both are optional. Without them the client behaves exactly as before and
    // never sends a Publisher session header, which is the Client/Reader path.
    this.getPublisherSession = getPublisherSession || null;
    this.onPublisherSessionReplaced = onPublisherSessionReplaced || null;
  }

  health() {
    return this.request("GET", "/health", { auth: false });
  }

  ready() {
    return this.request("GET", `${this.prefix}/ops/ready`, { acceptStatuses: [503] });
  }

  session() {
    return this.request("GET", `${this.prefix}/session`);
  }

  login(username, password, clientId) {
    const body = { username, password };
    if (clientId) body.client_id = clientId;
    return this.request("POST", `${this.prefix}/auth/login`, { auth: false, body });
  }

  setupStatus() {
    return this.request("GET", `${this.prefix}/auth/setup-status`, { auth: false });
  }

  setupPublisher(username, password, confirmPassword) {
    return this.request("POST", `${this.prefix}/auth/setup-publisher`, {
      auth: false,
      body: { username, password, confirm_password: confirmPassword },
    });
  }

  selectClient(clientId) {
    return this.request("POST", `${this.prefix}/auth/select-client`, {
      body: { client_id: clientId },
    });
  }

  listClients(params = {}) {
    const query = new URLSearchParams();
    if (params.include_inactive) query.set("include_inactive", "true");
    const suffix = query.toString() ? `?${query.toString()}` : "";
    return this.request("GET", `${this.prefix}/clients${suffix}`);
  }

  renameClient(clientId, name) {
    return this.request("POST", `${this.prefix}/clients/${clientId}/rename`, {
      body: { name },
    });
  }

  createClient(name) {
    return this.request("POST", `${this.prefix}/clients`, {
      body: { name },
    });
  }

  createClientUser(clientId, username, password, confirmPassword) {
    return this.request("POST", `${this.prefix}/clients/${clientId}/users`, {
      body: { username, password, confirm_password: confirmPassword, role: "client" },
    });
  }

  deactivateClient(clientId) {
    return this.request("POST", `${this.prefix}/clients/${clientId}/deactivate`);
  }

  reactivateClient(clientId) {
    return this.request("POST", `${this.prefix}/clients/${clientId}/reactivate`);
  }

  deleteClient(clientId) {
    return this.request("DELETE", `${this.prefix}/clients/${clientId}`);
  }

  logout() {
    return this.request("POST", `${this.prefix}/auth/logout`);
  }

  refresh() {
    return this.request("POST", `${this.prefix}/auth/refresh`);
  }

  // Bootstrap: sends no Publisher session header because it is the request
  // that creates the session. Taking over the previous session is intentional.
  openPublisherSession() {
    return this.request("POST", `${this.prefix}/auth/publisher-session`, {
      skipPublisherSession: true,
    });
  }

  // Carries the active Publisher session header.
  heartbeatPublisherSession() {
    return this.request("POST", `${this.prefix}/auth/publisher-session/heartbeat`);
  }

  listSourceFiles(params) {
    return this.request("GET", `${this.prefix}/source-files`, { params });
  }

  getSourceFile(id) {
    return this.request("GET", `${this.prefix}/source-files/${id}`);
  }

  listBatches(params) {
    return this.request("GET", `${this.prefix}/batches`, { params });
  }

  getBatch(id) {
    return this.request("GET", `${this.prefix}/batches/${id}`);
  }

  processBatch(id, params) {
    return this.request("POST", `${this.prefix}/batches/${id}/process`, { params });
  }

  cancelBatch(id, params) {
    return this.request("POST", `${this.prefix}/batches/${id}/cancel`, { params });
  }

  deleteBatch(id, params) {
    return this.request("DELETE", `${this.prefix}/batches/${id}`, { params });
  }

  listStagedRows(batchId, params) {
    return this.request("GET", `${this.prefix}/batches/${batchId}/staged-rows`, { params });
  }

  listProcessingRuns(params) {
    return this.request("GET", `${this.prefix}/processing-runs`, { params });
  }

  getProcessingRun(id) {
    return this.request("GET", `${this.prefix}/processing-runs/${id}`);
  }

  evaluateProcessingRunQa(id, params) {
    return this.request("POST", `${this.prefix}/processing-runs/${id}/qa`, { params });
  }

  listQaFindings(id, params) {
    return this.request("GET", `${this.prefix}/processing-runs/${id}/qa-findings`, { params });
  }

  listFacts(params) {
    return this.request("GET", `${this.prefix}/facts`, { params });
  }

  listFactHistory(params) {
    return this.request("GET", `${this.prefix}/facts/history`, { params });
  }

  createPublication(body) {
    return this.request("POST", `${this.prefix}/publications`, { body });
  }

  getPublicationProgress(params) {
    return this.request("GET", `${this.prefix}/publications/progress`, { params });
  }

  listPublications(params) {
    return this.request("GET", `${this.prefix}/publications`, { params });
  }

  getCurrentPublication(params) {
    return this.request("GET", `${this.prefix}/publications/current`, { params });
  }

  getOverviewKpis(params) {
    return this.request("GET", `${this.prefix}/analytics/overview`, { params });
  }

  getOverviewTrends(params) {
    return this.request("GET", `${this.prefix}/analytics/trends`, { params });
  }

  getOverviewKpiSparklines(params) {
    return this.getOverviewTrends(params);
  }

  getOverviewDrilldown(params) {
    return this.request("GET", `${this.prefix}/analytics/drilldown`, { params });
  }

  getOverviewExplorer(params) {
    return this.request("GET", `${this.prefix}/analytics/explorer`, { params });
  }

  downloadExplorerExport(params) {
    return this.request("GET", `${this.prefix}/analytics/explorer.csv`, { params, blob: true });
  }

  getOverviewInsights(params) {
    return this.request("GET", `${this.prefix}/analytics/insights`, { params });
  }

  getOverviewAnomalies(params) {
    return this.request("GET", `${this.prefix}/analytics/anomalies`, { params });
  }

  postOverviewAsk(body) {
    return this.request("POST", `${this.prefix}/analytics/ask`, { body });
  }

  listSavedAnalyses() {
    return this.request("GET", `${this.prefix}/analytics/saved`);
  }

  createSavedAnalysis(body) {
    return this.request("POST", `${this.prefix}/analytics/saved`, { body });
  }

  getSavedAnalysis(id) {
    return this.request("GET", `${this.prefix}/analytics/saved/${id}`);
  }

  updateSavedAnalysis(id, body) {
    return this.request("POST", `${this.prefix}/analytics/saved/${id}`, { body });
  }

  deleteSavedAnalysis(id) {
    return this.request("DELETE", `${this.prefix}/analytics/saved/${id}`);
  }

  downloadOverviewExport(params) {
    return this.request("GET", `${this.prefix}/analytics/export.csv`, { params, blob: true });
  }

  listPublishedFacts(params) {
    return this.request("GET", `${this.prefix}/publications/current/facts`, { params });
  }

  uploadWorkbook(file, { clientId, force } = {}) {
    const list = _asFileList(file);
    const body = new FormData();
    if (list.length > 1) {
      for (const item of list) {
        body.append("files", item, item && item.name ? item.name : "workbook.xlsx");
      }
    } else {
      const one = list[0];
      body.append("file", one, one && one.name ? one.name : "workbook.xlsx");
    }
    if (clientId) body.append("client_id", clientId);
    if (force) body.append("force", "true");
    return this.request("POST", `${this.prefix}/uploads`, { body, multipart: true });
  }

  listCatalogs(kind, params) {
    return this.request("GET", `${this.prefix}/catalogs/${kind}`, { params });
  }

  getCatalogVersion(kind, id, params) {
    return this.request("GET", `${this.prefix}/catalogs/${kind}/${id}`, { params });
  }

  uploadCatalog(kind, file, { clientId } = {}) {
    const body = new FormData();
    body.append("file", file, file && file.name ? file.name : "catalog.xlsx");
    if (clientId) body.append("client_id", clientId);
    return this.request("POST", `${this.prefix}/catalogs/${kind}`, { body, multipart: true });
  }

  activateCatalog(kind, id, params) {
    return this.request("POST", `${this.prefix}/catalogs/${kind}/${id}/activate`, { params });
  }

  deactivateCatalog(kind, id, params) {
    return this.request("POST", `${this.prefix}/catalogs/${kind}/${id}/deactivate`, { params });
  }

  async downloadCatalog(kind, id, params) {
    const payload = await this.request("GET", `${this.prefix}/catalogs/${kind}/${id}/download`, {
      params,
      blob: true,
    });
    return payload;
  }

  async downloadPublishedFacts(kind, params) {
    if (kind !== "csv" && kind !== "xlsx") {
      throw new ApiError(422, "VALIDATION_ERROR", "Download format must be csv or xlsx.");
    }
    const suffix = kind === "xlsx" ? "facts.xlsx" : "facts.csv";
    const payload = await this.request("GET", `${this.prefix}/publications/current/${suffix}`, {
      params,
      blob: true,
    });
    return payload;
  }

  async downloadClientReport(publicationId, params) {
    const path = publicationId
      ? `${this.prefix}/publications/${publicationId}/client-report.xlsx`
      : `${this.prefix}/publications/current/client-report.xlsx`;
    return this.request("GET", path, { params, blob: true });
  }

  async downloadConsolidatedClientReport(params) {
    return this.request("GET", `${this.prefix}/publications/current/consolidated-client-report.xlsx`, {
      params,
      blob: true,
      acceptStatuses: [202],
    });
  }

  async downloadRefreshableClientReport(params, { onHeaders } = {}) {
    return this.request("GET", `${this.prefix}/publications/current/refreshable-client-report.xlsx`, {
      params,
      blob: true,
      onHeaders,
    });
  }

  async request(
    method,
    path,
    {
      params,
      auth = true,
      body,
      multipart = false,
      blob = false,
      acceptStatuses = [],
      onHeaders,
      skipPublisherSession = false,
    } = {},
  ) {
    const url = new URL(path, `${this.baseUrl}/`);
    if (params) {
      for (const [key, value] of Object.entries(params)) {
        if (value === undefined || value === null) continue;
        if (Array.isArray(value)) {
          for (const item of value) {
            if (item === undefined || item === null) continue;
            url.searchParams.append(key, String(item));
          }
          continue;
        }
        if (value === "") continue;
        url.searchParams.set(key, String(value));
      }
    }
    const headers = { Accept: blob ? "*/*" : "application/json" };
    if (auth) {
      const token = this.getToken ? this.getToken() : "";
      if (!token) {
        throw new ApiError(401, "AUTHENTICATION_FAILED", "Authentication required.");
      }
      headers.Authorization = `Bearer ${token}`;
      // Single injection point: every authenticated request, including blob
      // downloads, multipart uploads, exports, and Analytics Studio, goes
      // through here. The getter returns null for a Client/Reader, before the
      // session is established, and after this page is marked replaced.
      if (!skipPublisherSession && this.getPublisherSession) {
        const publisherSession = this.getPublisherSession();
        if (publisherSession && publisherSession.value) {
          headers[publisherSession.name] = publisherSession.value;
        }
      }
    }
    const options = { method, headers, credentials: "omit" };
    if (body !== undefined) {
      if (multipart) {
        options.body = body;
      } else {
        headers["Content-Type"] = "application/json";
        options.body = JSON.stringify(body);
      }
    }
    let response;
    try {
      response = await fetch(url, options);
    } catch (error) {
      throw new ApiError(0, "NETWORK_FAILURE", "Network failure contacting the API.");
    }
    if (typeof onHeaders === "function") {
      onHeaders(response);
    }
    if (!response.ok && !acceptStatuses.includes(response.status)) {
      let payload = null;
      const text = await response.text();
      if (text) {
        try {
          payload = JSON.parse(text);
        } catch (error) {
          throw new ApiError(response.status, "INTERNAL_ERROR", "Invalid JSON response.");
        }
      }
      const error = payload && payload.error ? payload.error : {};
      const apiError = new ApiError(
        response.status,
        error.code || "INTERNAL_ERROR",
        error.message || "Request failed.",
        error.details || null,
      );
      // Terminal for this page. Reported before the throw so a poll that never
      // awaits the result still stops.
      if (
        apiError.code === PUBLISHER_SESSION_REPLACED &&
        typeof this.onPublisherSessionReplaced === "function"
      ) {
        this.onPublisherSessionReplaced(apiError);
      }
      throw apiError;
    }
    if (blob) {
      // An accepted non-200 status on a blob request means there is no file
      // yet. The consolidated workbook uses 202 to say "being prepared", so
      // report that to the caller instead of handing back the JSON body as if
      // it were a workbook.
      if (response.status !== 200) {
        let detail = null;
        try {
          const text = await response.text();
          detail = text ? JSON.parse(text) : null;
        } catch (error) {
          detail = null;
        }
        return {
          preparing: true,
          status: response.status,
          message: (detail && detail.message) || "The workbook is being prepared.",
          retryAfterSeconds: Number(response.headers.get("Retry-After")) || 5,
        };
      }
      let blobBody;
      try {
        blobBody = await response.blob();
      } catch (error) {
        throw new ApiError(0, "NETWORK_FAILURE", "The download was interrupted before the file finished.");
      }
      const disposition = response.headers.get("Content-Disposition") || "";
      const matched = /filename=\"([^\"]+)\"/.exec(disposition);
      return { blob: blobBody, filename: matched ? matched[1] : `published-facts.${kindFromPath(path)}` };
    }
    if (response.status === 204) {
      return {};
    }
    let payload = null;
    const text = await response.text();
    if (text) {
      try {
        payload = JSON.parse(text);
      } catch (error) {
        throw new ApiError(response.status, "INTERNAL_ERROR", "Invalid JSON response.");
      }
    }
    return payload;
  }
}

function kindFromPath(path) {
  if (String(path).endsWith(".xlsx")) return "xlsx";
  return "csv";
}

function _asFileList(file) {
  if (!file) return [];
  if (Array.isArray(file)) return file.filter(Boolean);
  if (typeof file.length === "number" && typeof file.item === "function") {
    return Array.from(file).filter(Boolean);
  }
  return [file];
}
