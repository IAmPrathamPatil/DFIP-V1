"""Frontend Publisher session: in-memory only, terminal on replacement.

The Publisher session id must never reach localStorage, sessionStorage, a
cookie, or the URL, and a replaced page must not re-establish. These tests
execute publisher-session.js in node and inspect real state rather than
matching source text, plus static checks for the wiring in app.js and
api-client.js.

Does not open PostgreSQL, does not print tokens, and does not change
publication_current state.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "apps" / "web" / "static" / "js"
MODULE = JS / "publisher-session.js"
API_CLIENT = JS / "api-client.js"
APP = JS / "app.js"

HARNESS = r"""
import { pathToFileURL } from "node:url";

const MODULE_PATH = process.argv[2];
const SCENARIO = process.argv[3];

const store = { local: {}, session: {}, cookies: [] };
globalThis.localStorage = {
  getItem: (k) => (k in store.local ? store.local[k] : null),
  setItem: (k, v) => { store.local[k] = String(v); },
  removeItem: (k) => { delete store.local[k]; },
  get length() { return Object.keys(store.local).length; },
};
globalThis.sessionStorage = {
  getItem: (k) => (k in store.session ? store.session[k] : null),
  setItem: (k, v) => { store.session[k] = String(v); },
  removeItem: (k) => { delete store.session[k]; },
  get length() { return Object.keys(store.session).length; },
};
globalThis.document = { cookie: "" };
const timers = [];
let nextTimerId = 1;
// A real timer is consumed when it fires, and only then. Reproducing that
// matters here because the heartbeat reschedules itself.
globalThis.setTimeout = (fn, ms) => {
  const id = nextTimerId++;
  const entry = { id, ms };
  entry.fn = async () => {
    const index = timers.findIndex((t) => t.id === id);
    if (index >= 0) timers.splice(index, 1);
    await fn();
  };
  timers.push(entry);
  return id;
};
globalThis.clearTimeout = (id) => {
  const index = timers.findIndex((t) => t.id === id);
  if (index >= 0) timers.splice(index, 1);
};

const ps = await import(pathToFileURL(MODULE_PATH).href);

const calls = { open: 0, heartbeat: 0, openHeaders: [], heartbeatHeaders: [] };
const api = {
  async openPublisherSession() {
    calls.open += 1;
    calls.openHeaders.push(ps.publisherSessionHeader());
    return {
      session_id: `sess-${calls.open}`,
      expires_at: "2026-01-01T00:15:00Z",
      heartbeat_seconds: 60,
    };
  },
  async heartbeatPublisherSession() {
    calls.heartbeat += 1;
    calls.heartbeatHeaders.push(ps.publisherSessionHeader());
    return { session_id: "sess-1", expires_at: "2026-01-01T00:15:00Z", heartbeat_seconds: 60 };
  },
};

const out = { calls, timers: () => timers.length, storage: store, replaced: false };
const replacedError = { code: "PUBLISHER_SESSION_REPLACED", status: 401 };
ps.setPublisherSessionReplacedHandler(() => { out.replaced = true; });

const PUBLISHER = { role: "publisher", client_id: "c1" };
const CLIENT = { role: "client", client_id: "c1" };
const READER = { role: "reader", client_id: "c1" };

if (SCENARIO === "client") {
  out.id = await ps.ensurePublisherSession(api, CLIENT);
  out.header = ps.publisherSessionHeader();
  out.readerId = await ps.ensurePublisherSession(api, READER);
  out.readerHeader = ps.publisherSessionHeader();
} else if (SCENARIO === "not-established") {
  out.header = ps.publisherSessionHeader();
  out.id = await ps.ensurePublisherSession(api, PUBLISHER);
  out.headerAfter = ps.publisherSessionHeader();
} else if (SCENARIO === "replaced-terminal") {
  await ps.ensurePublisherSession(api, PUBLISHER);
  out.headerBefore = ps.publisherSessionHeader();
  ps.markReplaced();
  out.headerAfter = ps.publisherSessionHeader();
  out.isReplaced = ps.isReplaced();
  await ps.ensurePublisherSession(api, PUBLISHER);
  await ps.ensurePublisherSession(api, PUBLISHER);
} else if (SCENARIO === "heartbeat") {
  await ps.ensurePublisherSession(api, PUBLISHER);
  out.timersAfterOpen = timers.length;
  out.interval = ps.heartbeatIntervalMs();
  const pending = timers[timers.length - 1];
  await pending.fn();
  out.timersAfterBeat = timers.length;
  out.heartbeatsBeforeStop = calls.heartbeat;
  ps.stopHeartbeat();
  out.timersAfterStop = timers.length;
} else if (SCENARIO === "heartbeat-replaced") {
  await ps.ensurePublisherSession(api, PUBLISHER);
  const pending = timers[timers.length - 1];
  api.heartbeatPublisherSession = async () => { throw replacedError; };
  await pending.fn();
  out.timersAfterFailure = timers.length;
  out.isReplaced = ps.isReplaced();
  out.replacedNotified = out.replaced;
  out.heartbeats = calls.heartbeat;
  out.headerAfter = ps.publisherSessionHeader();
} else if (SCENARIO === "heartbeat-transient") {
  await ps.ensurePublisherSession(api, PUBLISHER);
  const pending = timers[timers.length - 1];
  api.heartbeatPublisherSession = async () => { throw { code: "NETWORK_FAILURE", status: 0 }; };
  await pending.fn();
  out.timersAfterFailure = timers.length;
  out.isReplaced = ps.isReplaced();
  out.replacedNotified = out.replaced;
} else if (SCENARIO === "reset") {
  await ps.ensurePublisherSession(api, PUBLISHER);
  ps.stopHeartbeat();
  ps.resetPublisherSession();
  out.timersAfterReset = timers.length;
  out.headerAfterReset = ps.publisherSessionHeader();
  out.isReplaced = ps.isReplaced();
  out.idAfterReset = await ps.ensurePublisherSession(api, PUBLISHER);
} else {
  throw new Error(`unknown scenario: ${SCENARIO}`);
}

out.localStorageKeys = Object.keys(store.local);
out.sessionStorageKeys = Object.keys(store.session);
out.cookie = globalThis.document.cookie;
process.stdout.write(JSON.stringify(out));
"""


def _run_node(tmp_path: Path, scenario: str) -> dict:
    node = shutil.which("node")
    assert node is not None, "node is required for the Publisher session frontend tests"
    script = tmp_path / "publisher-session-harness.mjs"
    script.write_text(HARNESS, encoding="utf-8")
    done = subprocess.run(
        [node, str(script), str(MODULE), scenario],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(done.stdout)


@pytest.fixture(autouse=True)
def _require_node():
    if shutil.which("node") is None:  # pragma: no cover - environment guard
        pytest.skip("node is not available")


# --- the id never leaves module memory --------------------------------------


def test_session_id_is_never_persisted(tmp_path: Path) -> None:
    result = _run_node(tmp_path, "not-established")
    assert result["id"] == "sess-1"
    assert result["headerAfter"]["value"] == "sess-1"
    assert result["localStorageKeys"] == []
    assert result["sessionStorageKeys"] == []
    assert result["cookie"] == ""
    assert "sess-1" not in json.dumps(result["storage"])


def _code_only(path: Path) -> str:
    """Source with comments stripped, so prose about storage is not a hit."""
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", text)


def test_source_writes_no_browser_storage() -> None:
    source = _code_only(MODULE)
    for forbidden in ("localStorage", "sessionStorage", "document.cookie"):
        assert forbidden not in source, f"{forbidden} must not appear in publisher-session.js"


# --- header injection ------------------------------------------------------


def test_no_header_before_the_session_is_established(tmp_path: Path) -> None:
    result = _run_node(tmp_path, "not-established")
    assert result["header"] is None
    assert result["headerAfter"]["name"] == "X-DFIP-Publisher-Session"


def test_no_header_for_client_or_reader(tmp_path: Path) -> None:
    result = _run_node(tmp_path, "client")
    assert result["id"] is None
    assert result["header"] is None
    assert result["readerId"] is None
    assert result["readerHeader"] is None
    assert result["calls"]["open"] == 0


def test_establishment_request_sends_no_header_and_heartbeat_does(tmp_path: Path) -> None:
    result = _run_node(tmp_path, "heartbeat")
    # The bootstrap request creates the session, so it must not present one.
    assert result["calls"]["openHeaders"] == [None]
    # The heartbeat must present the active session.
    assert result["calls"]["heartbeatHeaders"][0]["value"] == "sess-1"


# --- replaced is terminal ---------------------------------------------------


def test_replaced_page_sends_no_header(tmp_path: Path) -> None:
    result = _run_node(tmp_path, "replaced-terminal")
    assert result["headerBefore"]["value"] == "sess-1"
    assert result["headerAfter"] is None
    assert result["isReplaced"] is True


def test_replaced_page_never_establishes_again(tmp_path: Path) -> None:
    result = _run_node(tmp_path, "replaced-terminal")
    assert result["calls"]["open"] == 1, "a replaced page must not re-establish"


def test_no_header_after_replacement_even_if_ensure_is_called(tmp_path: Path) -> None:
    result = _run_node(tmp_path, "replaced-terminal")
    assert result["headerAfter"] is None


# --- heartbeat scheduling ---------------------------------------------------


def test_heartbeat_is_scheduled_from_the_server_interval(tmp_path: Path) -> None:
    result = _run_node(tmp_path, "heartbeat")
    assert result["interval"] == 60000
    assert result["timersAfterOpen"] == 1
    assert result["heartbeatsBeforeStop"] == 1


def test_stop_heartbeat_clears_the_pending_beat(tmp_path: Path) -> None:
    result = _run_node(tmp_path, "heartbeat")
    assert result["timersAfterBeat"] == 1, "a beat reschedules itself"
    assert result["timersAfterStop"] == 0


def test_heartbeat_failure_marks_replaced_and_notifies(tmp_path: Path) -> None:
    result = _run_node(tmp_path, "heartbeat-replaced")
    assert result["timersAfterFailure"] == 0, "no retry after a replaced heartbeat"
    assert result["isReplaced"] is True
    assert result["replacedNotified"] is True
    assert result["headerAfter"] is None


def test_transient_heartbeat_failure_does_not_end_the_session(tmp_path: Path) -> None:
    result = _run_node(tmp_path, "heartbeat-transient")
    assert result["isReplaced"] is False
    assert result["replacedNotified"] is False
    assert result["timersAfterFailure"] == 1, "a network blip is retried on the next beat"


def test_sign_out_resets_the_module(tmp_path: Path) -> None:
    result = _run_node(tmp_path, "reset")
    assert result["timersAfterReset"] == 0
    assert result["headerAfterReset"] is None
    assert result["isReplaced"] is False
    assert result["idAfterReset"] == "sess-2"


# --- wiring in the existing request layer and app shell ---------------------


def test_api_client_injects_the_header_only_on_authenticated_requests() -> None:
    source = API_CLIENT.read_text(encoding="utf-8")
    assert "getPublisherSession" in source
    assert 'headers[publisherSession.name] = publisherSession.value' in source
    # The injector is inside the `if (auth)` branch, never for public routes.
    auth_block = source[
        source.index("if (auth) {") : source.index("const options = { method, headers")
    ]
    assert "getPublisherSession" in auth_block
    assert "PUBLISHER_SESSION_REPLACED" in source
    assert "onPublisherSessionReplaced" in source


def test_api_client_constructor_arguments_stay_optional() -> None:
    source = API_CLIENT.read_text(encoding="utf-8")
    # Both hooks are defaulted to null, so an existing three-argument client
    # still works and never sends the header.
    assert "this.getPublisherSession = getPublisherSession || null;" in source
    assert "this.onPublisherSessionReplaced = onPublisherSessionReplaced || null;" in source
    signature = "getToken, getPublisherSession, onPublisherSessionReplaced"
    assert signature in source


def test_bootstrap_request_skips_the_header() -> None:
    source = API_CLIENT.read_text(encoding="utf-8")
    assert "skipPublisherSession: true" in source
    assert "if (!skipPublisherSession && this.getPublisherSession)" in source


def test_app_shell_establishes_the_session_before_gated_requests() -> None:
    app = APP.read_text(encoding="utf-8")
    load_start = app.index("async function loadSession()")
    load_end = app.index("async function bindSelectedCompany(")
    body = app[load_start:load_end]
    assert "await ensurePublisherSession(api, session" in body


def test_app_shell_handles_replacement_before_the_generic_401() -> None:
    app = APP.read_text(encoding="utf-8")
    handler = app[app.index("function handleError(") : app.index("function saveBlob(")]
    assert "isPublisherSessionReplacedError(error)" in handler
    assert handler.index("isPublisherSessionReplacedError") < handler.index("AUTHENTICATION_FAILED")


def test_app_shell_shows_the_explicit_replacement_message() -> None:
    app = APP.read_text(encoding="utf-8")
    assert "Publisher session opened elsewhere. This session is no longer active." in app
    assert "function enterPublisherSessionReplaced(" in app


def test_replacement_stops_every_publisher_poller() -> None:
    app = APP.read_text(encoding="utf-8")
    start = app.index("function enterPublisherSessionReplaced(")
    block = app[start : start + 1400]
    for stop in (
        "stopHeartbeat()",
        "stopUploadPoll()",
        "stopRunPoll()",
        "stopBatchPoll()",
        "stopPublishPoll()",
    ):
        assert stop in block, f"{stop} must run on replacement"
    assert "clearToken()" in block


def test_replacement_prevents_new_polling() -> None:
    app = APP.read_text(encoding="utf-8")
    for name in ("scheduleBatchPoll", "scheduleUploadPoll", "scheduleRunPoll"):
        start = app.index(f"function {name}(")
        block = app[start : start + 220]
        assert "isReplaced()" in block, f"{name} must not reschedule after replacement"
    start = app.index("function startPublicationProgressPoll(")
    assert "isReplaced()" in app[start : start + 220]


def test_app_shell_latches_the_replaced_state() -> None:
    """The replacement handler must mark the state, not just stop timers.

    Without this the header getter keeps returning the stale session id and the
    poller guards stay open.
    """
    app = APP.read_text(encoding="utf-8")
    start = app.index("function enterPublisherSessionReplaced(")
    block = app[start : start + 500]
    assert "markReplaced();" in block
    # Latched before any cleanup, so nothing can observe a half-cleared state.
    assert block.index("markReplaced();") < block.index("stopHeartbeat()")
    assert block.index("markReplaced();") < block.index("clearToken()")


def test_generic_401_handling_is_unchanged() -> None:
    app = APP.read_text(encoding="utf-8")
    handler = app[app.index("function handleError(") : app.index("function saveBlob(")]
    assert "clearToken()" in handler
    assert 'credentialView({' in handler
    assert handler.count("isPublisherSessionReplacedError(error)") == 1


def test_client_paths_never_send_the_header() -> None:
    """The getter is the only source of the header and it returns null off-role."""
    module = MODULE.read_text(encoding="utf-8")
    assert "canAccessAdmin(currentSession.role)" in module
    assert 'state.status === "active" ? state.sessionId : ""' in module
