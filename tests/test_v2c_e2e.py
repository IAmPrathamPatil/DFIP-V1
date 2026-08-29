"""V2-C operational E2E: catalog upload → activate → ingest → process → publish.

In-memory stores only. Does not open PostgreSQL or print credentials.
"""

from __future__ import annotations

import csv
import io
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
    V2C_FL2,
    V2C_FL3,
    V2C_FL4,
    V2C_FL5,
    V2C_GROUP,
    V2C_MANUAL,
    catalog_xlsx,
    labels_xlsx,
    logic_xlsx,
)
from http_ingest_support import source_row, workbook_bytes
from test_p5_api import JWT_SECRET, _encode_jwt, make_settings
from test_v2_http_ingest import _upload

CLIENT_A = "a0000000-0000-4000-8000-000000000001"
CLIENT_B = "a0000000-0000-4000-8000-000000000002"
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


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
            catalog_store=InMemoryCatalogStore(),
        )
    )


def _post_catalog(
    http: TestClient, kind: str, content: bytes, filename: str, headers: dict[str, str]
):
    return http.post(
        f"/api/v1/catalogs/{kind}",
        headers=headers,
        files={"file": (filename, content, XLSX_TYPE)},
    )


def test_v2c_publisher_catalog_ingest_publish_export_and_isolation(tmp_path: Path) -> None:
    http = _app()
    publisher_a = _headers("publisher", CLIENT_A)
    publisher_b = _headers("publisher", CLIENT_B)

    invalid = _post_catalog(
        http,
        "logic",
        catalog_xlsx(("Nope",), [{"Nope": "x"}]),
        "bad.xlsx",
        publisher_a,
    )
    assert invalid.status_code == 422
    listed = http.get("/api/v1/catalogs/logic", headers=publisher_a).json()
    assert listed["items"] == []
    assert listed["processing_active"]["origin"] == "packaged"

    logic = _post_catalog(http, "logic", logic_xlsx(), "logic.xlsx", publisher_a)
    assert logic.status_code == 201
    assert logic.json()["version"]["status"] == "draft"
    logic_id = logic.json()["version"]["version_id"]
    activated_logic = http.post(f"/api/v1/catalogs/logic/{logic_id}/activate", headers=publisher_a)
    assert activated_logic.status_code == 200
    assert activated_logic.json()["status"] == "active"

    labels = _post_catalog(http, "labels", labels_xlsx(), "labels.xlsx", publisher_a)
    assert labels.status_code == 201
    labels_id = labels.json()["version"]["version_id"]
    activated_labels = http.post(
        f"/api/v1/catalogs/labels/{labels_id}/activate", headers=publisher_a
    )
    assert activated_labels.status_code == 200

    previous_logic = _post_catalog(
        http,
        "logic",
        logic_xlsx(),
        "logic-v2.xlsx",
        publisher_a,
    )
    previous_id = previous_logic.json()["version"]["version_id"]
    http.post(f"/api/v1/catalogs/logic/{previous_id}/activate", headers=publisher_a)
    versions = http.get("/api/v1/catalogs/logic", headers=publisher_a).json()
    statuses = {item["version_id"]: item["status"] for item in versions["items"]}
    assert statuses[logic_id] == "superseded"
    assert statuses[previous_id] == "active"
    assert versions["processing_active"]["version_id"] == previous_id

    content = workbook_bytes(
        tmp_path / "ops.xlsx",
        [
            source_row(
                **{
                    "Campaign ID": "v2c-e2e-1",
                    "Campaign Name": V2C_CAMPAIGN,
                    "Unique Clicks": 9,
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
    assert run["campaign_label_version_id"] == previous_id
    assert run["label_group_version_id"] == labels_id
    run_id = run["processing_run_id"]

    working = http.get("/api/v1/facts?limit=200", headers=publisher_a).json()
    fact = next(item for item in working["items"] if item["campaign_id"] == "v2c-e2e-1")
    assert fact["filter_logic_1"] == V2C_FL1
    assert fact["filter_logic_2"] == V2C_FL2
    assert fact["amc_status_filter_logic_3"] == V2C_FL3
    assert fact["amc_device_category_filter_logic_4"] == V2C_FL4
    assert fact["amc_product_cat_filter_logic_5"] == V2C_FL5
    assert fact["manual_or_automated"] == V2C_MANUAL
    assert fact["filter_logic_1_group"] == V2C_GROUP
    assert fact["label_match_status"] == "matched"
    assert fact["unique_clicks"] == 9
    assert fact["campaign_label_version_id"] == previous_id
    assert fact["label_group_version_id"] == labels_id

    published = http.post(
        "/api/v1/publications",
        headers=publisher_a,
        json={"client_id": CLIENT_A, "processing_run_id": run_id, "notes": "v2c"},
    )
    assert published.status_code == 201
    csv_body = http.get("/api/v1/publications/current/facts.csv", headers=publisher_a)
    xlsx_body = http.get("/api/v1/publications/current/facts.xlsx", headers=publisher_a)
    assert csv_body.status_code == 200
    assert xlsx_body.status_code == 200
    rows = list(csv.DictReader(io.StringIO(csv_body.content.decode("utf-8"))))
    match = next(row for row in rows if row["campaign_id"] == "v2c-e2e-1")
    assert match["filter_logic_1"] == V2C_FL1
    assert match["filter_logic_2"] == V2C_FL2
    assert match["filter_logic_1_group"] == V2C_GROUP
    assert match["unique_clicks"] == "9"

    restated = workbook_bytes(
        tmp_path / "ops-v2.xlsx",
        [
            source_row(
                **{
                    "Campaign ID": "v2c-e2e-1",
                    "Campaign Name": V2C_CAMPAIGN,
                    "Unique Clicks": 19,
                }
            )
        ],
    )
    restated_upload = _upload(http, restated, "ops-v2.xlsx", headers=publisher_a)
    assert restated_upload.status_code == 201
    assert restated_upload.json()["published"] is False
    run_v2 = restated_upload.json()["processing_run"]["processing_run_id"]
    assert run_v2 != run_id
    working_v2 = http.get("/api/v1/facts?limit=200", headers=publisher_a).json()
    restated_fact = next(item for item in working_v2["items"] if item["campaign_id"] == "v2c-e2e-1")
    assert restated_fact["unique_clicks"] == 19
    assert restated_fact["filter_logic_1"] == V2C_FL1
    assert restated_fact["filter_logic_1_group"] == V2C_GROUP
    assert restated_fact["campaign_label_version_id"] == previous_id
    published_after = http.get("/api/v1/publications/current/facts", headers=publisher_a).json()
    published_items = published_after.get("items") or []
    assert all(item.get("unique_clicks") != 19 for item in published_items)
    republished = http.post(
        "/api/v1/publications",
        headers=publisher_a,
        json={"client_id": CLIENT_A, "processing_run_id": run_v2, "notes": "v2c-restated"},
    )
    assert republished.status_code == 201
    csv_bytes = http.get("/api/v1/publications/current/facts.csv", headers=publisher_a)
    latest_csv = list(csv.DictReader(io.StringIO(csv_bytes.content.decode("utf-8"))))
    latest = next(row for row in latest_csv if row["campaign_id"] == "v2c-e2e-1")
    assert latest["unique_clicks"] == "19"
    assert latest["filter_logic_1"] == V2C_FL1
    assert latest["filter_logic_1_group"] == V2C_GROUP

    leak = http.get(f"/api/v1/catalogs/logic/{previous_id}", headers=publisher_b)
    assert leak.status_code == 404
    leak_download = http.get(f"/api/v1/catalogs/logic/{previous_id}/download", headers=publisher_b)
    assert leak_download.status_code == 404
    b_list = http.get("/api/v1/catalogs/logic", headers=publisher_b).json()
    assert all(item["version_id"] != previous_id for item in b_list["items"])
    assert b_list["processing_active"]["origin"] == "packaged"

    still_invalid = _post_catalog(
        http,
        "labels",
        catalog_xlsx(("Group Name",), [{"Group Name": "G"}]),
        "bad-labels.xlsx",
        publisher_a,
    )
    assert still_invalid.status_code == 422
    labels_list = http.get("/api/v1/catalogs/labels", headers=publisher_a).json()
    assert labels_list["processing_active"]["version_id"] == labels_id
    assert labels_list["processing_active"]["status"] == "active"
