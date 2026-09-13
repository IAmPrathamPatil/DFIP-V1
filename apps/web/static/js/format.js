export function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

export function raw(value) {
  if (isSafeHtml(value)) {
    return value;
  }
  if (value == null) {
    return safeHtml("");
  }
  return safeHtml(String(value));
}

export function toHtml(value) {
  return interpolate(value);
}

export const MIN_PASSWORD_LENGTH = 12;

export function html(strings, ...values) {
  const markup = strings.reduce((acc, part, index) => {
    if (index >= values.length) return acc + part;
    return acc + part + interpolate(values[index]);
  }, "");
  return safeHtml(markup);
}

function safeHtml(markup) {
  return {
    __html: String(markup),
    toString() {
      return String(markup);
    },
  };
}

function isSafeHtml(value) {
  return (
    value != null &&
    typeof value === "object" &&
    Object.prototype.hasOwnProperty.call(value, "__html") &&
    typeof value.__html === "string"
  );
}

function interpolate(value) {
  if (value == null) return "";
  if (Array.isArray(value)) return value.map(interpolate).join("");
  if (isSafeHtml(value)) return value.__html;
  return escapeHtml(value);
}

export function displayCell(value) {
  if (value === null) return html`<span class="null">NULL</span>`;
  if (value === "") return html`<span class="empty">""</span>`;
  return html`<span>${value}</span>`;
}

export function badge(status) {
  const text = status == null ? "NULL" : String(status);
  let kind = "";
  const lowered = text.toLowerCase();
  if (["ok", "succeeded", "processed", "staged", "matched", "active", "valid", "current", "pass", "published"].includes(lowered)) {
    kind = "ok";
  } else if (["failed", "error", "invalid", "critical", "fail", "unavailable", "cancelled"].includes(lowered)) {
    kind = "failed";
  } else if (["pending", "running", "received", "draft", "superseded", "warning", "warn", "cancelling"].includes(lowered)) {
    kind = "warn";
  } else if (["info", "information"].includes(lowered)) {
    kind = "info";
  }
  return html`<span class="badge ${kind}">${text}</span>`;
}

function errorTitle(code, status) {
  if (status === 401 || code === "AUTHENTICATION_FAILED") return "Sign-in required";
  if (status === 403 || code === "AUTHORIZATION_FAILED") return "Not authorized";
  if (status === 404 || code === "NOT_FOUND") return "Not found";
  if (status === 422 || code === "VALIDATION_ERROR") return "Validation failed";
  if (code === "INVALID_PAGINATION") return "Invalid page request";
  if (status === 503 || code === "PERSISTENCE_UNAVAILABLE") return "Storage unavailable";
  if (code === "NETWORK_FAILURE") return "Cannot reach the API";
  if (status === 500 || code === "INTERNAL_ERROR") return "The request could not be completed";
  return "Request failed";
}

export function errorBanner(error) {
  if (!error) return "";
  const code = error.code || "ERROR";
  const message = error.message || "Request failed.";
  let hint = "";
  if (error.status === 401 || code === "AUTHENTICATION_FAILED") {
    hint = "Sign in with your username and password.";
  } else if (error.status === 403 || code === "AUTHORIZATION_FAILED") {
    hint = "This action is not available for the current role.";
  } else if (error.status === 404 || code === "NOT_FOUND") {
    hint = "The requested record was not found.";
  } else if (error.status === 409 || code === "CONFLICT") {
    hint = "A publisher account already exists. Sign in with that operator account.";
  } else if (error.status === 422 || code === "VALIDATION_ERROR" || code === "INVALID_PAGINATION") {
    hint = passwordRuleHint(error) || "Check the file, required fields, or filters, then try again.";
  } else if (error.status === 503 || code === "PERSISTENCE_UNAVAILABLE") {
    hint = "Persistence is unavailable. The API did not open a live database.";
  } else if (code === "NETWORK_FAILURE") {
    hint = "Confirm the API process is running, then retry.";
  } else if (error.status === 500 || code === "INTERNAL_ERROR") {
    hint = "Retry the action. If it continues, contact an administrator.";
  }
  const meta = error.status ? `${code} · ${error.status}` : code;
  const details = validationDetailItems(error);
  return html`
    <div class="banner error" role="alert">
      <strong>${errorTitle(code, error.status)}</strong>
      <div>${message}</div>
      ${hint ? html`<div class="muted">${hint}</div>` : ""}
      ${
        details.length
          ? html`<ul class="error-details">${details.map((item) => html`<li>${item}</li>`)}</ul>`
          : ""
      }
      <div class="banner-meta muted">${meta}</div>
    </div>
  `;
}

function validationDetailItems(error) {
  const rows = Array.isArray(error && error.details) ? error.details : [];
  const items = [];
  for (const row of rows) {
    const loc = Array.isArray(row.loc) ? row.loc.filter((part) => part !== "body") : [];
    const field = loc.length ? String(loc[loc.length - 1]) : "";
    const msg = String((row && row.msg) || "").trim();
    if (!msg) continue;
    items.push(field ? `${field}: ${msg}` : msg);
  }
  return items;
}

function passwordRuleHint(error) {
  const rows = Array.isArray(error && error.details) ? error.details : [];
  const passwordLength = rows.some((row) => {
    const loc = Array.isArray(row.loc) ? row.loc.join(".") : "";
    const type = String((row && row.type) || "");
    const msg = String((row && row.msg) || "").toLowerCase();
    return loc.includes("password") && (type.includes("too_short") || msg.includes("at least 12"));
  });
  if (passwordLength) {
    return `Passwords must be at least ${MIN_PASSWORD_LENGTH} characters.`;
  }
  return "";
}

export function emptyState(message) {
  return html`<div class="empty-state"><p class="muted">${message}</p></div>`;
}

export function loadingState() {
  return html`
    <div class="skeleton-stack" role="status" aria-live="polite">
      <span class="sr-only">Loading…</span>
      <div class="skeleton skeleton-title"></div>
      <div class="skeleton skeleton-line"></div>
      <div class="skeleton skeleton-table"></div>
    </div>
  `;
}

export function paginationControls(pagination, buildHref) {
  const limit = pagination.limit;
  const offset = pagination.offset;
  const total = pagination.total;
  const prev = Math.max(0, offset - limit);
  const next = offset + limit;
  const from = total === 0 ? 0 : offset + 1;
  const to = Math.min(offset + limit, total);
  const prevLink =
    offset === 0
      ? html`<span class="muted">Previous</span>`
      : html`<a href="${buildHref(prev)}">Previous</a>`;
  const nextLink =
    next >= total
      ? html`<span class="muted">Next</span>`
      : html`<a href="${buildHref(next)}">Next</a>`;
  return html`
    <div class="pager">
      ${prevLink}
      <span class="muted">Showing ${from}–${to} of ${total}</span>
      ${nextLink}
    </div>
  `;
}
