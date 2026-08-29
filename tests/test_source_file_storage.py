"""Durable private source-file storage for HTTP uploads."""

from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import uuid4

import pytest
from dfip_api.app import create_app
from dfip_api.errors import AuthorizationError
from dfip_api.source_storage import (
    FilesystemSourceObjectStore,
    InMemorySourceObjectStore,
    build_source_object_store,
    source_object_key,
)
from dfip_core.ingest.store import InMemoryIngestStore
from dfip_core.transform.store import InMemoryFactStore
from fastapi.testclient import TestClient

from http_ingest_support import source_row, upload_workbook, workbook_bytes
from test_p5_api import AUTH, CLIENT_ID, JWT_SECRET, _encode_jwt, make_settings
from test_p7_publication import publisher_settings

CLIENT_B = "a0000000-0000-4000-8000-000000000002"


class FailingSourceStore(InMemorySourceObjectStore):
    def put(self, **_kwargs):
        raise OSError("disk full")


def _publisher_app(source_store=None, **overrides):
    return create_app(
        settings=publisher_settings(**overrides),
        ingest_store=InMemoryIngestStore(),
        fact_store=InMemoryFactStore(),
        source_store=source_store,
    )


def _upload(client, content: bytes, filename: str, **form):
    return upload_workbook(client, content, filename, headers=AUTH, **form)


def test_new_upload_archives_original_bytes_and_sets_storage_uri(tmp_path: Path) -> None:
    store = InMemorySourceObjectStore()
    app = _publisher_app(store)
    http = TestClient(app)
    content = workbook_bytes(tmp_path / "a.xlsx", [source_row()])
    digest = hashlib.sha256(content).hexdigest()
    response = _upload(http, content, "raw-jan.xlsx", client_id=CLIENT_ID)
    assert response.status_code == 201
    body = response.json()
    assert "storage_uri" not in body
    source = app.state.ingest_store.get_source_file(body["source_file_id"])
    assert source is not None
    assert source.storage_uri is not None
    assert source.storage_uri.startswith("dfip-source://dfip-source-files/")
    assert store.object_count() == 1
    stored = store.get(
        client_id=CLIENT_ID, source_file_id=source.id, sha256=digest
    )
    assert stored == content
    assert hashlib.sha256(stored).hexdigest() == digest
    listed = http.get("/api/v1/source-files", headers=AUTH).json()["items"][0]
    assert "storage_uri" not in listed
    assert "dfip-source://" not in http.get("/api/v1/source-files", headers=AUTH).text


def test_local_temp_directory_is_removed_and_object_remains(tmp_path: Path, monkeypatch) -> None:
    store = InMemorySourceObjectStore()
    work = tmp_path / "work"
    created: list[Path] = []

    def fake_mkdtemp(prefix="dfip-upload-", **_kwargs):
        folder = work / f"{prefix}{uuid4().hex}"
        folder.mkdir(parents=True)
        created.append(folder)
        return str(folder)

    monkeypatch.setattr("dfip_api.upload_service.tempfile.mkdtemp", fake_mkdtemp)
    app = _publisher_app(store)
    content = workbook_bytes(tmp_path / "a.xlsx", [source_row()])
    response = _upload(TestClient(app), content, "a.xlsx", client_id=CLIENT_ID)
    assert response.status_code == 201
    assert created
    assert all(not folder.exists() for folder in created)
    source = app.state.ingest_store.get_source_file(response.json()["source_file_id"])
    assert source is not None
    assert store.exists(client_id=CLIENT_ID, source_file_id=source.id, sha256=source.sha256)


def test_same_sha_replay_does_not_duplicate_object(tmp_path: Path) -> None:
    store = InMemorySourceObjectStore()
    app = _publisher_app(store)
    http = TestClient(app)
    content = workbook_bytes(tmp_path / "a.xlsx", [source_row()])
    first = _upload(http, content, "a.xlsx", client_id=CLIENT_ID)
    second = _upload(http, content, "a.xlsx", client_id=CLIENT_ID)
    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["replayed"] is True
    assert second.json()["source_file_id"] == first.json()["source_file_id"]
    assert store.object_count() == 1


def test_missing_object_on_replay_is_backfilled(tmp_path: Path) -> None:
    store = InMemorySourceObjectStore()
    app = _publisher_app(store)
    http = TestClient(app)
    content = workbook_bytes(tmp_path / "a.xlsx", [source_row()])
    first = _upload(http, content, "a.xlsx", client_id=CLIENT_ID)
    source_id = first.json()["source_file_id"]
    source = app.state.ingest_store.get_source_file(source_id)
    assert source is not None
    store._objects.clear()
    source.storage_uri = None
    second = _upload(http, content, "a.xlsx", client_id=CLIENT_ID)
    assert second.status_code == 200
    assert store.object_count() == 1
    refreshed = app.state.ingest_store.get_source_file(source_id)
    assert refreshed is not None
    assert refreshed.storage_uri is not None
    assert store.get(client_id=CLIENT_ID, source_file_id=source_id, sha256=source.sha256) == content


def test_different_sha_creates_new_source_object(tmp_path: Path) -> None:
    store = InMemorySourceObjectStore()
    app = _publisher_app(store)
    http = TestClient(app)
    first_bytes = workbook_bytes(tmp_path / "a.xlsx", [source_row(Sent=10)])
    second_bytes = workbook_bytes(tmp_path / "b.xlsx", [source_row(Sent=20)])
    first = _upload(http, first_bytes, "a.xlsx", client_id=CLIENT_ID)
    second = _upload(http, second_bytes, "b.xlsx", client_id=CLIENT_ID)
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["source_file_id"] != second.json()["source_file_id"]
    assert store.object_count() == 2


def test_force_keeps_source_recoverable(tmp_path: Path) -> None:
    store = InMemorySourceObjectStore()
    app = _publisher_app(store)
    http = TestClient(app)
    content = workbook_bytes(tmp_path / "a.xlsx", [source_row()])
    first = _upload(http, content, "a.xlsx", client_id=CLIENT_ID)
    forced = _upload(http, content, "a.xlsx", client_id=CLIENT_ID, force=True)
    assert first.status_code == 201
    assert forced.status_code == 201
    assert forced.json()["replayed"] is False
    source = app.state.ingest_store.get_source_file(first.json()["source_file_id"])
    assert source is not None
    assert store.get(client_id=CLIENT_ID, source_file_id=source.id, sha256=source.sha256) == content
    assert store.object_count() == 1


def test_processing_failure_does_not_delete_archive(tmp_path: Path, monkeypatch) -> None:
    store = InMemorySourceObjectStore()

    def boom(*_args, **_kwargs):
        raise RuntimeError("forced transform failure")

    monkeypatch.setattr("dfip_api.upload_service.run_transformation", boom)
    app = _publisher_app(store)
    content = workbook_bytes(tmp_path / "a.xlsx", [source_row()])
    response = _upload(TestClient(app), content, "a.xlsx", client_id=CLIENT_ID)
    assert response.status_code == 201
    source = app.state.ingest_store.get_source_file(response.json()["source_file_id"])
    assert source is not None
    assert source.storage_uri is not None
    assert store.get(client_id=CLIENT_ID, source_file_id=source.id, sha256=source.sha256) == content


def test_storage_failure_does_not_set_storage_uri(tmp_path: Path) -> None:
    store = FailingSourceStore()
    app = _publisher_app(store)
    content = workbook_bytes(tmp_path / "a.xlsx", [source_row()])
    response = TestClient(app).post(
        "/api/v1/uploads",
        headers=AUTH,
        files={
            "file": (
                "a.xlsx",
                content,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
        data={"client_id": CLIENT_ID},
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "PERSISTENCE_UNAVAILABLE"
    assert "storage_uri" not in response.text
    assert store.object_count() == 0
    files = list(app.state.ingest_store.source_files_by_id.values())
    assert all(item.storage_uri is None for item in files)


def test_client_b_cannot_read_client_a_object(tmp_path: Path) -> None:
    store = InMemorySourceObjectStore()
    app = _publisher_app(store)
    content = workbook_bytes(tmp_path / "a.xlsx", [source_row()])
    response = _upload(TestClient(app), content, "a.xlsx", client_id=CLIENT_ID)
    source = app.state.ingest_store.get_source_file(response.json()["source_file_id"])
    assert source is not None
    with pytest.raises(AuthorizationError):
        store.get(client_id=CLIENT_B, source_file_id=source.id, sha256=source.sha256)
    token = _encode_jwt(role="publisher", client_id=CLIENT_B)
    other = create_app(
        settings=make_settings(dfip_auth_mode="jwt", dfip_auth_secret=JWT_SECRET),
        ingest_store=app.state.ingest_store,
        fact_store=app.state.fact_store,
        source_store=store,
    )
    listed = TestClient(other).get(
        "/api/v1/source-files",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert listed.status_code == 200
    assert listed.json()["items"] == []


def test_filesystem_store_is_idempotent_and_private(tmp_path: Path) -> None:
    store = FilesystemSourceObjectStore(tmp_path / "objects")
    client_id = CLIENT_ID
    source_id = str(uuid4())
    payload = b"PK" + b"xlsx-bytes"
    digest = hashlib.sha256(payload).hexdigest()
    uri = store.put(
        client_id=client_id, source_file_id=source_id, sha256=digest, payload=payload
    )
    again = store.put(
        client_id=client_id, source_file_id=source_id, sha256=digest, payload=payload
    )
    assert uri == again
    assert store.object_count() == 1
    assert store.get(client_id=client_id, source_file_id=source_id, sha256=digest) == payload
    with pytest.raises(AuthorizationError):
        store.get(client_id=CLIENT_B, source_file_id=source_id, sha256=digest)
    key = source_object_key(client_id, source_id, digest)
    assert (tmp_path / "objects" / "dfip-source-files" / Path(key)).is_file()


def test_build_source_store_uses_memory_when_endpoint_empty() -> None:
    settings = make_settings()
    store = build_source_object_store(settings)
    assert isinstance(store, InMemorySourceObjectStore)


def test_build_source_store_uses_filesystem_when_endpoint_is_path(tmp_path: Path) -> None:
    settings = make_settings(dfip_storage_endpoint=str(tmp_path / "bucket-root"))
    store = build_source_object_store(settings)
    assert isinstance(store, FilesystemSourceObjectStore)
