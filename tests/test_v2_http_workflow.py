"""End-to-end HTTP ingest → transform → publish → rpt_* → download on PostgreSQL."""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import jwt
from dfip_analytics.aggregate import sum_additive_measures
from dfip_analytics.kpis import compute_kpis
from dfip_api.app import create_app
from fastapi.testclient import TestClient
from psycopg import connect
from psycopg.rows import dict_row

from http_ingest_support import source_row, upload_workbook, workbook_bytes
from postgres_support import (
    CLIENT_A,
    CLIENT_B,
    postgres_only,
    requires_postgres,
    seed_identity,
)
from test_p5_api import JWT_SECRET, make_settings
from test_v2_http_ingest import XLSX_TYPE, _error
from test_v2_phase2a_reporting import _as_api

pytestmark = [postgres_only, requires_postgres]


def _jwt(*, role: str, sub: str, client_id: str | None = None) -> dict[str, str]:
    payload = {
        "sub": sub,
        "exp": datetime.now(tz=UTC) + timedelta(minutes=10),
        "role": role,
    }
    if client_id is not None:
        payload["client_id"] = client_id
    token = jwt.encode(payload, JWT_SECRET, algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


def _app(postgres_url: str) -> TestClient:
    app = create_app(
        settings=make_settings(
            dfip_auth_mode="jwt",
            dfip_auth_secret=JWT_SECRET,
            database_url=postgres_url,
            dfip_env="test",
        )
    )
    return TestClient(app)


def _upload(client: TestClient, content: bytes, filename: str, headers: dict[str, str], **form):
    return upload_workbook(client, content, filename, headers=headers, **form)


def _csv_rows(response) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(response.content.decode("utf-8"))))


def test_http_upload_publish_download_isolation_and_republish(
    pg_conn, postgres_url, tmp_path: Path
) -> None:
    seed_identity(pg_conn, subject="publisher-a", role="publisher", client_id=CLIENT_A)
    seed_identity(pg_conn, subject="publisher-b", role="publisher", client_id=CLIENT_B)
    seed_identity(pg_conn, subject="reader-a", role="reader", client_id=CLIENT_A)
    seed_identity(pg_conn, subject="reader-b", role="reader", client_id=CLIENT_B)

    publisher_a = _jwt(role="publisher", sub="publisher-a", client_id=CLIENT_A)
    publisher_b = _jwt(role="publisher", sub="publisher-b", client_id=CLIENT_B)
    reader_a = _jwt(role="reader", sub="reader-a", client_id=CLIENT_A)
    reader_b = _jwt(role="reader", sub="reader-b", client_id=CLIENT_B)

    with _app(postgres_url) as http:
        assert (
            http.post("/api/v1/uploads", files={"file": ("a.xlsx", b"PK", XLSX_TYPE)}).status_code
            == 401
        )

        # 1-6. Client A workbook through HTTP upload → ingest → transform → facts.
        content_a = workbook_bytes(tmp_path / "client-a.xlsx", [source_row()])
        uploaded_a = _upload(http, content_a, "client-a.xlsx", publisher_a)
        assert uploaded_a.status_code == 201
        body_a = uploaded_a.json()
        assert body_a["client_id"] == CLIENT_A
        assert body_a["published"] is False
        assert body_a["processing_run"]["status"] == "succeeded"
        assert body_a["transform"]["transformed"] == 1
        run_a1 = body_a["processing_run"]["processing_run_id"]
        working = http.get("/api/v1/facts?limit=200", headers=publisher_a).json()
        assert working["pagination"]["total"] == 1
        assert working["items"][0]["unique_clicks"] == 2
        assert working["items"][0]["total_cost"] == "15.0000"

        # Authz: Client A publisher cannot upload as Client B.
        wrong = _upload(http, content_a, "client-a.xlsx", publisher_a, client_id=CLIENT_B)
        assert wrong.status_code == 403

        # Unpublished data is absent from published facts and downloads.
        unpublished = http.get("/api/v1/publications/current/facts", headers=reader_a).json()
        assert unpublished["items"] == []
        empty_csv = http.get("/api/v1/publications/current/facts.csv", headers=reader_a)
        assert empty_csv.status_code == 200
        assert _csv_rows(empty_csv) == []

        # 7. Publish the succeeded run.
        published = http.post(
            "/api/v1/publications",
            headers=publisher_a,
            json={"client_id": CLIENT_A, "processing_run_id": run_a1},
        )
        assert published.status_code == 201

        # Client B cannot publish Client A's run.
        cross_pub = http.post(
            "/api/v1/publications",
            headers=publisher_b,
            json={"client_id": CLIENT_A, "processing_run_id": run_a1},
        )
        assert cross_pub.status_code == 403

        # 8-9. Query published facts and rpt_*.
        facts_a = http.get(
            "/api/v1/publications/current/facts",
            headers=reader_a,
            params={"limit": 200},
        ).json()
        assert facts_a["pagination"]["total"] == 1
        assert facts_a["items"][0]["unique_clicks"] == 2

        with connect(postgres_url, row_factory=dict_row) as conn:
            _as_api(conn, role="reader", client_ids=CLIENT_A)
            rpt_a = conn.execute(
                """
                SELECT campaign_id, unique_clicks, delivered, total_cost
                FROM rpt_published_fact
                ORDER BY campaign_id
                """
            ).fetchall()
            working_as_reader = conn.execute(
                "SELECT COUNT(*) AS n FROM fact_campaign_day"
            ).fetchone()
            conn.execute("ROLLBACK")
        assert len(rpt_a) == 1
        assert rpt_a[0]["campaign_id"] == "camp-a"
        assert int(rpt_a[0]["unique_clicks"]) == 2
        assert working_as_reader is not None
        assert int(working_as_reader["n"]) == 0

        # 10. KPI values from the published slice.
        records, _total = http.app.state.fact_store.list_published_slice(
            client_id=CLIENT_A,
            processing_run_id=run_a1,
            period_start=None,
            period_end=None,
            limit=200,
            offset=0,
        )
        kpis = compute_kpis(sum_additive_measures(records), namespace="qa")
        assert kpis["ctr"] == Decimal("0.020000")
        assert records[0].total_cost == Decimal("15.0000")

        # 11-12. Download matches published facts.
        csv_a = http.get("/api/v1/publications/current/facts.csv", headers=reader_a)
        rows_a = _csv_rows(csv_a)
        assert len(rows_a) == 1
        assert rows_a[0]["campaign_id"] == facts_a["items"][0]["campaign_id"]
        assert rows_a[0]["unique_clicks"] == str(facts_a["items"][0]["unique_clicks"])
        assert rows_a[0]["total_cost"] == facts_a["items"][0]["total_cost"]

        # 13-15. Client B data is isolated both ways.
        content_b = workbook_bytes(
            tmp_path / "client-b.xlsx",
            [source_row(**{"Campaign ID": "camp-b", "Unique Clicks": 5, "Delivered": 50})],
        )
        uploaded_b = _upload(http, content_b, "client-b.xlsx", publisher_b)
        assert uploaded_b.status_code == 201
        run_b = uploaded_b.json()["processing_run"]["processing_run_id"]
        assert (
            http.post(
                "/api/v1/publications",
                headers=publisher_b,
                json={"client_id": CLIENT_B, "processing_run_id": run_b},
            ).status_code
            == 201
        )
        facts_b = http.get("/api/v1/publications/current/facts", headers=reader_b).json()
        assert facts_b["items"][0]["campaign_id"] == "camp-b"
        a_sees_b = http.get(
            "/api/v1/publications/current/facts",
            headers=reader_a,
            params={"client_id": CLIENT_B},
        )
        b_sees_a = http.get(
            "/api/v1/publications/current/facts",
            headers=reader_b,
            params={"client_id": CLIENT_A},
        )
        assert a_sees_b.status_code == 403
        assert b_sees_a.status_code == 403
        assert (
            _csv_rows(http.get("/api/v1/publications/current/facts.csv", headers=reader_a))[0][
                "campaign_id"
            ]
            == "camp-a"
        )
        assert (
            _csv_rows(http.get("/api/v1/publications/current/facts.csv", headers=reader_b))[0][
                "campaign_id"
            ]
            == "camp-b"
        )
        a_download_b = http.get(
            "/api/v1/publications/current/facts.csv",
            headers=reader_a,
            params={"client_id": CLIENT_B},
        )
        assert a_download_b.status_code == 403

        with connect(postgres_url, row_factory=dict_row) as conn:
            _as_api(conn, role="reader", client_ids=CLIENT_A)
            only_a = conn.execute("SELECT campaign_id FROM rpt_published_fact").fetchall()
            conn.execute("ROLLBACK")
        assert [row["campaign_id"] for row in only_a] == ["camp-a"]

        # 16-17. Malformed workbook is rejected safely.
        malformed = http.post(
            "/api/v1/uploads",
            headers=publisher_a,
            files={"file": ("bad.xlsx", b"PK\x03\x04not-zip", XLSX_TYPE)},
        )
        assert malformed.status_code == 422
        assert _error(malformed)["code"] == "VALIDATION_ERROR"

        # 18-19. Invalid numeric keeps INVALID_NUMERIC.
        numeric = workbook_bytes(
            tmp_path / "invalid-numeric.xlsx",
            [source_row(**{"Campaign ID": "camp-numeric", "Sent": "twelve"})],
        )
        numeric_upload = _upload(http, numeric, "invalid-numeric.xlsx", publisher_a)
        assert numeric_upload.status_code == 201
        assert any(
            item["reason_code"] == "INVALID_NUMERIC" for item in numeric_upload.json()["rejections"]
        )

        # 20. Duplicate grain.
        duplicate = workbook_bytes(
            tmp_path / "dup.xlsx",
            [
                source_row(**{"Campaign ID": "camp-dup"}),
                source_row(**{"Campaign ID": "camp-dup", "Sent": 80}),
            ],
        )
        dup_upload = _upload(http, duplicate, "dup.xlsx", publisher_a)
        assert dup_upload.status_code == 201
        assert any(
            item["reason_code"] == "DUPLICATE_GRAIN_KEY" for item in dup_upload.json()["rejections"]
        )

        # 21. A later unpublished restatement does not appear in the download.
        restated = workbook_bytes(
            tmp_path / "client-a-v2.xlsx",
            [source_row(**{"Unique Clicks": 20})],
        )
        restated_upload = _upload(http, restated, "client-a-v2.xlsx", publisher_a)
        assert restated_upload.status_code == 201
        run_a2 = restated_upload.json()["processing_run"]["processing_run_id"]
        assert run_a2 != run_a1
        still_first = http.get("/api/v1/publications/current/facts", headers=reader_a).json()
        # Restating the live grain vacates it from the published pointer until republish.
        assert still_first["items"] == [] or still_first["items"][0]["unique_clicks"] == 2
        unpublished_csv = _csv_rows(
            http.get("/api/v1/publications/current/facts.csv", headers=reader_a)
        )
        assert unpublished_csv == [] or unpublished_csv[0]["unique_clicks"] == "2"
        assert all(row.get("unique_clicks") != "20" for row in unpublished_csv)

        # 22-23. Republish moves the pointer; old slice is no longer exposed.
        republished = http.post(
            "/api/v1/publications",
            headers=publisher_a,
            json={"client_id": CLIENT_A, "processing_run_id": run_a2},
        )
        assert republished.status_code == 201
        latest = http.get("/api/v1/publications/current/facts", headers=reader_a).json()
        assert latest["items"][0]["unique_clicks"] == 20
        assert latest["items"][0]["processing_run_id"] == run_a2
        latest_csv = _csv_rows(http.get("/api/v1/publications/current/facts.csv", headers=reader_a))
        assert latest_csv[0]["unique_clicks"] == "20"
        kpis_after = compute_kpis(
            sum_additive_measures(
                http.app.state.fact_store.list_published_slice(
                    client_id=CLIENT_A,
                    processing_run_id=run_a2,
                    period_start=None,
                    period_end=None,
                    limit=200,
                    offset=0,
                )[0]
            ),
            namespace="qa",
        )
        assert kpis_after["ctr"] == Decimal("0.200000")

        with connect(postgres_url, row_factory=dict_row) as conn:
            _as_api(conn, role="reader", client_ids=CLIENT_A)
            rpt_latest = conn.execute(
                "SELECT unique_clicks, processing_run_id::text AS run_id FROM rpt_published_fact"
            ).fetchall()
            conn.execute("ROLLBACK")
        assert len(rpt_latest) == 1
        assert int(rpt_latest[0]["unique_clicks"]) == 20
        assert rpt_latest[0]["run_id"] == run_a2
