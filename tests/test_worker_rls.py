"""Upload worker DB access must bind processing_rls before the first lookup.

In-memory tests do not open PostgreSQL. Postgres tests use DFIP_TEST_DATABASE_URL
and a disposable NOINHERIT LOGIN. They do not touch hosted/live data.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from dfip_api.auth import Principal
from dfip_api.membership import rls_context_for
from dfip_api.qa_workflow import processing_rls, startup_recovery_rls
from dfip_api.upload_service import UploadService
from dfip_core.ingest.store import InMemoryIngestStore
from dfip_core.transform.store import InMemoryFactStore
from dfip_db.connection import close_pool, create_pool
from dfip_db.fact_store import PostgresFactStore
from dfip_db.ingest_store import PostgresIngestStore
from dfip_db.rls import RlsContext, bind_rls, current_rls, reset_rls
from psycopg.errors import InsufficientPrivilege

from postgres_support import CLIENT_A, CLIENT_B, requires_postgres
from test_login_rls import APP_LOGIN_ROLE, _ensure_app_login_role
from test_p5_api import CLIENT_ID, make_settings
from test_p13b_recovery import _received_batch

DIGEST = "a" * 64


def _stop_after_lookup(*_args, **_kwargs):
    raise RuntimeError("stop after first lookup")


def _service(
    ingest: InMemoryIngestStore | None = None,
) -> tuple[UploadService, InMemoryIngestStore]:
    store = ingest or InMemoryIngestStore()
    return UploadService(store, InMemoryFactStore(), make_settings()), store


def _received(ingest: InMemoryIngestStore, *, client_id: str = CLIENT_A, digest: str = DIGEST):
    source = ingest.register_source_file(
        client_id=client_id,
        sha256=digest,
        original_filename="a.xlsx",
        byte_size=10,
        source_kind="native_export",
    )
    batch = ingest.create_batch(source_file_id=source.id, client_id=client_id)
    return source, batch


def _job_paths(tmp_path: Path) -> tuple[Path, Path]:
    folder = tmp_path / f"dfip-upload-{uuid4().hex}"
    folder.mkdir()
    dest = folder / "a.xlsx"
    dest.write_bytes(b"PK")
    return dest, folder


def _assert_processing_worker(ctx: RlsContext | None, client_id: str) -> None:
    assert ctx is not None
    assert ctx.platform_admin is False
    assert ctx.client_ids == (client_id,)
    assert ctx.user_id == "dfip-processing-worker"
    assert ctx.subject == "dfip-processing-worker"
    assert ctx.role == "publisher"


def test_processing_rls_is_tenant_scoped_worker_not_platform_admin() -> None:
    reset_rls()
    with processing_rls(CLIENT_A):
        _assert_processing_worker(current_rls(), CLIENT_A)
    assert current_rls() is None


def test_run_job_binds_processing_rls_before_first_get_batch(tmp_path: Path) -> None:
    reset_rls()
    service, ingest = _service()
    _source, batch = _received(ingest)
    seen: list[RlsContext | None] = []
    original = ingest.get_batch

    def wrapped(batch_id: str):
        seen.append(current_rls())
        return original(batch_id)

    ingest.get_batch = wrapped  # type: ignore[method-assign]
    service._ingest_and_transform = _stop_after_lookup  # type: ignore[method-assign]
    dest, folder = _job_paths(tmp_path)
    service._run_job(dest, folder, batch.id, CLIENT_A, DIGEST, False)
    assert seen
    _assert_processing_worker(seen[0], CLIENT_A)
    assert current_rls() is None


def test_run_archive_job_binds_processing_rls_before_first_get_batch(tmp_path: Path) -> None:
    reset_rls()
    service, ingest = _service()
    _source, batch = _received(ingest)
    seen: list[RlsContext | None] = []
    original = ingest.get_batch

    def wrapped(batch_id: str):
        seen.append(current_rls())
        return original(batch_id)

    ingest.get_batch = wrapped  # type: ignore[method-assign]
    service._ingest_and_transform = _stop_after_lookup  # type: ignore[method-assign]
    dest, folder = _job_paths(tmp_path)
    service._run_archive_job(dest, folder, batch.id, CLIENT_A, DIGEST)
    assert seen
    _assert_processing_worker(seen[0], CLIENT_A)
    assert current_rls() is None


def test_run_job_forgets_inflight_when_first_get_batch_raises(tmp_path: Path) -> None:
    reset_rls()
    service, ingest = _service()
    _source, batch = _received(ingest)
    service._remember_inflight(CLIENT_A, DIGEST, batch.id)
    assert service._batch_is_locally_running(batch.id)
    calls = {"n": 0}
    original = ingest.get_batch

    def wrapped(batch_id: str):
        calls["n"] += 1
        _assert_processing_worker(current_rls(), CLIENT_A)
        if calls["n"] == 1:
            raise PermissionError("permission denied for table batch")
        return original(batch_id)

    ingest.get_batch = wrapped  # type: ignore[method-assign]
    dest, folder = _job_paths(tmp_path)
    service._run_job(dest, folder, batch.id, CLIENT_A, DIGEST, False)
    assert service._batch_is_locally_running(batch.id) is False
    failed = original(batch.id)
    assert failed is not None
    assert failed.status == "failed"
    assert current_rls() is None


def test_fail_batch_reads_and_writes_under_processing_rls() -> None:
    reset_rls()
    service, ingest = _service()
    _source, batch = _received(ingest)
    seen: list[RlsContext | None] = []
    original = ingest.get_batch
    saved: list[RlsContext | None] = []
    original_save = ingest.save_batch

    def wrapped_get(batch_id: str):
        seen.append(current_rls())
        return original(batch_id)

    def wrapped_save(record):
        saved.append(current_rls())
        return original_save(record)

    ingest.get_batch = wrapped_get  # type: ignore[method-assign]
    ingest.save_batch = wrapped_save  # type: ignore[method-assign]
    service._fail_batch(batch.id, "worker could not read batch", client_id=CLIENT_A)
    assert seen
    _assert_processing_worker(seen[0], CLIENT_A)
    assert saved
    _assert_processing_worker(saved[0], CLIENT_A)
    failed = original(batch.id)
    assert failed is not None
    assert failed.status == "failed"
    assert failed.error_summary == "worker could not read batch"
    assert current_rls() is None


def test_fail_batch_does_not_replace_http_rls() -> None:
    reset_rls()
    service, ingest = _service()
    _source, batch = _received(ingest)
    http = RlsContext(
        user_id="http-user",
        role="publisher",
        client_ids=(CLIENT_A,),
        platform_admin=False,
        subject="http-user",
    )
    bind_rls(http)
    try:
        seen: list[RlsContext | None] = []
        original = ingest.get_batch

        def wrapped(batch_id: str):
            seen.append(current_rls())
            return original(batch_id)

        ingest.get_batch = wrapped  # type: ignore[method-assign]
        service._fail_batch(batch.id, "http path", client_id=CLIENT_A)
        assert seen
        assert seen[0] is http
        assert seen[0].user_id == "http-user"
        assert current_rls() is http
    finally:
        reset_rls()
    assert current_rls() is None


def test_http_rls_factory_is_unchanged() -> None:
    inspector = Principal(
        subject="http-user",
        auth_mode="jwt",
        role="publisher",
        client_id=CLIENT_ID,
        user_id="http-user",
    )
    assert rls_context_for(inspector, db_mode=False) is None
    bound = rls_context_for(inspector, db_mode=True)
    assert bound is not None
    assert bound.platform_admin is False
    assert bound.client_ids == (CLIENT_ID,)
    assert bound.role == "publisher"
    assert bound.user_id == inspector.user_id
    assert bound.subject == inspector.subject


def test_fail_batch_does_not_replace_startup_recovery_rls() -> None:
    reset_rls()
    service, ingest = _service()
    _source, batch = _received(ingest)
    with startup_recovery_rls():
        seen: list[RlsContext | None] = []
        original = ingest.get_batch

        def wrapped(batch_id: str):
            seen.append(current_rls())
            return original(batch_id)

        ingest.get_batch = wrapped  # type: ignore[method-assign]
        service._fail_batch(batch.id, "recovery path", client_id=CLIENT_A)
        assert seen
        bound = seen[0]
        assert bound is not None
        assert bound.platform_admin is True
        assert bound.role == "admin"
        assert bound.user_id == "dfip-recovery-worker"
        assert bound.client_ids == ()
        assert current_rls() is bound
    assert current_rls() is None


def test_in_memory_worker_cannot_target_another_clients_batch_identity() -> None:
    reset_rls()
    service, ingest = _service()
    _source_a, batch_a = _received(ingest, client_id=CLIENT_A, digest="a" * 64)
    _source_b, batch_b = _received(ingest, client_id=CLIENT_B, digest="b" * 64)
    with processing_rls(CLIENT_A):
        ctx = current_rls()
        _assert_processing_worker(ctx, CLIENT_A)
        assert ctx is not None
        assert batch_b.client_id not in ctx.client_ids
        assert batch_a.client_id in ctx.client_ids
    service._fail_batch(batch_b.id, "cross-tenant", client_id=CLIENT_A)
    # In-memory stores are not RLS-enforcing; the worker context still names
    # only tenant A. Postgres coverage asserts the other client's row is hidden.
    assert ingest.get_batch(batch_b.id) is not None
    assert current_rls() is None


def _seed_received(pg_conn, *, client_id: str, digest: str) -> str:
    source_id = str(uuid4())
    batch_id = str(uuid4())
    pg_conn.execute(
        """
        INSERT INTO client (id, code, name)
        VALUES (%s, %s, %s)
        ON CONFLICT (id) DO NOTHING
        """,
        (client_id, client_id, f"worker-{client_id[-4:]}"),
    )
    pg_conn.execute(
        """
        INSERT INTO source_file (
            id, client_id, sha256, original_filename, byte_size, source_kind, uploaded_at
        )
        VALUES (%s, %s, %s, 'worker.xlsx', 10, 'native_export', NOW())
        """,
        (source_id, client_id, digest),
    )
    pg_conn.execute(
        """
        INSERT INTO batch (
            id, source_file_id, client_id, status, created_at, cancel_requested
        )
        VALUES (%s, %s, %s, 'received', NOW(), FALSE)
        """,
        (batch_id, source_id, client_id),
    )
    pg_conn.commit()
    return batch_id


@pytest.mark.postgres
@requires_postgres
def test_postgres_worker_noinherit_login_needs_processing_rls(
    tmp_path: Path, pg_conn, postgres_url
) -> None:
    reset_rls()
    api_login_url = _ensure_app_login_role(pg_conn, postgres_url)
    assert (
        pg_conn.execute(
            "SELECT has_table_privilege(%s, 'batch', 'SELECT') AS allowed",
            (APP_LOGIN_ROLE,),
        ).fetchone()["allowed"]
        is False
    )
    assert (
        pg_conn.execute(
            "SELECT has_table_privilege('dfip_api', 'batch', 'SELECT') AS allowed"
        ).fetchone()["allowed"]
        is True
    )
    batch_a = _seed_received(pg_conn, client_id=CLIENT_A, digest="c" * 64)
    batch_b = _seed_received(pg_conn, client_id=CLIENT_B, digest="d" * 64)
    pool = create_pool(api_login_url)
    ingest = PostgresIngestStore(pool)
    facts = PostgresFactStore(pool)
    service = UploadService(ingest, facts, make_settings())
    try:
        with pytest.raises(InsufficientPrivilege, match="batch"):
            ingest.get_batch(batch_a)
        seen: list[RlsContext | None] = []
        original = ingest.get_batch

        def wrapped(batch_id: str):
            seen.append(current_rls())
            return original(batch_id)

        ingest.get_batch = wrapped  # type: ignore[method-assign]
        dest, folder = _job_paths(tmp_path)
        service._ingest_and_transform = _stop_after_lookup  # type: ignore[method-assign]
        service._run_job(dest, folder, batch_a, CLIENT_A, "c" * 64, False)
        assert seen
        _assert_processing_worker(seen[0], CLIENT_A)
        ingest.get_batch = original  # type: ignore[method-assign]
        with processing_rls(CLIENT_A):
            owned = ingest.get_batch(batch_a)
            hidden = ingest.get_batch(batch_b)
        assert owned is not None
        assert owned.status == "failed"
        assert owned.client_id == CLIENT_A
        assert hidden is None
        with processing_rls(CLIENT_B):
            other = ingest.get_batch(batch_b)
        assert other is not None
        assert other.status == "received"
        service._fail_batch(batch_b, "should not be visible", client_id=CLIENT_A)
        with processing_rls(CLIENT_B):
            still_b = ingest.get_batch(batch_b)
        assert still_b is not None
        assert still_b.status == "received"
    finally:
        close_pool(pool)
        reset_rls()
        assert current_rls() is None


@pytest.mark.postgres
@requires_postgres
def test_postgres_startup_recovery_still_uses_platform_admin(pg_conn, pg_stores) -> None:
    reset_rls()
    batch_id = _received_batch(pg_conn)
    ingest, facts, _pubs, _repo = pg_stores
    service = UploadService(ingest, facts, make_settings())
    seen: list[object] = []
    original = ingest.list_auto_resume_batches

    def wrapped(*, limit: int = 50, resume_reasons: tuple[str, ...] = ()):
        bound = current_rls()
        seen.append(bound)
        rows = original(limit=limit, resume_reasons=resume_reasons)
        assert any(item.id == batch_id for item in rows)
        return rows

    ingest.list_auto_resume_batches = wrapped  # type: ignore[method-assign]
    service._resume_one_batch = lambda _batch: False  # type: ignore[method-assign]
    try:
        started = service.resume_orphaned_work()
    finally:
        ingest.list_auto_resume_batches = original  # type: ignore[method-assign]
    assert started == 0
    assert seen
    bound = seen[0]
    assert bound is not None
    assert bound.platform_admin is True
    assert bound.role == "admin"
    assert bound.client_ids == ()
    assert bound.user_id == "dfip-recovery-worker"
    assert current_rls() is None
