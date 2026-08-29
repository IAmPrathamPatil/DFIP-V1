"""Publisher operational E2E on in-memory stores (no live database).

Covers the production-style API path: upload → ingest → logic/label bind →
facts → unpublished published-slice → explicit publish → history list →
CSV/XLSX → tenant isolation. Does not drop PostgreSQL and does not call
Supabase.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

from dfip_api.app import create_app
from dfip_api.publication_store import InMemoryPublicationStore
from dfip_core.ingest.store import InMemoryIngestStore
from dfip_core.transform.store import InMemoryFactStore
from fastapi.testclient import TestClient

from http_ingest_support import source_row, workbook_bytes
from test_p4_transform import GROUP7_CAMPAIGN
from test_p5_api import JWT_SECRET, _encode_jwt, make_settings
from test_v2_http_ingest import _error, _upload

CLIENT_A = "a0000000-0000-4000-8000-000000000001"
CLIENT_B = "a0000000-0000-4000-8000-000000000002"
CAMPAIGN_V2 = "a0000000-0000-4000-8000-000000000022"
TEMPLATE_V4 = "a0000000-0000-4000-8000-000000000034"
LABEL_GROUP = "a0000000-0000-4000-8000-000000000041"


def _headers(role: str, client_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {_encode_jwt(role=role, client_id=client_id)}"}


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
        )
    )


def test_publisher_operational_path_binds_logic_publishes_and_isolates(tmp_path: Path) -> None:
    http = _app()
    publisher_a = _headers("publisher", CLIENT_A)
    publisher_b = _headers("publisher", CLIENT_B)
    reader_b = _headers("reader", CLIENT_B)

    content = workbook_bytes(
        tmp_path / "ops.xlsx",
        [
            source_row(
                **{
                    "Campaign ID": "ops-camp-1",
                    "Campaign Name": GROUP7_CAMPAIGN,
                    "Unique Clicks": 7,
                }
            )
        ],
    )
    uploaded = _upload(http, content, "ops.xlsx", headers=publisher_a)
    assert uploaded.status_code == 201
    body = uploaded.json()
    assert body["published"] is False
    run = body["processing_run"]
    assert run["status"] == "succeeded"
    assert run["campaign_label_version_id"] == CAMPAIGN_V2
    assert run["template_label_version_id"] == TEMPLATE_V4
    assert run["label_group_version_id"] == LABEL_GROUP
    run_id = run["processing_run_id"]

    unpublished = http.get("/api/v1/publications/current/facts", headers=publisher_a).json()
    assert unpublished["pagination"]["total"] == 0

    working = http.get("/api/v1/facts?limit=200", headers=publisher_a).json()
    fact = next(item for item in working["items"] if item["campaign_id"] == "ops-camp-1")
    assert fact["unique_clicks"] == 7
    assert fact["filter_logic_1_group"] == "Group7"
    assert fact["campaign_label_version_id"] == CAMPAIGN_V2

    first = http.post(
        "/api/v1/publications",
        headers=publisher_a,
        json={"client_id": CLIENT_A, "processing_run_id": run_id, "notes": "v1"},
    )
    assert first.status_code == 201
    first_id = first.json()["publication"]["publication_id"]

    csv_body = http.get("/api/v1/publications/current/facts.csv", headers=publisher_a)
    xlsx_body = http.get("/api/v1/publications/current/facts.xlsx", headers=publisher_a)
    assert csv_body.status_code == 200
    assert xlsx_body.status_code == 200
    rows = list(csv.DictReader(io.StringIO(csv_body.content.decode("utf-8"))))
    assert any(row["campaign_id"] == "ops-camp-1" for row in rows)

    second = http.post(
        "/api/v1/publications",
        headers=publisher_a,
        json={"client_id": CLIENT_A, "processing_run_id": run_id, "notes": "v2"},
    )
    assert second.status_code == 201
    second_id = second.json()["publication"]["publication_id"]
    history = http.get("/api/v1/publications", headers=publisher_a).json()
    assert history["pagination"]["total"] == 2
    assert [item["publication_id"] for item in history["items"]] == [second_id, first_id]
    current = http.get("/api/v1/publications/current", headers=publisher_a).json()
    assert current["publication"]["publication_id"] == second_id

    leak = http.get("/api/v1/publications/current/facts.csv", headers=reader_b)
    leak_rows = list(csv.DictReader(io.StringIO(leak.content.decode("utf-8"))))
    assert not any(row.get("campaign_id") == "ops-camp-1" for row in leak_rows)
    cross = http.get(
        "/api/v1/publications/current/facts.csv",
        headers=reader_b,
        params={"client_id": CLIENT_A},
    )
    assert cross.status_code == 403
    steal = http.post(
        "/api/v1/publications",
        headers=publisher_b,
        json={"client_id": CLIENT_A, "processing_run_id": run_id},
    )
    assert steal.status_code == 403
    other_history = http.get("/api/v1/publications", headers=publisher_b).json()
    assert other_history["pagination"]["total"] == 0


def test_invalid_source_and_failed_run_are_not_published(tmp_path: Path) -> None:
    http = _app()
    publisher_a = _headers("publisher", CLIENT_A)
    bad = workbook_bytes(
        tmp_path / "bad.xlsx",
        [source_row()],
        header_override={12: "Campaign"},
    )
    uploaded = _upload(http, bad, "bad.xlsx", headers=publisher_a)
    assert uploaded.status_code == 201
    body = uploaded.json()
    assert body["processing_run"] is None
    assert body["batch"]["status"] == "failed"

    numeric = workbook_bytes(tmp_path / "num.xlsx", [source_row(**{"Sent": "twelve"})])
    failed_upload = _upload(http, numeric, "num.xlsx", headers=publisher_a)
    assert failed_upload.status_code == 201
    failed_run = failed_upload.json()["processing_run"]
    assert failed_run["status"] == "failed"
    publish_failed = http.post(
        "/api/v1/publications",
        headers=publisher_a,
        json={
            "client_id": CLIENT_A,
            "processing_run_id": failed_run["processing_run_id"],
        },
    )
    assert publish_failed.status_code == 422
    assert _error(publish_failed)["code"] == "VALIDATION_ERROR"

    missing = http.post(
        "/api/v1/publications",
        headers=publisher_a,
        json={
            "client_id": CLIENT_A,
            "processing_run_id": "d0000000-0000-4000-8000-000000000099",
        },
    )
    assert missing.status_code == 404


def test_repeated_ingest_is_replayed_without_publish(tmp_path: Path) -> None:
    http = _app()
    publisher_a = _headers("publisher", CLIENT_A)
    content = workbook_bytes(tmp_path / "same.xlsx", [source_row()])
    first = _upload(http, content, "same.xlsx", headers=publisher_a)
    second = _upload(http, content, "same.xlsx", headers=publisher_a)
    assert first.status_code == 201
    assert second.status_code in {200, 201}
    assert second.json()["replayed"] is True
    assert second.json()["published"] is False
    unpublished = http.get("/api/v1/publications/current/facts", headers=publisher_a).json()
    assert unpublished["pagination"]["total"] == 0


def test_admin_overview_shows_publication_history_and_logic_versions() -> None:
    root = Path(__file__).resolve().parents[1]
    views = (root / "apps" / "web" / "static" / "js" / "views.js").read_text(encoding="utf-8")
    app_js = (root / "apps" / "web" / "static" / "js" / "app.js").read_text(encoding="utf-8")
    client_js = (root / "apps" / "web" / "static" / "js" / "api-client.js").read_text(
        encoding="utf-8"
    )
    assert "listPublications" in client_js
    assert "listPublications" in app_js
    assert "Publication history" in views
    assert "campaign_label_version_id" in views
    assert "GET /api/v1/publications" in views
