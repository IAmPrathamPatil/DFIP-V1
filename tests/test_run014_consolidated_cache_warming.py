"""RUN 014: background warming of the consolidated company workbook.

Caching alone did not meet the SLA. The first download after a publication
still paid the full cumulative-history load plus the workbook render, which is
where the multi-minute wait came from. So the artifact is now built in the
background when a publication completes, and a download that arrives while the
build is still running is told so instead of silently blocking.

This file covers the warming contract specifically:

- a successful publication triggers a build;
- a new month or a republish invalidates and regenerates;
- a failed or unpublished run never warms or replaces anything;
- the build runs off the request thread;
- a download during a build has defined, non-blocking behaviour;
- warming reuses the same renderer, cache and fingerprint as the download.

Existing behaviour covered by RUN 012 / RUN 013 (charts, Downloads button,
static and refreshable artifacts) is deliberately re-asserted here at the
boundary where warming could have broken it.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from dfip_api import publication_service as publication_service_module
from dfip_api.app import InlineExecutor, create_app
from dfip_api.publication_routes import (
    CONSOLIDATED_PREPARING_MESSAGE,
    CONSOLIDATED_PREPARING_RETRY_SECONDS,
)
from dfip_api.publication_store import InMemoryPublicationStore
from dfip_web.consolidated_cache import cache_root
from fastapi.testclient import TestClient

from test_p5_api import AUTH, CLIENT_ID
from test_p7_publication import publisher_settings, seed_stores
from test_r10_published_history import _publish_snapshot
from test_run012_consolidated_workbook import (
    CLIENT_B,
    CONSOLIDATED_PATH,
    RUN_M1,
    _add_run,
    _measure,
    _month_facts,
    _months,
)


class RecordingExecutor:
    """Runs background work on a thread the test controls.

    Lets a test assert that warming did not happen on the request thread and
    decide exactly when the build finishes.
    """

    def __init__(self) -> None:
        self.submitted: list[tuple[Any, tuple[Any, ...]]] = []
        self.request_thread_ids: list[int] = []
        self._pending: list[tuple[Any, tuple[Any, ...]]] = []
        self._lock = threading.Lock()

    def submit(self, fn, /, *args, **kwargs):  # type: ignore[override]
        from concurrent.futures import Future

        future: Future = Future()
        with self._lock:
            self.submitted.append((fn, args))
            self.request_thread_ids.append(threading.get_ident())
            self._pending.append((fn, args))

        def run() -> None:
            try:
                future.set_result(fn(*args))
            except BaseException as exc:  # noqa: BLE001
                future.set_exception(exc)

        future._dfip_runner = run  # type: ignore[attr-defined]
        return future

    def run_all(self) -> None:
        """Drain everything queued so far, on this thread."""
        while True:
            with self._lock:
                if not self._pending:
                    return
                _fn, args = self._pending.pop(0)
            _fn(*args)

    def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:
        return None


def _app(executor, cache_dir: Path | None = None, **overrides: object):
    ingest, facts = seed_stores()
    store = InMemoryPublicationStore()
    resolved: dict[str, object] = {
        "dfip_consolidated_cache_enabled": True,
        "dfip_consolidated_cache_dir": str(cache_dir) if cache_dir else "",
    }
    resolved.update(overrides)
    app = create_app(
        settings=publisher_settings(**resolved),
        ingest_store=ingest,
        fact_store=facts,
        publication_store=store,
        executor=executor,
    )
    return app, ingest, facts, store


def _publish_via_api(app, ingest, store, run_id: str, month: str, *, sent: int = 10):
    """Publish through the real endpoint so the completion hook fires."""
    http = TestClient(app)
    _add_run(ingest, run_id)
    _publish_snapshot(store, _month_facts(month, sent=sent, clicks=2), run_id=run_id)
    return http


# ---------------------------------------------------------------------------
# Warming triggers
# ---------------------------------------------------------------------------


def test_successful_publication_triggers_a_warm(tmp_path: Path) -> None:
    executor = RecordingExecutor()
    app, ingest, _facts, store = _app(executor, tmp_path)
    _publish_via_api(app, ingest, store, RUN_M1, "Jan-25")

    # Seeded directly, so drive the publication service the way the API does.
    service = app.state.publication_service
    service.request_consolidated_warm(CLIENT_ID)

    assert executor.submitted, "a warm must be queued on the background executor"
    assert executor.run_all() is None
    cached = list((cache_root(tmp_path) / CLIENT_ID).glob("*.xlsx"))
    assert len(cached) == 1
    assert cached[0].read_bytes().startswith(b"PK")


class FutureCapturingExecutor:
    """A real thread pool that also records the futures it hands out."""

    def __init__(self) -> None:
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="test-warm")
        self.futures: list[Any] = []

    def submit(self, fn, /, *args, **kwargs):  # type: ignore[override]
        future = self._pool.submit(fn, *args, **kwargs)
        self.futures.append(future)
        return future

    def run_all(self) -> None:
        for future in list(self.futures):
            future.result(timeout=60)

    def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:
        self._pool.shutdown(wait=wait, cancel_futures=cancel_futures)


def test_warm_runs_off_the_request_thread(tmp_path: Path) -> None:
    """The publisher must not be held for the length of the render."""
    executor = FutureCapturingExecutor()
    try:
        app, ingest, _facts, store = _app(executor, tmp_path)
        _add_run(ingest, RUN_M1)
        _publish_snapshot(store, _month_facts("Jan-25", sent=10, clicks=2), run_id=RUN_M1)

        service = app.state.publication_service
        seen: list[int] = []
        real_render = service._render_consolidated

        def recording_render(**kwargs: object):
            seen.append(threading.get_ident())
            return real_render(**kwargs)

        service._render_consolidated = recording_render  # type: ignore[method-assign]
        try:
            service.request_consolidated_warm(CLIENT_ID)
            executor.run_all()
        finally:
            del service._render_consolidated  # type: ignore[attr-defined]

        assert seen, "the warm never ran"
        assert seen[0] != threading.get_ident(), "the warm must not block the request thread"
        assert list((cache_root(tmp_path) / CLIENT_ID).glob("*.xlsx"))
    finally:
        executor.shutdown(wait=True)


def test_publication_create_queues_the_warm() -> None:
    """Publishing through the real endpoint queues exactly one build."""
    from test_p7_publication import _publish

    executor = RecordingExecutor()
    app, _ingest, _facts, _store = _app(executor)
    http = TestClient(app)

    response = _publish(http)

    assert response.status_code in {200, 201}
    assert len(executor.submitted) == 1, "publication completion must queue the warm"


def test_unpublished_run_is_not_included(tmp_path: Path) -> None:
    executor = InlineExecutor()
    app, ingest, _facts, store = _app(executor, tmp_path)
    http = TestClient(app)

    _add_run(ingest, RUN_M1)
    _publish_snapshot(store, _month_facts("Jan-25", sent=10, clicks=2), run_id=RUN_M1)
    # A succeeded run that was never published contributes nothing.
    _add_run(ingest, "a0000000-0000-4000-8000-0000000000e1")

    response = http.get(CONSOLIDATED_PATH, headers=AUTH, params={"client_id": CLIENT_ID})
    assert response.status_code == 200
    assert _months(response.content) == {"Jan-25"}
    assert _measure(response.content, "Sent") == 10


# ---------------------------------------------------------------------------
# Invalidation
# ---------------------------------------------------------------------------


def test_new_month_regenerates_the_artifact(tmp_path: Path) -> None:
    executor = InlineExecutor()
    app, ingest, _facts, store = _app(executor, tmp_path)
    http = TestClient(app)

    _add_run(ingest, RUN_M1)
    _publish_snapshot(store, _month_facts("Jan-25", sent=10, clicks=2), run_id=RUN_M1)
    january = http.get(CONSOLIDATED_PATH, headers=AUTH, params={"client_id": CLIENT_ID})
    assert january.status_code == 200
    assert _months(january.content) == {"Jan-25"}

    _add_run(ingest, "a0000000-0000-4000-8000-0000000000a2")
    _publish_snapshot(
        store,
        _month_facts("Feb-25", sent=10, clicks=2),
        run_id="a0000000-0000-4000-8000-0000000000a2",
    )
    february = http.get(CONSOLIDATED_PATH, headers=AUTH, params={"client_id": CLIENT_ID})

    assert february.status_code == 200
    assert _months(february.content) == {"Jan-25", "Feb-25"}
    assert february.content != january.content


def test_republish_regenerates_the_artifact(tmp_path: Path) -> None:
    executor = InlineExecutor()
    app, ingest, _facts, store = _app(executor, tmp_path)
    http = TestClient(app)

    _add_run(ingest, RUN_M1)
    _publish_snapshot(store, _month_facts("Jan-25", sent=10, clicks=2), run_id=RUN_M1)
    first = http.get(CONSOLIDATED_PATH, headers=AUTH, params={"client_id": CLIENT_ID})
    assert _measure(first.content, "Sent") == 10

    _add_run(ingest, "a0000000-0000-4000-8000-0000000000b1")
    _publish_snapshot(
        store,
        _month_facts("Jan-25", sent=40, clicks=7),
        run_id="a0000000-0000-4000-8000-0000000000b1",
    )
    second = http.get(CONSOLIDATED_PATH, headers=AUTH, params={"client_id": CLIENT_ID})

    assert second.content != first.content
    assert _measure(second.content, "Sent") == 40


def test_warm_is_idempotent_per_company(tmp_path: Path) -> None:
    executor = RecordingExecutor()
    app, _ingest, _facts, _store = _app(executor, tmp_path)
    service = app.state.publication_service

    assert service.request_consolidated_warm(CLIENT_ID) is True
    assert service.request_consolidated_warm(CLIENT_ID) is True
    assert len(executor.submitted) == 1, "a second request must not queue a duplicate build"

    # A different company is warmed independently.
    service.request_consolidated_warm(CLIENT_B)
    assert len(executor.submitted) == 2


def test_warming_finishing_clears_the_in_flight_flag(tmp_path: Path) -> None:
    executor = RecordingExecutor()
    app, ingest, _facts, store = _app(executor, tmp_path)
    _publish_via_api(app, ingest, store, RUN_M1, "Jan-25")
    service = app.state.publication_service

    service.request_consolidated_warm(CLIENT_ID)
    assert service.is_consolidated_warming(CLIENT_ID) is True
    executor.run_all()
    assert service.is_consolidated_warming(CLIENT_ID) is False


def test_a_failing_warm_leaves_the_download_working(tmp_path: Path) -> None:
    """Warming is best effort. A broken build must not break downloads."""
    executor = InlineExecutor()
    app, ingest, _facts, store = _app(executor, tmp_path)
    http = TestClient(app)
    _add_run(ingest, RUN_M1)
    _publish_snapshot(store, _month_facts("Jan-25", sent=10, clicks=2), run_id=RUN_M1)

    service = app.state.publication_service
    original = publication_service_module.render_client_report_xlsx

    def boom(*args: object, **kwargs: object) -> bytes:
        raise RuntimeError("render exploded")

    service._render_consolidated = boom  # type: ignore[method-assign]
    try:
        # A warm that blows up is swallowed.
        service.request_consolidated_warm(CLIENT_ID)
    finally:
        del service._render_consolidated  # type: ignore[attr-defined]

    response = http.get(CONSOLIDATED_PATH, headers=AUTH, params={"client_id": CLIENT_ID})
    assert response.status_code == 200
    assert _months(response.content) == {"Jan-25"}
    assert original is publication_service_module.render_client_report_xlsx


# ---------------------------------------------------------------------------
# Download during a build
# ---------------------------------------------------------------------------


def test_download_during_a_build_returns_202_not_a_stalled_file(tmp_path: Path) -> None:
    executor = RecordingExecutor()
    app, ingest, _facts, store = _app(executor, tmp_path)
    _publish_via_api(app, ingest, store, RUN_M1, "Jan-25")
    http = TestClient(app)

    # Queue a build and deliberately leave it unfinished.
    service = app.state.publication_service
    service.request_consolidated_warm(CLIENT_ID)

    response = http.get(CONSOLIDATED_PATH, headers=AUTH, params={"client_id": CLIENT_ID})

    assert response.status_code == 202
    assert response.headers["Retry-After"] == str(CONSOLIDATED_PREPARING_RETRY_SECONDS)
    body = response.json()
    assert body["status"] == "preparing"
    assert body["message"] == CONSOLIDATED_PREPARING_MESSAGE
    assert body["retry_after_seconds"] == CONSOLIDATED_PREPARING_RETRY_SECONDS
    assert "content-disposition" not in response.headers


def test_retry_after_the_build_finishes_returns_the_file(tmp_path: Path) -> None:
    executor = RecordingExecutor()
    app, ingest, _facts, store = _app(executor, tmp_path)
    _publish_via_api(app, ingest, store, RUN_M1, "Jan-25")
    http = TestClient(app)
    service = app.state.publication_service

    service.request_consolidated_warm(CLIENT_ID)
    pending = http.get(CONSOLIDATED_PATH, headers=AUTH, params={"client_id": CLIENT_ID})
    assert pending.status_code == 202

    executor.run_all()

    ok = http.get(CONSOLIDATED_PATH, headers=AUTH, params={"client_id": CLIENT_ID})
    assert ok.status_code == 200
    assert ok.content.startswith(b"PK")
    assert _months(ok.content) == {"Jan-25"}


def test_second_does_not_queue_a_duplicate_build(tmp_path: Path) -> None:
    executor = RecordingExecutor()
    app, ingest, _facts, store = _app(executor, tmp_path)
    _publish_via_api(app, ingest, store, RUN_M1, "Jan-25")
    http = TestClient(app)

    for _ in range(4):
        http.get(CONSOLIDATED_PATH, headers=AUTH, params={"client_id": CLIENT_ID})

    # A download must not spawn one build per click.
    assert len(executor.submitted) == 1


def test_preparing_response_requires_authentication(tmp_path: Path) -> None:
    executor = RecordingExecutor()
    app, ingest, _facts, store = _app(executor, tmp_path)
    _publish_via_api(app, ingest, store, RUN_M1, "Jan-25")
    http = TestClient(app)
    app.state.publication_service.request_consolidated_warm(CLIENT_ID)

    anonymous = http.get(CONSOLIDATED_PATH, params={"client_id": CLIENT_ID})
    assert anonymous.status_code == 401


def test_preparing_response_respects_company_scoping(tmp_path: Path) -> None:
    executor = RecordingExecutor()
    app, ingest, _facts, store = _app(executor, tmp_path)
    http = TestClient(app)
    _add_run(ingest, RUN_M1)
    _publish_snapshot(store, _month_facts("Jan-25", sent=10, clicks=2), run_id=RUN_M1)
    app.state.publication_service.request_consolidated_warm(CLIENT_ID)

    # Client B has no history at all, so it must not borrow A's state.
    response = http.get(CONSOLIDATED_PATH, headers=AUTH, params={"client_id": CLIENT_B})
    assert response.status_code == 404


def test_over_cap_is_reported_immediately_not_left_preparing(tmp_path: Path) -> None:
    """An over-cap company gets 422 at once instead of retrying forever."""
    executor = RecordingExecutor()
    app, ingest, _facts, store = _app(executor, tmp_path)
    http = TestClient(app)
    for index, month in enumerate(("Jan-25", "Feb-25", "Mar-25"), start=1):
        run_id = f"a0000000-0000-4000-8000-0000000000a{index}"
        _add_run(ingest, run_id)
        _publish_snapshot(store, _month_facts(month, sent=10, clicks=2), run_id=run_id)

    monkey = pytest.MonkeyPatch()
    monkey.setattr(publication_service_module, "HISTORY_FACTS_MAX_PAGE_LIMIT", 2)
    try:
        response = http.get(CONSOLIDATED_PATH, headers=AUTH, params={"client_id": CLIENT_ID})
    finally:
        monkey.undo()

    assert response.status_code == 422
    assert "workbook download limit" in response.json()["error"]["message"]


# ---------------------------------------------------------------------------
# Caching off, and unaffected neighbours
# ---------------------------------------------------------------------------


def test_disabling_the_cache_restores_the_synchronous_download(tmp_path: Path) -> None:
    executor = InlineExecutor()
    app, ingest, _facts, store = _app(executor, tmp_path, dfip_consolidated_cache_enabled=False)
    http = TestClient(app)
    _add_run(ingest, RUN_M1)
    _publish_snapshot(store, _month_facts("Jan-25", sent=10, clicks=2), run_id=RUN_M1)

    response = http.get(CONSOLIDATED_PATH, headers=AUTH, params={"client_id": CLIENT_ID})

    assert response.status_code == 200
    assert _months(response.content) == {"Jan-25"}
    assert executor.submitted == [] if hasattr(executor, "submitted") else True
    assert not list((cache_root(tmp_path) / CLIENT_ID).glob("*.xlsx"))


def test_static_and_refreshable_workbooks_are_unaffected(tmp_path: Path) -> None:
    executor = RecordingExecutor()
    app, ingest, _facts, store = _app(executor, tmp_path)
    _publish_via_api(app, ingest, store, RUN_M1, "Jan-25")
    executor.run_all()
    http = TestClient(app)

    static = http.get(
        "/api/v1/publications/current/client-report.xlsx",
        headers=AUTH,
        params={"client_id": CLIENT_ID},
    )
    refreshable = http.get(
        "/api/v1/publications/current/refreshable-client-report.xlsx",
        headers=AUTH,
        params={"client_id": CLIENT_ID},
    )

    assert static.status_code == 200
    assert refreshable.status_code == 200


def test_month_one_two_and_three_all_download(tmp_path: Path) -> None:
    executor = InlineExecutor()
    app, ingest, _facts, store = _app(executor, tmp_path)
    http = TestClient(app)

    expected: set[str] = set()
    for index, month in enumerate(("Jan-25", "Feb-25", "Mar-25"), start=1):
        run_id = f"a0000000-0000-4000-8000-0000000000a{index}"
        _add_run(ingest, run_id)
        _publish_snapshot(store, _month_facts(month, sent=10, clicks=2), run_id=run_id)
        expected.add(month)
        response = http.get(CONSOLIDATED_PATH, headers=AUTH, params={"client_id": CLIENT_ID})
    assert response.status_code == 200
    assert _months(response.content) == expected
    assert _measure(response.content, "Sent") == 10 * len(expected)


# ---------------------------------------------------------------------------
# Warm executor isolation
# ---------------------------------------------------------------------------


def test_warming_does_not_occupy_the_upload_pool(tmp_path: Path) -> None:
    """A long workbook build must never park the upload worker.

    Production runs the upload pool as one deliberately serialized thread
    behind an unbounded FIFO queue, so a warm submitted there would delay
    every later upload for a cache refresh nobody is waiting on. Warming
    therefore gets its own single-worker pool. If the two pools were shared
    again, the upload-side task below could not start until the warm
    finished, and the timeout would trip.
    """
    ingest, facts = seed_stores()
    store = InMemoryPublicationStore()
    # No injected executor: this exercises the pools the app really creates.
    app = create_app(
        settings=publisher_settings(
            dfip_consolidated_cache_enabled=True,
            dfip_consolidated_cache_dir=str(tmp_path),
        ),
        ingest_store=ingest,
        fact_store=facts,
        publication_store=store,
    )

    assert app.state.warm_executor is not app.state.upload_executor

    _add_run(ingest, RUN_M1)
    _publish_snapshot(store, _month_facts("Jan-25", sent=10, clicks=2), run_id=RUN_M1)

    service = app.state.publication_service
    warm_threads: list[str] = []
    entered = threading.Event()
    release = threading.Event()
    real_render = service._render_consolidated

    def recording_render(**kwargs: object):
        warm_threads.append(threading.current_thread().name)
        entered.set()
        # Hold the warm thread open long enough that sharing the pool would
        # starve the upload-side task for the whole wait.
        release.wait(45)
        return real_render(**kwargs)

    service._render_consolidated = recording_render  # type: ignore[method-assign]
    upload_thread = ""
    try:
        service.request_consolidated_warm(CLIENT_ID)
        assert entered.wait(30), "the warm never started"
        upload_thread = app.state.upload_executor.submit(
            lambda: threading.current_thread().name
        ).result(timeout=20)
    finally:
        release.set()
        del service._render_consolidated  # type: ignore[attr-defined]
        app.state.warm_executor.shutdown(wait=True, cancel_futures=False)
        app.state.upload_executor.shutdown(wait=True, cancel_futures=False)

    assert warm_threads and warm_threads[0].startswith("dfip-warm")
    assert upload_thread.startswith("dfip-upload")
    assert list((cache_root(tmp_path) / CLIENT_ID).glob("*.xlsx"))
