"""Existing-batch reprocess: new run, current catalogs, no Raw re-upload.

In-memory stores only. Does not open PostgreSQL or print credentials.
"""

from __future__ import annotations

from pathlib import Path

from dfip_api.app import create_app
from dfip_api.publication_store import InMemoryPublicationStore
from dfip_config.catalog import InMemoryCatalogStore
from dfip_core.ingest.store import InMemoryIngestStore
from dfip_core.transform.store import InMemoryFactStore
from fastapi.testclient import TestClient

from catalog_support import (
    V2C_CAMPAIGN,
    V2C_FL1,
    labels_xlsx,
    logic_xlsx,
)
from http_ingest_support import (
    complete_reprocess_response,
    source_row,
    upload_workbook,
    workbook_bytes,
)
from test_p5_api import JWT_SECRET, _encode_jwt, make_settings
from test_v2_http_ingest import _error
from test_v2c_catalog import CAMPAIGN_V2, CLIENT_A, CLIENT_B, XLSX_TYPE

PACKAGED_LABELS = "a0000000-0000-4000-8000-000000000041"


def _headers(role: str, client_id: str | None) -> dict[str, str]:
    claims = {"role": role}
    if client_id is not None:
        claims["client_id"] = client_id
    return {"Authorization": f"Bearer {_encode_jwt(**claims)}"}


def _unscoped(role: str = "publisher") -> dict[str, str]:
    return _headers(role, None)


def _app() -> TestClient:
    return TestClient(
        create_app(
            settings=make_settings(
                dfip_auth_mode="jwt",
                dfip_auth_secret=JWT_SECRET,
                database_url="",
            ),
            ingest_store=InMemoryIngestStore(),
            fact_store=InMemoryFactStore(),
            publication_store=InMemoryPublicationStore(),
            catalog_store=InMemoryCatalogStore(),
        )
    )


def _post_catalog(http: TestClient, kind: str, content: bytes, filename: str, headers):
    return http.post(
        f"/api/v1/catalogs/{kind}",
        headers=headers,
        files={"file": (filename, content, XLSX_TYPE)},
    )


def _activate_catalogs(http: TestClient, headers) -> tuple[str, str]:
    logic_id = _post_catalog(http, "logic", logic_xlsx(), "logic.xlsx", headers).json()[
        "version"
    ]["version_id"]
    labels_id = _post_catalog(http, "labels", labels_xlsx(), "labels.xlsx", headers).json()[
        "version"
    ]["version_id"]
    assert (
        http.post(
            f"/api/v1/catalogs/logic/{logic_id}/activate",
            headers=headers,
            params=_client_params(headers),
        ).status_code
        == 200
    )
    assert (
        http.post(
            f"/api/v1/catalogs/labels/{labels_id}/activate",
            headers=headers,
            params=_client_params(headers),
        ).status_code
        == 200
    )
    return logic_id, labels_id


def _client_params(headers: dict[str, str]) -> dict[str, str]:
    return {}


def _upload_raw(http: TestClient, tmp_path: Path, headers, *, name: str, campaign_id: str):
    content = workbook_bytes(
        tmp_path / name,
        [
            source_row(
                **{
                    "Campaign ID": campaign_id,
                    "Campaign Name": V2C_CAMPAIGN,
                    "Delivered": 10,
                    "Unique Clicks": 2,
                }
            )
        ],
    )
    return upload_workbook(http, content, name, headers=headers)


def _reprocess(http: TestClient, batch_id: str, headers, **params):
    query = dict(params)
    response = http.post(
        f"/api/v1/batches/{batch_id}/process",
        headers=headers,
        params=query or None,
    )
    return complete_reprocess_response(http, response, headers=headers)


def test_reprocess_creates_new_run_with_current_catalogs(tmp_path: Path) -> None:
    http = _app()
    headers = _headers("publisher", CLIENT_A)
    uploaded = _upload_raw(http, tmp_path, headers, name="raw-a.xlsx", campaign_id="rep-1")
    assert uploaded.status_code in {200, 201}
    old_run = uploaded.json()["processing_run"]
    old_run_id = old_run["processing_run_id"]
    batch_id = uploaded.json()["batch"]["batch_id"]
    assert old_run["campaign_label_version_id"] == CAMPAIGN_V2
    assert old_run["label_group_version_id"] == PACKAGED_LABELS
    old_qa = http.get(
        f"/api/v1/processing-runs/{old_run_id}/qa-findings", headers=headers
    ).json()
    logic_id, labels_id = _activate_catalogs(http, headers)

    result = _reprocess(http, batch_id, headers)
    assert result.status_code == 200
    body = result.json()
    assert body["published"] is False
    new_run = body["processing_run"]
    new_run_id = new_run["processing_run_id"]
    assert new_run_id != old_run_id
    assert new_run["batch_id"] == batch_id
    assert new_run["status"] == "succeeded"
    assert new_run["campaign_label_version_id"] == logic_id
    assert new_run["label_group_version_id"] == labels_id
    assert body["logic_version_label"]
    assert body["labels_version_label"]

    frozen = http.get(f"/api/v1/processing-runs/{old_run_id}", headers=headers).json()
    assert frozen["status"] == "succeeded"
    assert frozen["campaign_label_version_id"] == CAMPAIGN_V2
    assert frozen["label_group_version_id"] == PACKAGED_LABELS
    assert frozen["started_at"] == old_run["started_at"]
    assert frozen["finished_at"] == old_run["finished_at"]

    after_qa = http.get(
        f"/api/v1/processing-runs/{old_run_id}/qa-findings", headers=headers
    ).json()
    assert after_qa["items"] == old_qa["items"]
    new_qa = http.get(
        f"/api/v1/processing-runs/{new_run_id}/qa-findings", headers=headers
    ).json()
    assert all(item["processing_run_id"] == new_run_id for item in new_qa["items"])

    facts = http.get(
        "/api/v1/facts",
        headers=headers,
        params={"batch_id": batch_id, "limit": 50},
    ).json()
    assert facts["pagination"]["total"] == 1
    fact = facts["items"][0]
    assert fact["processing_run_id"] == new_run_id
    assert fact["campaign_label_version_id"] == logic_id
    assert fact["label_group_version_id"] == labels_id
    assert fact["filter_logic_1"] == V2C_FL1
    assert fact["delivered"] == 10
    assert fact["unique_clicks"] == 2
    history = http.get(
        "/api/v1/facts/history",
        headers=headers,
        params={"batch_id": batch_id, "limit": 50},
    ).json()
    assert history["pagination"]["total"] >= 1
    assert all(item["processing_run_id"] == old_run_id for item in history["items"])

    current = http.get("/api/v1/publications/current", headers=headers)
    assert current.status_code == 200
    pointer = current.json().get("publication")
    assert pointer is None or pointer["processing_run_id"] != new_run_id


def test_reprocess_can_run_twice_with_distinct_ids(tmp_path: Path) -> None:
    http = _app()
    headers = _headers("publisher", CLIENT_A)
    uploaded = _upload_raw(http, tmp_path, headers, name="raw-b.xlsx", campaign_id="rep-2")
    batch_id = uploaded.json()["batch"]["batch_id"]
    _activate_catalogs(http, headers)
    first = _reprocess(http, batch_id, headers).json()["processing_run"]["processing_run_id"]
    second = _reprocess(http, batch_id, headers).json()["processing_run"]["processing_run_id"]
    assert first != second
    listed = http.get(
        "/api/v1/processing-runs",
        headers=headers,
        params={"batch_id": batch_id, "limit": 20},
    ).json()
    ids = [item["processing_run_id"] for item in listed["items"]]
    assert uploaded.json()["processing_run"]["processing_run_id"] in ids
    assert first in ids
    assert second in ids
    facts = http.app.state.fact_store.list_current()
    assert len(facts) == 1
    assert facts[0].processing_run_id == second


def test_reprocess_missing_client_id_unscoped(tmp_path: Path) -> None:
    http = _app()
    bound = _headers("publisher", CLIENT_A)
    uploaded = _upload_raw(http, tmp_path, bound, name="raw-c.xlsx", campaign_id="rep-3")
    batch_id = uploaded.json()["batch"]["batch_id"]
    missing = http.post(f"/api/v1/batches/{batch_id}/process", headers=_unscoped())
    assert missing.status_code == 422
    assert "client_id is required" in _error(missing)["message"]
    assert uploaded.json()["processing_run"]["status"] == "succeeded"


def test_reprocess_wrong_client_and_non_publisher(tmp_path: Path) -> None:
    http = _app()
    publisher_a = _headers("publisher", CLIENT_A)
    publisher_b = _headers("publisher", CLIENT_B)
    reader = _headers("reader", CLIENT_A)
    uploaded = _upload_raw(http, tmp_path, publisher_a, name="raw-d.xlsx", campaign_id="rep-4")
    batch_id = uploaded.json()["batch"]["batch_id"]
    mismatch = http.post(
        f"/api/v1/batches/{batch_id}/process",
        headers=publisher_a,
        params={"client_id": CLIENT_B},
    )
    assert mismatch.status_code == 403
    assert _error(mismatch)["code"] == "AUTHORIZATION_FAILED"
    other = http.post(f"/api/v1/batches/{batch_id}/process", headers=publisher_b)
    assert other.status_code == 404
    forbidden = http.post(f"/api/v1/batches/{batch_id}/process", headers=reader)
    assert forbidden.status_code == 403
    unscoped = http.post(
        f"/api/v1/batches/{batch_id}/process",
        headers=_unscoped(),
        params={"client_id": CLIENT_B},
    )
    assert unscoped.status_code == 403


def test_reprocess_does_not_change_publication_pointer(tmp_path: Path) -> None:
    http = _app()
    headers = _headers("publisher", CLIENT_A)
    published_upload = _upload_raw(
        http, tmp_path, headers, name="pub.xlsx", campaign_id="pub-1"
    )
    pub_run = published_upload.json()["processing_run"]["processing_run_id"]
    created = http.post(
        "/api/v1/publications",
        headers=headers,
        json={"client_id": CLIENT_A, "processing_run_id": pub_run},
    )
    assert created.status_code == 201
    target = _upload_raw(http, tmp_path, headers, name="rep.xlsx", campaign_id="rep-5")
    batch_id = target.json()["batch"]["batch_id"]
    _activate_catalogs(http, headers)
    _reprocess(http, batch_id, headers)
    current = http.get("/api/v1/publications/current", headers=headers).json()
    assert current["publication"]["processing_run_id"] == pub_run
    published = http.get(
        "/api/v1/publications/current/facts",
        headers=headers,
        params={"limit": 50},
    ).json()
    assert published["pagination"]["total"] >= 1
    assert all(item["processing_run_id"] == pub_run for item in published["items"])
    assert all(item["campaign_id"] == "pub-1" for item in published["items"])


def test_failed_reprocess_does_not_publish(tmp_path: Path, monkeypatch) -> None:
    http = _app()
    headers = _headers("publisher", CLIENT_A)
    uploaded = _upload_raw(http, tmp_path, headers, name="fail.xlsx", campaign_id="rep-6")
    batch_id = uploaded.json()["batch"]["batch_id"]
    old_run_id = uploaded.json()["processing_run"]["processing_run_id"]

    def _boom(*_args, **_kwargs):
        raise RuntimeError("forced transform failure")

    monkeypatch.setattr("dfip_api.upload_service.run_transformation", _boom)
    response = http.post(f"/api/v1/batches/{batch_id}/process", headers=headers)
    body = complete_reprocess_response(http, response, headers=headers).json()
    new_run_id = body["processing_run"]["processing_run_id"]
    assert new_run_id != old_run_id
    assert body["processing_run"]["status"] == "failed"
    assert body["processing_run"]["error_summary"]
    assert body["processing_run"]["finished_at"] is not None
    assert body["published"] is False
    current = http.get("/api/v1/publications/current", headers=headers)
    payload = current.json()
    assert payload.get("publication") is None
    frozen = http.get(f"/api/v1/processing-runs/{old_run_id}", headers=headers).json()
    assert frozen["status"] == "succeeded"
    facts = http.get(
        "/api/v1/facts",
        headers=headers,
        params={"processing_run_id": old_run_id, "limit": 50},
    ).json()
    assert facts["pagination"]["total"] == 1


def test_unscoped_reprocess_with_query_client_id(tmp_path: Path) -> None:
    http = _app()
    bound = _headers("publisher", CLIENT_A)
    uploaded = _upload_raw(http, tmp_path, bound, name="raw-u.xlsx", campaign_id="rep-7")
    batch_id = uploaded.json()["batch"]["batch_id"]
    _activate_catalogs(http, bound)
    unscoped = _unscoped()
    result = _reprocess(http, batch_id, unscoped, client_id=CLIENT_A)
    assert result.status_code == 200
    assert result.json()["processing_run"]["status"] == "succeeded"
    assert result.json()["published"] is False


def test_spa_reprocess_wiring() -> None:
    root = Path(__file__).resolve().parents[1] / "apps" / "web" / "static" / "js"
    client_js = (root / "api-client.js").read_text(encoding="utf-8")
    app_js = (root / "app.js").read_text(encoding="utf-8")
    views = (root / "views.js").read_text(encoding="utf-8")
    assert "processBatch" in client_js
    assert "/batches/" in client_js
    assert "/process" in client_js
    assert "processBatch" in app_js
    assert "data-batch-reprocess" in views
    assert "Re-process" in views
