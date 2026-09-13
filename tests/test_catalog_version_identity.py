"""RUN 004B-3: stored catalog version IDs must belong to the run's client_id.

Packaged JSON fallback must not masquerade as a different tenant's catalog
version. Disposable PostgreSQL only — never a live URI.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

from dfip_api.app import create_app
from dfip_api.local_demo_seed import seed_company2_identities, seed_demo_identities
from dfip_config.catalog_identity import (
    packaged_catalog_owner_client_id,
    packaged_catalog_version_ids,
    persistable_catalog_version_id,
    strip_foreign_packaged_catalog_version_id,
)
from dfip_core.ingest.pipeline import bind_versions_for_day, ingest_workbook
from dfip_core.ingest.store import BatchRecord, InMemoryIngestStore
from dfip_core.transform.cost import persistable_rate_card_rule_id
from dfip_core.transform.engine import run_transformation
from dfip_core.transform.labels import bind_configuration, bind_configuration_for_run
from dfip_core.transform.store import InMemoryFactStore
from dfip_db.local_demo_guard import (
    COMPANY_2_CLIENT_ID,
    DEFAULT_CLIENT_ID,
    DEMO_CLIENT_2_SUBJECT,
    DEMO_PUBLISHER_2_SUBJECT,
    DEMO_PUBLISHER_SUBJECT,
)
from fastapi.testclient import TestClient

from catalog_support import V2C_CAMPAIGN, V2C_FL1, labels_xlsx, logic_xlsx
from http_ingest_support import source_row, upload_workbook, workbook_bytes
from postgres_support import CLIENT_B, postgres_only, requires_postgres
from test_company2_isolation import (
    CLIENT2_PASSWORD,
    PUBLISHER2_PASSWORD,
    PUBLISHER_PASSWORD,
    _bearer,
    _token,
)
from test_company_registry import _select
from test_p4_transform import GROUP7_CAMPAIGN, GROUP7_FILTER_LOGIC_1
from test_p5_api import JWT_SECRET, make_settings
from test_v2_http_ingest import XLSX_TYPE
from test_v2c_catalog import CAMPAIGN_V2, LABEL_GROUP, _app, _headers, _post_catalog, _upload

TEMPLATE_V4 = "a0000000-0000-4000-8000-000000000034"
RATE_V2 = "a0000000-0000-4000-8000-000000000012"
RATE_V2_SMS = "a0000000-0000-4000-8000-000000000122"

_VERSION_TABLES = (
    ("campaign_label_version_id", "campaign_label_version"),
    ("template_label_version_id", "template_label_version"),
    ("rate_card_version_id", "rate_card_version"),
    ("label_group_version_id", "label_group_version"),
)


def _version_payload(run: dict) -> dict[str, str | None]:
    return {
        "campaign_label_version_id": run.get("campaign_label_version_id"),
        "template_label_version_id": run.get("template_label_version_id"),
        "rate_card_version_id": run.get("rate_card_version_id"),
        "label_group_version_id": run.get("label_group_version_id"),
    }


def test_persistable_catalog_version_id_keeps_default_packaged_ids() -> None:
    assert persistable_catalog_version_id(DEFAULT_CLIENT_ID, CAMPAIGN_V2) == CAMPAIGN_V2
    assert persistable_catalog_version_id(DEFAULT_CLIENT_ID, LABEL_GROUP) == LABEL_GROUP
    assert persistable_catalog_version_id(None, CAMPAIGN_V2) == CAMPAIGN_V2


def test_persistable_catalog_version_id_nulls_foreign_packaged_and_mismatch() -> None:
    assert persistable_catalog_version_id(COMPANY_2_CLIENT_ID, CAMPAIGN_V2) is None
    assert persistable_catalog_version_id(COMPANY_2_CLIENT_ID, TEMPLATE_V4) is None
    assert persistable_catalog_version_id(COMPANY_2_CLIENT_ID, RATE_V2) is None
    assert persistable_catalog_version_id(COMPANY_2_CLIENT_ID, LABEL_GROUP) is None
    assert (
        persistable_catalog_version_id(
            COMPANY_2_CLIENT_ID,
            CAMPAIGN_V2,
            owner_client_id=DEFAULT_CLIENT_ID,
        )
        is None
    )
    overlay = str(uuid4())
    assert persistable_catalog_version_id(COMPANY_2_CLIENT_ID, overlay) is None
    assert (
        persistable_catalog_version_id(
            COMPANY_2_CLIENT_ID,
            overlay,
            owner_client_id=COMPANY_2_CLIENT_ID,
        )
        == overlay
    )
    assert strip_foreign_packaged_catalog_version_id(COMPANY_2_CLIENT_ID, CAMPAIGN_V2) is None
    assert strip_foreign_packaged_catalog_version_id(DEFAULT_CLIENT_ID, CAMPAIGN_V2) == CAMPAIGN_V2
    assert persistable_rate_card_rule_id(DEFAULT_CLIENT_ID, RATE_V2_SMS) == RATE_V2_SMS
    assert persistable_rate_card_rule_id(COMPANY_2_CLIENT_ID, RATE_V2_SMS) is None
    assert persistable_rate_card_rule_id(None, RATE_V2_SMS) == RATE_V2_SMS


def test_packaged_fallback_bind_does_not_store_foreign_uuids() -> None:
    day = date(2025, 8, 1)
    default_bind = bind_versions_for_day(day, client_id=DEFAULT_CLIENT_ID)
    other_bind = bind_versions_for_day(day, client_id=COMPANY_2_CLIENT_ID)
    assert default_bind.campaign_label_version_id == CAMPAIGN_V2
    assert default_bind.template_label_version_id == TEMPLATE_V4
    assert default_bind.rate_card_version_id == RATE_V2
    assert default_bind.label_group_version_id == LABEL_GROUP
    assert other_bind.campaign_version_label == "campaign-v2"
    assert other_bind.template_version_label == "template-v4"
    assert other_bind.rate_card_version_label == "rate-v2"
    assert other_bind.campaign_label_version_id is None
    assert other_bind.template_label_version_id is None
    assert other_bind.rate_card_version_id is None
    assert other_bind.label_group_version_id is None

    default_cfg = bind_configuration(day, client_id=DEFAULT_CLIENT_ID)
    other_cfg = bind_configuration(day, client_id=COMPANY_2_CLIENT_ID)
    assert default_cfg.campaign_label_version_id == CAMPAIGN_V2
    assert other_cfg.campaign_version_label == "campaign-v2"
    assert other_cfg.campaign(GROUP7_CAMPAIGN).filter_logic_1 == GROUP7_FILTER_LOGIC_1
    assert other_cfg.campaign_label_version_id is None
    assert other_cfg.template_label_version_id is None
    assert other_cfg.rate_card_version_id is None
    assert other_cfg.label_group_version_id is None

    mismatched = bind_configuration_for_run(
        day,
        campaign_label_version_id=CAMPAIGN_V2,
        template_label_version_id=TEMPLATE_V4,
        rate_card_version_id=RATE_V2,
        label_group_version_id=LABEL_GROUP,
        client_id=COMPANY_2_CLIENT_ID,
    )
    assert mismatched.campaign_version_label == "campaign-v2"
    assert mismatched.campaign_label_version_id is None
    assert mismatched.template_label_version_id is None
    assert mismatched.rate_card_version_id is None
    assert mismatched.label_group_version_id is None


def test_memory_default_and_company2_ingest_version_identity(tmp_path: Path) -> None:
    default_store = InMemoryIngestStore()
    default_facts = InMemoryFactStore()
    other_store = InMemoryIngestStore()
    other_facts = InMemoryFactStore()
    default_path = tmp_path / "default.xlsx"
    other_path = tmp_path / "other.xlsx"
    workbook_bytes(
        default_path,
        [source_row(**{"Campaign ID": "camp-default"})],
    )
    workbook_bytes(
        other_path,
        [source_row(**{"Campaign ID": "camp-other"})],
    )
    default_result = ingest_workbook(default_path, default_store, client_id=DEFAULT_CLIENT_ID)
    other_result = ingest_workbook(other_path, other_store, client_id=COMPANY_2_CLIENT_ID)
    assert default_result.processing_run is not None
    assert other_result.processing_run is not None
    assert default_result.processing_run.campaign_label_version_id == CAMPAIGN_V2
    assert default_result.processing_run.template_label_version_id == TEMPLATE_V4
    assert default_result.processing_run.rate_card_version_id == RATE_V2
    assert default_result.processing_run.label_group_version_id == LABEL_GROUP
    assert other_result.processing_run.campaign_label_version_id is None
    assert other_result.processing_run.template_label_version_id is None
    assert other_result.processing_run.rate_card_version_id is None
    assert other_result.processing_run.label_group_version_id is None

    default_tx = run_transformation(
        default_store,
        default_facts,
        default_result.batch.id,
        processing_run_id=default_result.processing_run.id,
    )
    other_tx = run_transformation(
        other_store,
        other_facts,
        other_result.batch.id,
        processing_run_id=other_result.processing_run.id,
    )
    assert default_tx.succeeded
    assert other_tx.succeeded
    default_fact = next(
        item for item in default_facts.facts.values() if item.campaign_id == "camp-default"
    )
    other_fact = next(
        item for item in other_facts.facts.values() if item.campaign_id == "camp-other"
    )
    assert default_fact.filter_logic_1 == GROUP7_FILTER_LOGIC_1
    assert other_fact.filter_logic_1 == GROUP7_FILTER_LOGIC_1
    assert default_fact.campaign_label_version_id == CAMPAIGN_V2
    assert default_fact.rate_card_rule_id == RATE_V2_SMS
    assert default_fact.total_cost is not None
    assert other_fact.campaign_label_version_id is None
    assert other_fact.template_label_version_id is None
    assert other_fact.rate_card_version_id is None
    assert other_fact.label_group_version_id is None
    assert other_fact.rate_card_rule_id is None
    assert other_fact.total_cost == default_fact.total_cost


def test_memory_company2_logic_and_labels_overlay_win(tmp_path: Path) -> None:
    http, catalog = _app()
    headers = _headers("publisher", COMPANY_2_CLIENT_ID)
    logic_id = _post_catalog(http, "logic", logic_xlsx(), "logic.xlsx", headers).json()["version"][
        "version_id"
    ]
    assert (
        http.post(f"/api/v1/catalogs/logic/{logic_id}/activate", headers=headers).status_code == 200
    )
    logic_upload = _upload(
        http,
        workbook_bytes(
            tmp_path / "logic-overlay.xlsx",
            [source_row(**{"Campaign ID": "logic-2", "Campaign Name": V2C_CAMPAIGN})],
        ),
        "logic-overlay.xlsx",
        headers=headers,
    )
    assert logic_upload.status_code == 201, logic_upload.text
    logic_run = logic_upload.json()["processing_run"]
    assert logic_run["campaign_label_version_id"] == logic_id
    assert logic_run["label_group_version_id"] is None
    logic_fact = next(
        item
        for item in http.get("/api/v1/facts?limit=200", headers=headers).json()["items"]
        if item["campaign_id"] == "logic-2"
    )
    assert logic_fact["filter_logic_1"] == V2C_FL1
    assert logic_fact["campaign_label_version_id"] == logic_id
    assert logic_fact["rate_card_rule_id"] is None
    assert catalog.active_for(COMPANY_2_CLIENT_ID, "logic").id == logic_id
    assert (
        http.post(f"/api/v1/catalogs/logic/{logic_id}/deactivate", headers=headers).status_code
        == 200
    )

    labels_id = _post_catalog(
        http,
        "labels",
        labels_xlsx([{"Group Name": "Company2-Group", "Filter Logic 1": GROUP7_FILTER_LOGIC_1}]),
        "labels.xlsx",
        headers,
    ).json()["version"]["version_id"]
    assert (
        http.post(f"/api/v1/catalogs/labels/{labels_id}/activate", headers=headers).status_code
        == 200
    )
    labels_upload = _upload(
        http,
        workbook_bytes(
            tmp_path / "labels-overlay.xlsx",
            [source_row(**{"Campaign ID": "labels-2", "Campaign Name": GROUP7_CAMPAIGN})],
        ),
        "labels-overlay.xlsx",
        headers=headers,
    )
    assert labels_upload.status_code == 201, labels_upload.text
    labels_run = labels_upload.json()["processing_run"]
    assert labels_run["label_group_version_id"] == labels_id
    assert labels_run["campaign_label_version_id"] is None
    labels_fact = next(
        item
        for item in http.get("/api/v1/facts?limit=200", headers=headers).json()["items"]
        if item["campaign_id"] == "labels-2"
    )
    assert labels_fact["filter_logic_1"] == GROUP7_FILTER_LOGIC_1
    assert labels_fact["filter_logic_1_group"] == "Company2-Group"
    assert labels_fact["label_group_version_id"] == labels_id
    assert labels_fact["rate_card_rule_id"] is None


def test_memory_mismatch_packaged_id_is_not_persisted() -> None:
    store = InMemoryIngestStore()
    file_id = str(uuid4())
    batch_id = str(uuid4())
    now = datetime.now(tz=UTC)
    store.batches[batch_id] = BatchRecord(
        id=batch_id,
        source_file_id=file_id,
        client_id=COMPANY_2_CLIENT_ID,
        status="staged",
        row_count_declared=1,
        row_count_staged=1,
        row_count_rejected=0,
        observed_day_min=date(2025, 8, 1),
        observed_day_max=date(2025, 8, 1),
        created_at=now,
        completed_at=None,
        error_summary=None,
        worksheet_name="Web-Engage Raw",
        header_row=1,
        source_start_column="K",
        empty_row_count=0,
    )
    run = store.add_processing_run(
        batch_id=batch_id,
        campaign_label_version_id=CAMPAIGN_V2,
        template_label_version_id=TEMPLATE_V4,
        rate_card_version_id=RATE_V2,
        label_group_version_id=LABEL_GROUP,
        engine_version="0.4.0",
    )
    assert run.campaign_label_version_id is None
    assert run.template_label_version_id is None
    assert run.rate_card_version_id is None
    assert run.label_group_version_id is None
    run.campaign_label_version_id = CAMPAIGN_V2
    store.save_processing_run(run)
    stored = store.get_processing_run(run.id)
    assert stored is not None
    assert stored.campaign_label_version_id is None


def _seed_two_tenants(pg_conn) -> None:
    seed_demo_identities(
        pg_conn,
        publisher_password=PUBLISHER_PASSWORD,
        client_password="local-client-pass",
        iterations=1000,
    )
    seed_company2_identities(
        pg_conn,
        publisher_password=PUBLISHER2_PASSWORD,
        client_password="local-client2-pass",
        iterations=1000,
    )
    pg_conn.commit()


def _assert_sql_version_ownership(conn, run_id: str, client_id: str) -> None:
    row = conn.execute(
        """
        SELECT
            client_id::text AS client_id,
            campaign_label_version_id::text AS campaign_label_version_id,
            template_label_version_id::text AS template_label_version_id,
            rate_card_version_id::text AS rate_card_version_id,
            label_group_version_id::text AS label_group_version_id
        FROM processing_run
        WHERE id = %s
        """,
        (run_id,),
    ).fetchone()
    assert row["client_id"] == client_id
    packaged = packaged_catalog_version_ids()
    owner = packaged_catalog_owner_client_id()
    for column, table in _VERSION_TABLES:
        version_id = row[column]
        if version_id is None:
            continue
        parent = conn.execute(
            f"SELECT client_id::text AS client_id FROM {table} WHERE id = %s",
            (version_id,),
        ).fetchone()
        assert parent is not None, column
        assert parent["client_id"] == client_id, column
        if version_id in packaged:
            assert client_id == owner, column
    facts = conn.execute(
        """
        SELECT
            campaign_label_version_id::text AS campaign_label_version_id,
            template_label_version_id::text AS template_label_version_id,
            rate_card_version_id::text AS rate_card_version_id,
            label_group_version_id::text AS label_group_version_id,
            rate_card_rule_id::text AS rate_card_rule_id
        FROM fact_campaign_day
        WHERE processing_run_id = %s
        """,
        (run_id,),
    ).fetchall()
    for fact in facts:
        for column, table in _VERSION_TABLES:
            version_id = fact[column]
            if version_id is None:
                continue
            parent = conn.execute(
                f"SELECT client_id::text AS client_id FROM {table} WHERE id = %s",
                (version_id,),
            ).fetchone()
            assert parent is not None
            assert parent["client_id"] == client_id
        rule_id = fact["rate_card_rule_id"]
        if rule_id is None:
            continue
        owner = conn.execute(
            """
            SELECT v.client_id::text AS client_id
            FROM rate_card_rule AS r
            JOIN rate_card_version AS v ON v.id = r.version_id
            WHERE r.id = %s
            """,
            (rule_id,),
        ).fetchone()
        assert owner is not None
        assert owner["client_id"] == client_id


def _pg_app(postgres_url: str):
    return create_app(
        settings=make_settings(
            dfip_auth_mode="jwt",
            dfip_auth_secret=JWT_SECRET,
            database_url=postgres_url,
            dfip_password_pbkdf2_iterations=1000,
            dfip_env="test",
        )
    )


@requires_postgres
@postgres_only
def test_postgres_default_client_keeps_owned_packaged_ids(
    pg_conn, postgres_url: str, tmp_path: Path
) -> None:
    _seed_two_tenants(pg_conn)
    with TestClient(_pg_app(postgres_url)) as http:
        headers = _bearer(
            _token(http, DEMO_PUBLISHER_SUBJECT, PUBLISHER_PASSWORD, DEFAULT_CLIENT_ID)
        )
        uploaded = upload_workbook(
            http,
            workbook_bytes(
                tmp_path / "default.xlsx",
                [source_row(**{"Campaign ID": "camp-default-pg"})],
            ),
            "default.xlsx",
            headers=headers,
            client_id=DEFAULT_CLIENT_ID,
        )
        assert uploaded.status_code in {200, 201}, uploaded.text
        run = uploaded.json()["processing_run"]
        versions = _version_payload(run)
        assert versions["campaign_label_version_id"] == CAMPAIGN_V2
        assert versions["template_label_version_id"] == TEMPLATE_V4
        assert versions["rate_card_version_id"] == RATE_V2
        assert versions["label_group_version_id"] == LABEL_GROUP
        facts = http.get("/api/v1/facts?limit=200", headers=headers).json()["items"]
        fact = next(item for item in facts if item["campaign_id"] == "camp-default-pg")
        assert fact["filter_logic_1"] == GROUP7_FILTER_LOGIC_1
        assert fact["campaign_label_version_id"] == CAMPAIGN_V2
        assert fact["rate_card_rule_id"] == RATE_V2_SMS
        assert fact["total_cost"] is not None
        _assert_sql_version_ownership(pg_conn, run["processing_run_id"], DEFAULT_CLIENT_ID)


@requires_postgres
@postgres_only
def test_postgres_company2_packaged_fallback_nulls_foreign_ids(
    pg_conn, postgres_url: str, tmp_path: Path
) -> None:
    _seed_two_tenants(pg_conn)
    with TestClient(_pg_app(postgres_url)) as http:
        headers = _bearer(_token(http, DEMO_PUBLISHER_2_SUBJECT, PUBLISHER2_PASSWORD))
        uploaded = upload_workbook(
            http,
            workbook_bytes(
                tmp_path / "c2.xlsx",
                [source_row(**{"Campaign ID": "camp-c2-pg"})],
            ),
            "c2.xlsx",
            headers=headers,
            client_id=COMPANY_2_CLIENT_ID,
        )
        assert uploaded.status_code in {200, 201}, uploaded.text
        run = uploaded.json()["processing_run"]
        versions = _version_payload(run)
        assert versions == {
            "campaign_label_version_id": None,
            "template_label_version_id": None,
            "rate_card_version_id": None,
            "label_group_version_id": None,
        }
        facts = http.get("/api/v1/facts?limit=200", headers=headers).json()["items"]
        fact = next(item for item in facts if item["campaign_id"] == "camp-c2-pg")
        assert fact["filter_logic_1"] == GROUP7_FILTER_LOGIC_1
        assert fact["campaign_label_version_id"] is None
        assert fact["template_label_version_id"] is None
        assert fact["rate_card_version_id"] is None
        assert fact["label_group_version_id"] is None
        assert fact["rate_card_rule_id"] is None
        assert fact["total_cost"] is not None
        _assert_sql_version_ownership(pg_conn, run["processing_run_id"], COMPANY_2_CLIENT_ID)


@requires_postgres
@postgres_only
def test_postgres_new_tenant_packaged_fallback_nulls_foreign_ids(
    pg_conn, postgres_url: str, tmp_path: Path
) -> None:
    _seed_two_tenants(pg_conn)
    with TestClient(_pg_app(postgres_url)) as http:
        creator = _bearer(
            _token(http, DEMO_PUBLISHER_SUBJECT, PUBLISHER_PASSWORD, DEFAULT_CLIENT_ID)
        )
        created = http.post(
            "/api/v1/clients",
            headers=creator,
            json={"name": "DFIP-004B3 synthetic tenant"},
        )
        assert created.status_code == 201, created.text
        new_id = created.json()["client_id"]
        selected = _select(http, creator["Authorization"].split(" ", 1)[1], new_id)
        headers = _bearer(selected.json()["access_token"])
        uploaded = upload_workbook(
            http,
            workbook_bytes(
                tmp_path / "new.xlsx",
                [source_row(**{"Campaign ID": "camp-new-pg"})],
            ),
            "new.xlsx",
            headers=headers,
            client_id=new_id,
        )
        assert uploaded.status_code in {200, 201}, uploaded.text
        run = uploaded.json()["processing_run"]
        assert _version_payload(run) == {
            "campaign_label_version_id": None,
            "template_label_version_id": None,
            "rate_card_version_id": None,
            "label_group_version_id": None,
        }
        facts = http.get("/api/v1/facts?limit=200", headers=headers).json()["items"]
        fact = next(item for item in facts if item["campaign_id"] == "camp-new-pg")
        assert fact["filter_logic_1"] == GROUP7_FILTER_LOGIC_1
        assert fact["campaign_label_version_id"] is None
        assert fact["rate_card_rule_id"] is None
        assert fact["total_cost"] is not None
        assert run.get("qa_verdict") in {"pass", "warn"}
        created_pub = http.post(
            "/api/v1/publications",
            headers=headers,
            json={"client_id": new_id, "processing_run_id": run["processing_run_id"]},
        )
        assert created_pub.status_code == 201, created_pub.text
        published = http.get("/api/v1/publications/current/facts", headers=headers)
        assert published.status_code == 200, published.text
        assert {item["campaign_id"] for item in published.json()["items"]} == {"camp-new-pg"}
        other_client = _bearer(_token(http, DEMO_CLIENT_2_SUBJECT, CLIENT2_PASSWORD))
        steal = http.get("/api/v1/publications/current/facts", headers=other_client)
        assert steal.status_code in {200, 404}
        if steal.status_code == 200:
            assert "camp-new-pg" not in {
                item["campaign_id"] for item in steal.json()["items"] or []
            }
        _assert_sql_version_ownership(pg_conn, run["processing_run_id"], new_id)


@requires_postgres
@postgres_only
def test_postgres_company2_logic_overlay_wins(pg_conn, postgres_url: str, tmp_path: Path) -> None:
    _seed_two_tenants(pg_conn)
    with TestClient(_pg_app(postgres_url)) as http:
        headers = _bearer(_token(http, DEMO_PUBLISHER_2_SUBJECT, PUBLISHER2_PASSWORD))
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
        uploaded = upload_workbook(
            http,
            workbook_bytes(
                tmp_path / "logic.xlsx",
                [source_row(**{"Campaign ID": "c2-logic", "Campaign Name": V2C_CAMPAIGN})],
            ),
            "logic.xlsx",
            headers=headers,
            client_id=COMPANY_2_CLIENT_ID,
        )
        assert uploaded.status_code in {200, 201}, uploaded.text
        run = uploaded.json()["processing_run"]
        assert run["campaign_label_version_id"] == logic_id
        assert run["template_label_version_id"] is None
        assert run["rate_card_version_id"] is None
        fact = next(
            item
            for item in http.get("/api/v1/facts?limit=200", headers=headers).json()["items"]
            if item["campaign_id"] == "c2-logic"
        )
        assert fact["filter_logic_1"] == V2C_FL1
        assert fact["campaign_label_version_id"] == logic_id
        assert fact["rate_card_rule_id"] is None
        _assert_sql_version_ownership(pg_conn, run["processing_run_id"], COMPANY_2_CLIENT_ID)


@requires_postgres
@postgres_only
def test_postgres_company2_labels_overlay_wins(pg_conn, postgres_url: str, tmp_path: Path) -> None:
    _seed_two_tenants(pg_conn)
    with TestClient(_pg_app(postgres_url)) as http:
        headers = _bearer(_token(http, DEMO_PUBLISHER_2_SUBJECT, PUBLISHER2_PASSWORD))
        labels = http.post(
            "/api/v1/catalogs/labels",
            headers=headers,
            files={
                "file": (
                    "labels.xlsx",
                    labels_xlsx(
                        [
                            {
                                "Group Name": "Company2-Group",
                                "Filter Logic 1": GROUP7_FILTER_LOGIC_1,
                            }
                        ]
                    ),
                    XLSX_TYPE,
                )
            },
        )
        assert labels.status_code == 201, labels.text
        labels_id = labels.json()["version"]["version_id"]
        assert (
            http.post(f"/api/v1/catalogs/labels/{labels_id}/activate", headers=headers).status_code
            == 200
        )
        uploaded = upload_workbook(
            http,
            workbook_bytes(
                tmp_path / "labels.xlsx",
                [source_row(**{"Campaign ID": "c2-labels", "Campaign Name": GROUP7_CAMPAIGN})],
            ),
            "labels.xlsx",
            headers=headers,
            client_id=COMPANY_2_CLIENT_ID,
        )
        assert uploaded.status_code in {200, 201}, uploaded.text
        run = uploaded.json()["processing_run"]
        assert run["label_group_version_id"] == labels_id
        assert run["campaign_label_version_id"] is None
        fact = next(
            item
            for item in http.get("/api/v1/facts?limit=200", headers=headers).json()["items"]
            if item["campaign_id"] == "c2-labels"
        )
        assert fact["filter_logic_1"] == GROUP7_FILTER_LOGIC_1
        assert fact["filter_logic_1_group"] == "Company2-Group"
        assert fact["label_group_version_id"] == labels_id
        assert fact["rate_card_rule_id"] is None
        _assert_sql_version_ownership(pg_conn, run["processing_run_id"], COMPANY_2_CLIENT_ID)


@requires_postgres
@postgres_only
def test_postgres_mismatch_version_id_is_not_persisted(pg_conn, pg_stores) -> None:
    ingest = pg_stores[0]
    file_id = str(uuid4())
    batch_id = str(uuid4())
    now = datetime(2025, 8, 1, tzinfo=UTC)
    pg_conn.execute(
        """
        INSERT INTO source_file (
            id, client_id, sha256, original_filename, byte_size, source_kind, uploaded_at
        )
        VALUES (%s, %s, %s, 'mismatch.xlsx', 10, 'native_export', %s)
        """,
        (file_id, CLIENT_B, "b" * 64, now),
    )
    pg_conn.execute(
        """
        INSERT INTO batch (
            id, source_file_id, client_id, status, row_count_declared, row_count_staged,
            row_count_rejected, observed_day_min, observed_day_max, created_at, completed_at,
            worksheet_name, header_row, source_start_column, empty_row_count
        )
        VALUES (
            %s, %s, %s, 'staged', 1, 1, 0, %s, %s, %s, NULL,
            'Web-Engage Raw', 1, 'K', 0
        )
        """,
        (batch_id, file_id, CLIENT_B, date(2025, 8, 1), date(2025, 8, 1), now),
    )
    pg_conn.commit()
    run = ingest.add_processing_run(
        batch_id=batch_id,
        campaign_label_version_id=CAMPAIGN_V2,
        template_label_version_id=TEMPLATE_V4,
        rate_card_version_id=RATE_V2,
        label_group_version_id=LABEL_GROUP,
        engine_version="0.4.0",
    )
    assert run.client_id == CLIENT_B
    assert run.campaign_label_version_id is None
    assert run.template_label_version_id is None
    assert run.rate_card_version_id is None
    assert run.label_group_version_id is None
    run.campaign_label_version_id = CAMPAIGN_V2
    run.template_label_version_id = TEMPLATE_V4
    run.rate_card_version_id = RATE_V2
    run.label_group_version_id = LABEL_GROUP
    ingest.save_processing_run(run)
    stored = ingest.get_processing_run(run.id)
    assert stored is not None
    assert stored.campaign_label_version_id is None
    assert stored.template_label_version_id is None
    assert stored.rate_card_version_id is None
    assert stored.label_group_version_id is None
    _assert_sql_version_ownership(pg_conn, run.id, CLIENT_B)
