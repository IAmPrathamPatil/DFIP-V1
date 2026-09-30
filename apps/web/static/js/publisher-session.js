// Single active Publisher session for this browser instance.
//
// The access JWT lives in sessionStorage and is shared by every tab, so it
// cannot tell one Publisher browser instance from another. The server issues a
// separate opaque session id for the active instance and revokes the previous
// one, so this module only has to hold that id and honour the lifecycle.
//
// Storage rule: the id lives in this module's memory for the lifetime of the
// page and is never written to localStorage, sessionStorage, cookies, or the
// URL. A reload or a duplicated tab therefore starts with no id, which the
// server treats as a new instance and which correctly takes over.
//
// Replaced is terminal. Once this page is told it was replaced it must not
// establish another session, or the two pages would trade the session back and
// forth forever.

import { canAccessAdmin } from "./roles.js";

const SESSION_HEADER = "X-DFIP-Publisher-Session";
const REPLACED_CODE = "PUBLISHER_SESSION_REPLACED";
const DEFAULT_HEARTBEAT_MS = 60000;

const state = {
  sessionId: "",
  status: "new",
  heartbeatMs: DEFAULT_HEARTBEAT_MS,
  timer: null,
  onReplaced: null,
};

export function isPublisherSessionReplacedError(error) {
  return Boolean(error) && error.code === REPLACED_CODE;
}

export function isReplaced() {
  return state.status === "replaced";
}

export function currentSessionId() {
  return state.status === "active" ? state.sessionId : "";
}

export function heartbeatIntervalMs() {
  return state.heartbeatMs;
}

function clearTimer() {
  if (state.timer !== null) {
    clearTimeout(state.timer);
    state.timer = null;
  }
}

// Returns the header to merge into a request, or null when this page must not
// send one: not a Publisher, not established yet, or already replaced.
export function publisherSessionHeader() {
  const value = currentSessionId();
  return value ? { name: SESSION_HEADER, value } : null;
}

export function markReplaced() {
  state.status = "replaced";
  state.sessionId = "";
  clearTimer();
}

function applySession(payload) {
  if (!payload || !payload.session_id) return;
  state.sessionId = String(payload.session_id);
  state.status = "active";
  const seconds = Number(payload.heartbeat_seconds);
  if (Number.isFinite(seconds) && seconds > 0) {
    state.heartbeatMs = Math.max(1000, Math.round(seconds * 1000));
  }
}

// Resets everything. Used when signing out so a later sign-in on the same page
// can establish a fresh session.
export function resetPublisherSession() {
  clearTimer();
  state.sessionId = "";
  state.status = "new";
  state.heartbeatMs = DEFAULT_HEARTBEAT_MS;
}

export function setPublisherSessionReplacedHandler(handler) {
  state.onReplaced = typeof handler === "function" ? handler : null;
}

function reportReplaced() {
  if (state.onReplaced) state.onReplaced();
}

// Opens this page as the active Publisher session, then starts the lease
// heartbeat. A Client/Reader is a no-op. A replaced page never re-establishes.
export async function ensurePublisherSession(api, currentSession, { onReplaced } = {}) {
  if (state.onReplaced === null && typeof onReplaced === "function") {
    state.onReplaced = onReplaced;
  }
  if (state.status === "replaced") return null;
  if (!currentSession || !canAccessAdmin(currentSession.role)) return null;
  if (state.status === "active") return state.sessionId;
  // Bootstrap deliberately sends no session header: this request is what
  // creates the session, so there is nothing to present yet.
  const payload = await api.openPublisherSession();
  applySession(payload);
  startHeartbeat(api);
  return state.sessionId;
}

export function startHeartbeat(api) {
  clearTimer();
  if (state.status !== "active" || !api) return;
  const tick = async () => {
    state.timer = null;
    if (state.status !== "active") return;
    try {
      const payload = await api.heartbeatPublisherSession();
      applySession(payload);
    } catch (error) {
      if (isPublisherSessionReplacedError(error)) {
        markReplaced();
        reportReplaced();
        return;
      }
      // A transient network or server error is not a replacement. Retry on the
      // next tick; the lease absorbs a few missed beats.
    }
    if (state.status === "active") {
      state.timer = setTimeout(tick, state.heartbeatMs);
    }
  };
  state.timer = setTimeout(tick, state.heartbeatMs);
}

export function stopHeartbeat() {
  clearTimer();
}
