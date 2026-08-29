export class ApiError extends Error {
  constructor(status, code, message, details) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details || null;
  }
}

export class DfipApiClient {
  constructor({ baseUrl, prefix, getToken }) {
    this.baseUrl = String(baseUrl || "").replace(/\/$/, "");
    this.prefix = "/" + String(prefix || "/api/v1").replace(/^\/+|\/+$/g, "");
    this.getToken = getToken;
  }

  health() {
    return this.request("GET", "/health", { auth: false });
  }

  session() {
    return this.request("GET", `${this.prefix}/session`);
  }

  login(username, password, clientId) {
    const body = { username, password };
    if (clientId) body.client_id = clientId;
    return this.request("POST", `${this.prefix}/auth/login`, { auth: false, body });
  }

  logout() {
    return this.request("POST", `${this.prefix}/auth/logout`);
  }

  refresh() {
    return this.request("POST", `${this.prefix}/auth/refresh`);
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

  listPublications(params) {
    return this.request("GET", `${this.prefix}/publications`, { params });
  }

  getCurrentPublication(params) {
    return this.request("GET", `${this.prefix}/publications/current`, { params });
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

  async request(method, path, { params, auth = true, body, multipart = false, blob = false } = {}) {
    const url = new URL(path, `${this.baseUrl}/`);
    if (params) {
      for (const [key, value] of Object.entries(params)) {
        if (value === undefined || value === null || value === "") continue;
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
    if (!response.ok) {
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
      throw new ApiError(
        response.status,
        error.code || "INTERNAL_ERROR",
        error.message || "Request failed.",
        error.details || null,
      );
    }
    if (blob) {
      const blobBody = await response.blob();
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
