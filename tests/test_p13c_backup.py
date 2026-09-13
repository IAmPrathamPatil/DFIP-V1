"""P13C backup/restore.

In-memory tests do not open PostgreSQL. Restore acceptance uses
DFIP_TEST_DATABASE_URL and a newly created disposable database. It does not
touch hosted, live, or August data.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest
from dfip_api.app import create_app
from dfip_api.backup import (
    ARCHIVE_RELATIVE,
    DUMP_RELATIVE,
    MANIFEST_NAME,
    BackupError,
    _tool_major,
    assert_privileged_dump_role,
    assert_safe_restore_target,
    copy_archive_tree,
    create_backup,
    create_empty_database,
    drop_database,
    expected_archive_relative,
    inspect_dump,
    inventory_archive,
    parse_storage_uri,
    postgres_tools_available,
    restore_backup,
    restore_database_report,
    sha256_file,
    verify_backup,
    verify_source_mapping,
    write_manifest,
)
from dfip_api.source_storage import DEFAULT_BUCKET, source_object_key, source_object_uri
from dfip_web.pivot_report import assert_native_pivot_package
from fastapi.testclient import TestClient
from psycopg import connect
from psycopg.rows import dict_row

from http_ingest_support import (
    complete_reprocess_response,
    source_row,
    upload_workbook,
    workbook_bytes,
)
from postgres_support import (
    CLIENT_A,
    CLIENT_B,
    requires_postgres,
    seed_identity,
)
from test_p5_api import JWT_SECRET, _encode_jwt, make_settings


def _uri(client_id: str, source_id: str, sha256: str, bucket: str = DEFAULT_BUCKET) -> str:
    return source_object_uri(bucket, source_object_key(client_id, source_id, sha256))


def _write_xlsx(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def test_pg_tool_major_parses_client_and_server_strings() -> None:
    assert _tool_major("pg_dump (PostgreSQL) 18.4") == 18
    assert _tool_major("pg_dump (PostgreSQL) 16.15 (Debian 16.15-1.pgdg13+2)") == 16
    assert _tool_major("16.15 (Debian 16.15-1.pgdg13+2)") == 16


def test_parse_storage_uri_and_expected_path() -> None:
    sha = "a" * 64
    uri = _uri(CLIENT_A, CLIENT_B, sha)
    bucket, client_id, source_id, digest = parse_storage_uri(uri)
    assert bucket == DEFAULT_BUCKET
    assert client_id == CLIENT_A
    assert source_id == CLIENT_B
    assert digest == sha
    assert expected_archive_relative(uri) == (f"{DEFAULT_BUCKET}/{CLIENT_A}/{CLIENT_B}/{sha}.xlsx")


def test_malformed_storage_uri_is_rejected() -> None:
    with pytest.raises(BackupError, match="malformed"):
        parse_storage_uri("https://example.invalid/file.xlsx")


def test_archive_inventory_is_deterministic(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    first = root / DEFAULT_BUCKET / CLIENT_A / CLIENT_B / f"{'b' * 64}.xlsx"
    second = root / DEFAULT_BUCKET / CLIENT_B / CLIENT_A / f"{'a' * 64}.xlsx"
    _write_xlsx(first, b"one")
    _write_xlsx(second, b"two")
    items = inventory_archive(root)
    paths = [item.path for item in items]
    assert paths == sorted(paths)
    assert {item.sha256 for item in items} == {
        hashlib.sha256(b"one").hexdigest(),
        hashlib.sha256(b"two").hexdigest(),
    }


def test_mapping_detects_missing_sha_size_and_malformed(tmp_path: Path) -> None:
    source_id = "b0000000-0000-4000-8000-000000000099"
    payload = b"workbook-bytes"
    digest = hashlib.sha256(payload).hexdigest()
    uri = _uri(CLIENT_A, source_id, digest)
    relative = expected_archive_relative(uri)
    archive = tmp_path / "archive"
    _write_xlsx(archive.joinpath(*relative.split("/")), payload)
    good = {
        "id": source_id,
        "client_id": CLIENT_A,
        "sha256": digest,
        "byte_size": len(payload),
        "storage_uri": uri,
    }
    ok = verify_source_mapping([good], archive, expected_bucket=DEFAULT_BUCKET)
    assert ok.is_ok()
    missing = verify_source_mapping(
        [
            {
                **good,
                "id": "b0000000-0000-4000-8000-000000000098",
                "storage_uri": _uri(CLIENT_A, "b0000000-0000-4000-8000-000000000098", digest),
            }
        ],
        archive,
    )
    assert missing.missing
    archive.joinpath(*relative.split("/")).write_bytes(b"tampered-workbook")
    sha_bad = verify_source_mapping([good], archive)
    assert sha_bad.sha_mismatch
    _write_xlsx(archive.joinpath(*relative.split("/")), payload)
    size_bad = verify_source_mapping([{**good, "byte_size": 1}], archive)
    assert size_bad.size_mismatch
    malformed = verify_source_mapping(
        [{**good, "storage_uri": "not-a-uri"}],
        archive,
    )
    assert malformed.malformed_uri
    extra = archive / DEFAULT_BUCKET / "orphan.xlsx"
    _write_xlsx(extra, b"orphan")
    with_orphan = verify_source_mapping([good], archive)
    assert with_orphan.orphan_count >= 1
    assert with_orphan.is_ok()


def test_manifest_refuses_secret_fields(tmp_path: Path) -> None:
    with pytest.raises(BackupError, match="secret"):
        write_manifest(tmp_path / "manifest.json", {"password": "nope"})


def test_verify_detects_missing_dump_and_checksum(tmp_path: Path) -> None:
    backup = tmp_path / "backup"
    backup.mkdir()
    write_manifest(
        backup / MANIFEST_NAME,
        {
            "postgres": {"dump_sha256": "abc"},
            "source_archive": {"bucket": DEFAULT_BUCKET, "inventory": []},
        },
    )
    with pytest.raises(BackupError, match="dump file is missing"):
        verify_backup(backup)
    dump = backup / DUMP_RELATIVE
    dump.parent.mkdir(parents=True)
    dump.write_bytes(b"not-a-real-dump")
    (backup / ARCHIVE_RELATIVE).mkdir()
    with pytest.raises(BackupError, match="checksum"):
        verify_backup(backup)
    write_manifest(
        backup / MANIFEST_NAME,
        {
            "postgres": {"dump_sha256": sha256_file(dump)},
            "source_archive": {"bucket": DEFAULT_BUCKET, "inventory": []},
        },
    )
    assert verify_backup(backup)["ok"] is True
    (backup / ARCHIVE_RELATIVE / "extra.xlsx").write_bytes(b"x")
    with pytest.raises(BackupError, match="inventory"):
        verify_backup(backup)


def test_restore_refuses_production_database_name() -> None:
    with pytest.raises(BackupError, match="database name"):
        assert_safe_restore_target(
            "postgresql://postgres@127.0.0.1:5432/dfip",
            confirmed=True,
        )
    with pytest.raises(BackupError, match="confirm"):
        assert_safe_restore_target(
            "postgresql://postgres@127.0.0.1:5432/dfip_p13c_restore",
            confirmed=False,
        )
    with pytest.raises(BackupError, match="hosted"):
        assert_safe_restore_target(
            "postgresql://postgres.project:secret@aws-0-region.pooler.supabase.com:5432/postgres",
            confirmed=True,
        )


def test_copy_archive_preserves_hierarchy(tmp_path: Path) -> None:
    source = tmp_path / "src"
    dest = tmp_path / "dest"
    relative = f"{DEFAULT_BUCKET}/{CLIENT_A}/{CLIENT_B}/{'e' * 64}.xlsx"
    _write_xlsx(source.joinpath(*relative.split("/")), b"keep")
    copy_archive_tree(source, dest)
    assert (dest.joinpath(*relative.split("/"))).read_bytes() == b"keep"


def _pg_app(postgres_url: str, archive: Path) -> TestClient:
    return TestClient(
        create_app(
            settings=make_settings(
                dfip_auth_mode="jwt",
                dfip_auth_secret=JWT_SECRET,
                database_url=postgres_url,
                dfip_env="test",
                dfip_storage_endpoint=str(archive),
            )
        )
    )


def _pg_auth(subject: str, client_id: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_encode_jwt(role='publisher', client_id=client_id, sub=subject)}"
    }


requires_pg_dump = pytest.mark.skipif(
    not postgres_tools_available(),
    reason="pg_dump/pg_restore are not on PATH and compose dfip_db is unavailable",
)


def _publish(http: TestClient, headers: dict[str, str], run_id: str, client_id: str) -> str:
    created = http.post(
        "/api/v1/publications",
        headers=headers,
        json={"client_id": client_id, "processing_run_id": run_id},
    )
    assert created.status_code == 201, created.json()
    return created.json()["publication"]["publication_id"]


def _upload_process(
    http: TestClient,
    tmp_path: Path,
    headers: dict[str, str],
    name: str,
    **row,
):
    uploaded = upload_workbook(
        http,
        workbook_bytes(tmp_path / name, [source_row(**row)]),
        name,
        headers=headers,
    )
    assert uploaded.status_code == 201, uploaded.json()
    run_id = uploaded.json()["processing_run"]["processing_run_id"]
    qa = http.post(f"/api/v1/processing-runs/{run_id}/qa", headers=headers)
    assert qa.status_code == 200, qa.json()
    return uploaded.json()


@pytest.mark.postgres
@requires_postgres
@requires_pg_dump
def test_postgres_backup_restore_preserves_history_and_isolation(
    tmp_path: Path, pg_conn, postgres_url
) -> None:
    seed_identity(pg_conn, subject="publisher-a", role="publisher", client_id=CLIENT_A)
    seed_identity(pg_conn, subject="publisher-b", role="publisher", client_id=CLIENT_B)
    archive = tmp_path / "live-archive"
    http = _pg_app(postgres_url, archive)
    headers_a = _pg_auth("publisher-a", CLIENT_A)
    headers_b = _pg_auth("publisher-b", CLIENT_B)
    first = _upload_process(http, tmp_path, headers_a, "a1.xlsx", **{"Campaign ID": "camp-a1"})
    a1 = _publish(http, headers_a, first["processing_run"]["processing_run_id"], CLIENT_A)
    second = _upload_process(http, tmp_path, headers_a, "a2.xlsx", **{"Campaign ID": "camp-a2"})
    a2 = _publish(http, headers_a, second["processing_run"]["processing_run_id"], CLIENT_A)
    company_b = _upload_process(http, tmp_path, headers_b, "b1.xlsx", **{"Campaign ID": "camp-b1"})
    b1 = _publish(http, headers_b, company_b["processing_run"]["processing_run_id"], CLIENT_B)
    abandoned_run = second["processing_run"]["processing_run_id"]
    http.app.state.upload_executor.shutdown(wait=True, cancel_futures=False)
    http.close()
    with connect(postgres_url, row_factory=dict_row, autocommit=True) as conn:
        conn.execute(
            """
            UPDATE processing_run
            SET status = 'running', finished_at = NULL, error_summary = NULL
            WHERE id = %s
            """,
            (abandoned_run,),
        )
        privilege = conn.execute(
            """
            SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user
            """
        ).fetchone()
    assert privilege is not None
    assert bool(privilege["rolsuper"]) or bool(privilege["rolbypassrls"])
    assert_privileged_dump_role(postgres_url)
    backup_root = tmp_path / "backup-set"
    manifest = create_backup(
        database_url=postgres_url,
        archive_root=archive,
        output_root=backup_root,
    )
    payload = json.dumps(manifest)
    assert "DATABASE_URL" not in payload
    assert "DFIP_AUTH_SECRET" not in payload
    assert "DFIP_BOOTSTRAP_TOKEN" not in payload
    assert "postgresql://" not in payload.lower()
    assert (backup_root / DUMP_RELATIVE).is_file()
    inspect_dump(backup_root / DUMP_RELATIVE)
    verify_backup(backup_root, database_url=postgres_url)
    displaced = tmp_path / "displaced-archive"
    archive.rename(displaced)
    restore_name = f"dfip_p13c_{uuid4().hex[:12]}"
    restore_url = None
    restored_archive = tmp_path / "restored-archive"
    try:
        restore_url = create_empty_database(postgres_url, restore_name)
        restore_backup(
            backup_root,
            target_url=restore_url,
            target_archive_root=restored_archive,
            confirm_disposable=True,
        )
        db_report = restore_database_report(restore_url)
        assert db_report["ok"] is True
        assert db_report["publication_history"] >= 3
        assert db_report["publication_current"] >= 2
        restored = None
        try:
            restored = _pg_app(restore_url, restored_archive)
            current_a = restored.get("/api/v1/publications/current", headers=headers_a).json()
            assert current_a["publication"]["publication_id"] == a2
            history_a = restored.get("/api/v1/publications", headers=headers_a).json()
            ids_a = [item["publication_id"] for item in history_a["items"]]
            assert a1 in ids_a
            assert a2 in ids_a
            assert b1 not in ids_a
            current_b = restored.get("/api/v1/publications/current", headers=headers_b).json()
            assert current_b["publication"]["publication_id"] == b1
            denied = restored.get(f"/api/v1/publications/{b1}/facts", headers=headers_a)
            assert denied.status_code in {403, 404}
            denied_b = restored.get(f"/api/v1/publications/{a2}/facts", headers=headers_b)
            assert denied_b.status_code in {403, 404}
            current_xlsx = restored.get(
                "/api/v1/publications/current/client-report.xlsx", headers=headers_a
            )
            assert current_xlsx.status_code == 200
            assert current_xlsx.content.startswith(b"PK")
            assert_native_pivot_package(current_xlsx.content)
            historical = restored.get(
                f"/api/v1/publications/{a1}/client-report.xlsx", headers=headers_a
            )
            assert historical.status_code == 200
            assert historical.content.startswith(b"PK")
            assert_native_pivot_package(historical.content)
            abandoned = restored.get(
                f"/api/v1/processing-runs/{abandoned_run}", headers=headers_a
            ).json()
            assert abandoned["status"] == "failed"
            assert "abandoned" in (abandoned.get("error_summary") or "").lower()
            retried = complete_reprocess_response(
                restored,
                restored.post(
                    f"/api/v1/batches/{second['batch']['batch_id']}/process",
                    headers=headers_a,
                ),
                headers=headers_a,
            ).json()
            assert retried["processing_run"]["status"] == "succeeded"
            assert retried["published"] is False
            source_id = second["source_file_id"]
            sha = second["sha256"]
            assert restored.app.state.source_store.exists(
                client_id=CLIENT_A, source_file_id=source_id, sha256=sha
            )
        finally:
            if restored is not None:
                restored.app.state.upload_executor.shutdown(wait=True, cancel_futures=False)
                restored.close()
    finally:
        if restore_url:
            drop_database(postgres_url, restore_name)


def test_incomplete_archive_fails_backup_verification(tmp_path: Path) -> None:
    sha = "f" * 64
    source_id = "b0000000-0000-4000-8000-000000000077"
    uri = _uri(CLIENT_A, source_id, sha)
    archive = tmp_path / "empty-archive"
    archive.mkdir()
    report = verify_source_mapping(
        [
            {
                "id": source_id,
                "client_id": CLIENT_A,
                "sha256": sha,
                "byte_size": 4,
                "storage_uri": uri,
            }
        ],
        archive,
    )
    assert report.missing
    assert not report.is_ok()
