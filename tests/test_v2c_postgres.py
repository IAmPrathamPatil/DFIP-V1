"""V2-C Logic/Labels persistence on a scratch PostgreSQL database.

Never uses live DATABASE_URL. Skips unless DFIP_TEST_DATABASE_URL is set.
"""

from __future__ import annotations

from pathlib import Path

from dfip_api.app import create_app
from fastapi.testclient import TestClient

from catalog_support import (
    V2C_CAMPAIGN,
    V2C_FL1,
    V2C_FL2,
    V2C_GROUP,
    labels_xlsx,
    logic_xlsx,
)
from http_ingest_support import source_row, workbook_bytes
from postgres_support import CLIENT_A, postgres_only, requires_postgres, seed_identity
from test_p5_api import JWT_SECRET, _encode_jwt, make_settings
from test_v2_http_ingest import XLSX_TYPE, _upload

pytestmark = [postgres_only, requires_postgres]


def _headers(role: str, client_id: str, sub: str = "v2c-publisher") -> dict[str, str]:
    return {"Authorization": f"Bearer {_encode_jwt(role=role, client_id=client_id, sub=sub)}"}


def test_postgres_catalog_upload_activates_and_binds_processing(
    pg_conn, postgres_url, tmp_path: Path
) -> None:
    seed_identity(pg_conn, subject="v2c-publisher", role="publisher", client_id=CLIENT_A)
    headers = _headers("publisher", CLIENT_A)
    with TestClient(
        create_app(
            settings=make_settings(
                dfip_auth_mode="jwt",
                dfip_auth_secret=JWT_SECRET,
                database_url=postgres_url,
                dfip_env="test",
            )
        )
    ) as http:
        logic = http.post(
            "/api/v1/catalogs/logic",
            headers=headers,
            files={"file": ("logic.xlsx", logic_xlsx(), XLSX_TYPE)},
        )
        assert logic.status_code == 201, logic.text
        logic_id = logic.json()["version"]["version_id"]
        assert (
            http.post(f"/api/v1/catalogs/logic/{logic_id}/activate", headers=headers).status_code
            == 200
        )
        labels = http.post(
            "/api/v1/catalogs/labels",
            headers=headers,
            files={"file": ("labels.xlsx", labels_xlsx(), XLSX_TYPE)},
        )
        assert labels.status_code == 201, labels.text
        labels_id = labels.json()["version"]["version_id"]
        assert (
            http.post(f"/api/v1/catalogs/labels/{labels_id}/activate", headers=headers).status_code
            == 200
        )
        content = workbook_bytes(
            tmp_path / "v2c-pg.xlsx",
            [
                source_row(
                    **{
                        "Campaign ID": "v2c-pg-1",
                        "Campaign Name": V2C_CAMPAIGN,
                        "Unique Clicks": 4,
                    }
                )
            ],
        )
        uploaded = _upload(http, content, "v2c-pg.xlsx", headers=headers)
        assert uploaded.status_code == 201, uploaded.text
        run = uploaded.json()["processing_run"]
        assert run["campaign_label_version_id"] == logic_id
        assert run["label_group_version_id"] == labels_id
        fact = next(
            item
            for item in http.get("/api/v1/facts?limit=200", headers=headers).json()["items"]
            if item["campaign_id"] == "v2c-pg-1"
        )
        assert fact["filter_logic_1"] == V2C_FL1
        assert fact["filter_logic_2"] == V2C_FL2
        assert fact["filter_logic_1_group"] == V2C_GROUP
        assert fact["campaign_label_version_id"] == logic_id

    logic_row = pg_conn.execute(
        """
        SELECT status, notes FROM campaign_label_version WHERE id = %s
        """,
        (logic_id,),
    ).fetchone()
    label_row = pg_conn.execute(
        """
        SELECT status, notes FROM label_group_version WHERE id = %s
        """,
        (labels_id,),
    ).fetchone()
    members = pg_conn.execute(
        """
        SELECT group_name, filter_logic_1_value FROM label_group_member
        WHERE version_id = %s
        """,
        (labels_id,),
    ).fetchall()
    assert logic_row["status"] == "active"
    assert logic_row["notes"] == "v2c-publisher-upload"
    assert label_row["status"] == "active"
    assert label_row["notes"] == "v2c-publisher-upload"
    assert len(members) == 1
    assert members[0]["group_name"] == V2C_GROUP
    assert members[0]["filter_logic_1_value"] == V2C_FL1
