"""P13F operator purge CLI, backup identify-eligible, temp cleanup.

PostgreSQL cases require DFIP_TEST_DATABASE_URL. Never hosted/live/August.
"""

from __future__ import annotations

import inspect
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from dfip_api.backup import (
    ARCHIVE_RELATIVE,
    DUMP_RELATIVE,
    identify_eligible_backups,
    sha256_file,
    write_manifest,
)
from dfip_api.purge import (
    PurgeError,
    assert_safe_purge_target,
    dry_run,
    execute_sql_purge,
    purge_company,
    stale_upload_dirs,
    tenant_archive_dir,
)
from dfip_api.source_storage import DEFAULT_BUCKET
from dfip_config.catalog_identity import packaged_catalog_owner_client_id
from dfip_db.local_demo_guard import DEFAULT_CLIENT_CODE, DEFAULT_CLIENT_ID

from postgres_support import postgres_only, requires_postgres

CODE_A = "p13f-company-a"
CODE_B = "p13f-company-b"


def _count(conn, sql: str, params: tuple[object, ...] = ()) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(next(iter(row.values())))


def _gone(conn, table: str, client_id: str) -> None:
    sql = f"SELECT COUNT(*) FROM {table} WHERE client_id = %s"
    assert _count(conn, sql, (client_id,)) == 0


def _verified_backup(parent: Path, name: str, created_at: str) -> Path:
    root = parent / name
    dump = root / DUMP_RELATIVE
    dump.parent.mkdir(parents=True, exist_ok=True)
    dump.write_bytes(f"dump-{name}".encode())
    (root / ARCHIVE_RELATIVE).mkdir(parents=True, exist_ok=True)
    write_manifest(
        root / "manifest.json",
        {
            "created_at": created_at,
            "postgres": {
                "dump_filename": DUMP_RELATIVE,
                "dump_sha256": sha256_file(dump),
            },
            "source_archive": {"inventory": [], "bucket": DEFAULT_BUCKET},
        },
    )
    return root


def test_hosted_and_unconfirmed_purge_refused() -> None:
    with pytest.raises(PurgeError, match="confirm-disposable"):
        assert_safe_purge_target("postgresql://dfip@127.0.0.1:5432/dfip_test", confirmed=False)
    with pytest.raises(PurgeError, match="hosted"):
        assert_safe_purge_target(
            "postgresql://postgres.abc:x@aws-0-us.pooler.supabase.com:5432/postgres",
            confirmed=True,
        )
    with pytest.raises(PurgeError, match="database name"):
        assert_safe_purge_target("postgresql://dfip@127.0.0.1:5432/dfip", confirmed=True)
    with pytest.raises(PurgeError, match="database name"):
        assert_safe_purge_target("postgresql://dfip@127.0.0.1:5432/postgres", confirmed=True)


def test_purge_sql_has_no_cascade() -> None:
    source = inspect.getsource(execute_sql_purge)
    assert "CASCADE" not in source
    assert "%s" in source


def test_identify_eligible_never_selects_only_verified(tmp_path: Path) -> None:
    only = _verified_backup(tmp_path, "only", "2026-01-01T00:00:00+00:00")
    report = identify_eligible_backups(tmp_path, keep_count=1)
    assert report["verified_count"] == 1
    assert report["eligible"] == []
    assert report["deleted"] == []
    assert report["protected"][0]["path"] == str(only.resolve())

    unverified = tmp_path / "broken"
    unverified.mkdir()
    (unverified / "manifest.json").write_text("{}", encoding="utf-8")
    report = identify_eligible_backups(tmp_path, keep_count=1)
    assert report["eligible"] == []
    assert any(not item["verified"] for item in report["unverified"])

    older = _verified_backup(tmp_path, "older", "2025-01-01T00:00:00+00:00")
    newer = _verified_backup(tmp_path, "newer", "2026-06-01T00:00:00+00:00")
    report = identify_eligible_backups(tmp_path, keep_count=1)
    eligible_paths = {item["path"] for item in report["eligible"]}
    assert str(newer.resolve()) not in eligible_paths
    assert str(older.resolve()) in eligible_paths
    assert str(only.resolve()) in eligible_paths


def test_stale_upload_helper_ignores_other_dirs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TMP", str(tmp_path))
    monkeypatch.setenv("TEMP", str(tmp_path))
    import tempfile

    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    stale = tmp_path / "dfip-upload-old"
    stale.mkdir()
    fresh = tmp_path / "dfip-upload-new"
    fresh.mkdir()
    other = tmp_path / "generated-user"
    other.mkdir()
    old_mtime = (datetime.now(tz=UTC) - timedelta(hours=48)).timestamp()
    os.utime(stale, (old_mtime, old_mtime))
    found = stale_upload_dirs(max_age_hours=24)
    names = {item.name for item in found}
    assert "dfip-upload-old" in names
    assert "dfip-upload-new" not in names
    assert "generated-user" not in names


def _insert_company(conn, *, code: str, name: str) -> str:
    client_id = str(uuid4())
    conn.execute(
        "INSERT INTO client (id, code, name) VALUES (%s, %s, %s)",
        (client_id, code, name),
    )
    return client_id


def _insert_user(
    conn, *, subject: str, client_id: str, role: str, platform_admin: bool = False
) -> str:
    user_id = str(uuid4())
    conn.execute(
        """
        INSERT INTO app_user (id, subject, is_platform_admin, token_version)
        VALUES (%s, %s, %s, 1)
        """,
        (user_id, subject, platform_admin),
    )
    conn.execute(
        "INSERT INTO client_membership (user_id, client_id, role) VALUES (%s, %s, %s)",
        (user_id, client_id, role),
    )
    return user_id


def _seed_tenant(
    conn, *, client_id: str, archive: Path, with_overlay: bool = True
) -> dict[str, str]:
    source_id = str(uuid4())
    batch_id = str(uuid4())
    run_id = str(uuid4())
    pub_id = str(uuid4())
    sha = uuid4().hex + uuid4().hex
    conn.execute(
        """
        INSERT INTO source_file (
            id, client_id, sha256, original_filename, byte_size, source_kind, uploaded_at
        )
        VALUES (%s, %s, %s, 'raw.xlsx', 12, 'native_export', now())
        """,
        (source_id, client_id, sha),
    )
    conn.execute(
        """
        INSERT INTO batch (
            id, source_file_id, client_id, status, row_count_declared, row_count_staged,
            row_count_rejected, created_at
        )
        VALUES (%s, %s, %s, 'processed', 1, 1, 1, now())
        """,
        (batch_id, source_id, client_id),
    )
    conn.execute(
        """
        INSERT INTO processing_run (
            id, batch_id, client_id, engine_version, started_at, finished_at, status
        )
        VALUES (%s, %s, %s, '0.4.0', now(), now(), 'succeeded')
        """,
        (run_id, batch_id, client_id),
    )
    conn.execute(
        """
        INSERT INTO stg_source_row (batch_id, client_id, source_row_number, raw)
        VALUES (%s, %s, 1, '{}'::jsonb)
        """,
        (batch_id, client_id),
    )
    conn.execute(
        """
        INSERT INTO stg_rejected_row (batch_id, client_id, source_row_number, raw, reason_code)
        VALUES (%s, %s, 2, '{}'::jsonb, 'test')
        """,
        (batch_id, client_id),
    )
    conn.execute(
        """
        INSERT INTO fact_campaign_day (
            client_id, campaign_id, variation_id_key, day, first_seen_at, last_seen_at,
            processing_run_id, batch_id
        )
        VALUES (%s, 'camp-a', 'var-a', DATE '2025-08-01', now(), now(), %s, %s)
        """,
        (client_id, run_id, batch_id),
    )
    conn.execute(
        """
        INSERT INTO fact_campaign_day_history (
            client_id, campaign_id, variation_id_key, day, first_seen_at, last_seen_at,
            superseded_at, processing_run_id, batch_id
        )
        VALUES (%s, 'camp-a', 'var-a', DATE '2025-08-01', now(), now(), now(), %s, %s)
        """,
        (client_id, run_id, batch_id),
    )
    conn.execute(
        """
        INSERT INTO qa_finding (
            client_id, processing_run_id, batch_id, rule_id, severity, status,
            entity_type, entity_key, message
        )
        VALUES (%s, %s, %s, 'rule-1', 'info', 'detected', 'run', 'k', 'ok')
        """,
        (client_id, run_id, batch_id),
    )
    conn.execute(
        """
        INSERT INTO publication (
            id, client_id, processing_run_id, published_by, snapshot_status, snapshot_row_count
        )
        VALUES (%s, %s, %s, 'tester', 'none', NULL)
        """,
        (pub_id, client_id, run_id),
    )
    conn.execute(
        """
        INSERT INTO publication_current (client_id, publication_id, updated_at)
        VALUES (%s, %s, now())
        """,
        (client_id, pub_id),
    )
    conn.execute(
        """
        INSERT INTO publication_fact (
            publication_id, client_id, campaign_id, variation_id_key, day,
            first_seen_at, last_seen_at
        )
        VALUES (%s, %s, 'camp-a', 'var-a', DATE '2025-08-01', now(), now())
        """,
        (pub_id, client_id),
    )
    if with_overlay:
        version_id = str(uuid4())
        conn.execute(
            """
            INSERT INTO campaign_label_version (
                id, client_id, version_label, status, created_by, notes
            )
            VALUES (%s, %s, 'p13f-overlay', 'draft', 'tester', 'p13f')
            """,
            (version_id, client_id),
        )
        conn.execute(
            """
            INSERT INTO campaign_label_row (version_id, row_order, campaign_name)
            VALUES (%s, 1, 'Overlay')
            """,
            (version_id,),
        )
    conn.execute(
        """
        INSERT INTO audit_log (actor, action, entity_type, entity_id, client_id)
        VALUES ('tester', 'seed', 'client', %s, %s)
        """,
        (client_id, client_id),
    )
    dest = tenant_archive_dir(archive, client_id)
    dest.mkdir(parents=True, exist_ok=True)
    (dest / f"{source_id}.xlsx").write_bytes(b"source-bytes")
    return {
        "source_id": source_id,
        "batch_id": batch_id,
        "run_id": run_id,
        "publication_id": pub_id,
    }


def _deactivate_eligible(conn, client_id: str) -> None:
    conn.execute(
        """
        UPDATE client
        SET lifecycle_status = 'inactive',
            deactivated_at = now(),
            purge_eligible_after = now() - interval '1 second'
        WHERE id = %s
        """,
        (client_id,),
    )


@requires_postgres
@postgres_only
def test_postgres_purge_isolation_and_dry_run(pg_conn, postgres_url: str, tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    archive.mkdir()
    backups = tmp_path / "backups"
    backup_file = _verified_backup(backups, "pre-purge", "2026-01-01T00:00:00+00:00")
    before_hash = sha256_file(backup_file / DUMP_RELATIVE)

    company_a = _insert_company(pg_conn, code=CODE_A, name="Disposable A")
    company_b = _insert_company(pg_conn, code=CODE_B, name="Disposable B")
    solo = _insert_user(pg_conn, subject="p13f-solo-a", client_id=company_a, role="client")
    shared = _insert_user(pg_conn, subject="p13f-shared", client_id=company_a, role="publisher")
    pg_conn.execute(
        "INSERT INTO client_membership (user_id, client_id, role) VALUES (%s, %s, 'publisher')",
        (shared, company_b),
    )
    admin_id = str(uuid4())
    pg_conn.execute(
        """
        INSERT INTO app_user (id, subject, is_platform_admin, token_version)
        VALUES (%s, %s, true, 1)
        """,
        (admin_id, "p13f-platform-admin"),
    )
    pg_conn.execute(
        "INSERT INTO client_membership (user_id, client_id, role) VALUES (%s, %s, 'publisher')",
        (admin_id, company_a),
    )
    _insert_user(pg_conn, subject="p13f-b-only", client_id=company_b, role="client")
    _seed_tenant(pg_conn, client_id=company_a, archive=archive)
    _seed_tenant(pg_conn, client_id=company_b, archive=archive)
    pg_conn.commit()

    with pytest.raises(PurgeError, match="not purge-eligible"):
        purge_company(
            database_url=postgres_url,
            code=CODE_A,
            archive_root=archive,
            bucket=DEFAULT_BUCKET,
            confirm_disposable=True,
            confirm_purge=True,
        )

    _deactivate_eligible(pg_conn, company_a)
    pg_conn.commit()

    with pytest.raises(PurgeError, match="confirm-purge"):
        purge_company(
            database_url=postgres_url,
            code=CODE_A,
            archive_root=archive,
            bucket=DEFAULT_BUCKET,
            confirm_disposable=True,
            confirm_purge=False,
        )

    with pytest.raises(PurgeError, match="not found"):
        dry_run(
            database_url=postgres_url,
            code="no-such-company-code",
            archive_root=archive,
            bucket=DEFAULT_BUCKET,
            confirm_disposable=True,
        )

    before_b_facts = _count(
        pg_conn, "SELECT COUNT(*) FROM fact_campaign_day WHERE client_id = %s", (company_b,)
    )
    inventory = dry_run(
        database_url=postgres_url,
        code=CODE_A,
        archive_root=archive,
        bucket=DEFAULT_BUCKET,
        confirm_disposable=True,
    )
    assert inventory["mutated"] is False
    assert inventory["code"] == CODE_A
    assert inventory["client_id"] == company_a
    assert inventory["legacy_snapshot_none"] == 1
    assert inventory["facts"] == 1
    assert inventory["publication_facts"] == 1
    assert inventory["memberships"] == 3
    deleted_subjects = {item["subject"] for item in inventory["users_deleted"]}
    retained_subjects = {item["subject"] for item in inventory["users_retained"]}
    assert "p13f-solo-a" in deleted_subjects
    assert "p13f-shared" in retained_subjects
    assert "p13f-platform-admin" in retained_subjects
    assert _count(pg_conn, "SELECT COUNT(*) FROM client WHERE id = %s", (company_a,)) == 1

    with pytest.raises(PurgeError, match="not purge-eligible"):
        purge_company(
            database_url=postgres_url,
            code=CODE_B,
            archive_root=archive,
            bucket=DEFAULT_BUCKET,
            confirm_disposable=True,
            confirm_purge=True,
        )

    result = purge_company(
        database_url=postgres_url,
        code=CODE_A,
        archive_root=archive,
        bucket=DEFAULT_BUCKET,
        confirm_disposable=True,
        confirm_purge=True,
    )
    assert result["ok"] is True
    assert result["database_purged"] is True
    assert result["archive_removed"] is True
    assert result["counts"]["facts"] == inventory["facts"]
    assert result["counts"]["legacy_snapshot_none"] == inventory["legacy_snapshot_none"]
    assert result["counts"]["users_deleted"] == inventory["users_deleted"]

    pg_conn.commit()
    assert _count(pg_conn, "SELECT COUNT(*) FROM client WHERE id = %s", (company_a,)) == 0
    assert _count(pg_conn, "SELECT COUNT(*) FROM client WHERE id = %s", (company_b,)) == 1
    for table in (
        "publication_current",
        "publication_history_grain",
        "excel_workbook_grant",
        "analytics_saved_analysis",
        "publication_fact",
        "publication",
        "fact_campaign_day",
        "fact_campaign_day_history",
        "stg_source_row",
        "batch",
        "processing_run",
        "source_file",
        "campaign_label_version",
        "client_membership",
    ):
        _gone(pg_conn, table, company_a)
    assert _count(pg_conn, "SELECT COUNT(*) FROM app_user WHERE id = %s", (solo,)) == 0
    assert _count(pg_conn, "SELECT COUNT(*) FROM app_user WHERE id = %s", (shared,)) == 1
    assert _count(pg_conn, "SELECT COUNT(*) FROM app_user WHERE id = %s", (admin_id,)) == 1
    remaining_b = _count(
        pg_conn, "SELECT COUNT(*) FROM fact_campaign_day WHERE client_id = %s", (company_b,)
    )
    assert remaining_b == before_b_facts
    assert not tenant_archive_dir(archive, company_a).exists()
    assert tenant_archive_dir(archive, company_b).is_dir()
    assert sha256_file(backup_file / DUMP_RELATIVE) == before_hash

    grants = pg_conn.execute(
        """
        SELECT privilege_type
        FROM information_schema.role_table_grants
        WHERE grantee = 'dfip_api'
          AND table_schema = 'public'
          AND table_name IN ('client', 'app_user', 'client_membership', 'publication_fact')
        """
    ).fetchall()
    delete_grants = {row["privilege_type"] for row in grants if row["privilege_type"] == "DELETE"}
    assert delete_grants == set()

    with pytest.raises(PurgeError, match="not found"):
        purge_company(
            database_url=postgres_url,
            code=CODE_A,
            archive_root=archive,
            bucket=DEFAULT_BUCKET,
            confirm_disposable=True,
            confirm_purge=True,
        )


@requires_postgres
@postgres_only
def test_postgres_purge_rollback_leaves_company(pg_conn, postgres_url: str, tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    archive.mkdir()
    company_a = _insert_company(pg_conn, code="p13f-rollback-a", name="Rollback A")
    _insert_user(pg_conn, subject="p13f-rollback-user", client_id=company_a, role="client")
    _seed_tenant(pg_conn, client_id=company_a, archive=archive)
    _deactivate_eligible(pg_conn, company_a)
    pg_conn.commit()
    with pytest.raises(PurgeError, match="Injected failure"):
        purge_company(
            database_url=postgres_url,
            code="p13f-rollback-a",
            archive_root=archive,
            bucket=DEFAULT_BUCKET,
            confirm_disposable=True,
            confirm_purge=True,
            fail_before="client",
        )
    pg_conn.commit()
    assert _count(pg_conn, "SELECT COUNT(*) FROM client WHERE id = %s", (company_a,)) == 1
    assert (
        _count(pg_conn, "SELECT COUNT(*) FROM publication WHERE client_id = %s", (company_a,)) == 1
    )
    assert (
        _count(pg_conn, "SELECT COUNT(*) FROM fact_campaign_day WHERE client_id = %s", (company_a,))
        == 1
    )
    assert tenant_archive_dir(archive, company_a).is_dir()


@requires_postgres
@postgres_only
def test_postgres_filesystem_failure_is_incomplete(
    pg_conn, postgres_url: str, tmp_path: Path
) -> None:
    archive = tmp_path / "archive"
    archive.mkdir()
    company_a = _insert_company(pg_conn, code="p13f-fs-a", name="Filesystem A")
    _insert_user(pg_conn, subject="p13f-fs-user", client_id=company_a, role="client")
    _seed_tenant(pg_conn, client_id=company_a, archive=archive)
    _deactivate_eligible(pg_conn, company_a)
    pg_conn.commit()

    def _boom(_path: Path) -> None:
        raise OSError("cannot remove")

    result = purge_company(
        database_url=postgres_url,
        code="p13f-fs-a",
        archive_root=archive,
        bucket=DEFAULT_BUCKET,
        confirm_disposable=True,
        confirm_purge=True,
        archive_remover=_boom,
    )
    assert result["ok"] is False
    assert result["database_purged"] is True
    assert result["archive_removed"] is False
    assert "archive" in str(result.get("error", "")).lower()
    pg_conn.commit()
    assert _count(pg_conn, "SELECT COUNT(*) FROM client WHERE id = %s", (company_a,)) == 0


@requires_postgres
@postgres_only
def test_postgres_default_owner_purge_refused(pg_conn, postgres_url: str, tmp_path: Path) -> None:
    _deactivate_eligible(pg_conn, DEFAULT_CLIENT_ID)
    pg_conn.commit()
    try:
        with pytest.raises(PurgeError, match="packaged default"):
            purge_company(
                database_url=postgres_url,
                code=DEFAULT_CLIENT_CODE,
                archive_root=tmp_path,
                bucket=DEFAULT_BUCKET,
                confirm_disposable=True,
                confirm_purge=True,
            )
        assert (
            _count(pg_conn, "SELECT COUNT(*) FROM client WHERE id = %s", (DEFAULT_CLIENT_ID,)) == 1
        )
        assert packaged_catalog_owner_client_id() == DEFAULT_CLIENT_ID
    finally:
        pg_conn.execute(
            """
            UPDATE client
            SET lifecycle_status = 'active',
                deactivated_at = NULL,
                purge_eligible_after = NULL
            WHERE id = %s
            """,
            (DEFAULT_CLIENT_ID,),
        )
        pg_conn.commit()
