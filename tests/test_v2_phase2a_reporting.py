"""Phase 2A published reporting views and QA persistence against PostgreSQL 16."""

from __future__ import annotations

import time
from datetime import date
from uuid import uuid4

from dfip_analytics.qa import QaContext, QaFinding, evaluate_qa
from dfip_core.transform.derive import month_label
from dfip_db.analytics_repository import PostgresAnalyticsRepository
from psycopg import connect
from psycopg.rows import dict_row

from postgres_support import (
    BATCH_A,
    CLIENT_A,
    CLIENT_B,
    RUN_A,
    postgres_only,
    requires_postgres,
    sample_fact,
    seed_working_set,
)

pytestmark = [postgres_only, requires_postgres]


def _as_api(conn, *, role: str, client_ids: str, platform_admin: bool = False):
    conn.execute("BEGIN")
    conn.execute("SET LOCAL ROLE dfip_api")
    conn.execute("SELECT set_config('dfip.role', %s, true)", (role,))
    conn.execute("SELECT set_config('dfip.client_ids', %s, true)", (client_ids,))
    conn.execute(
        "SELECT set_config('dfip.platform_admin', %s, true)",
        ("true" if platform_admin else "false",),
    )
    return conn


def _seed_client_b(conn) -> str:
    file_id = str(uuid4())
    batch_id = str(uuid4())
    run_id = str(uuid4())
    conn.execute(
        """
        INSERT INTO source_file (
            id, client_id, sha256, original_filename, byte_size, source_kind
        )
        VALUES (%s, %s, %s, 'b.xlsx', 1, 'native_export')
        """,
        (file_id, CLIENT_B, "b" * 64),
    )
    conn.execute(
        """
        INSERT INTO batch (
            id, source_file_id, client_id, status, row_count_staged, row_count_rejected,
            empty_row_count
        )
        VALUES (%s, %s, %s, 'processed', 1, 0, 0)
        """,
        (batch_id, file_id, CLIENT_B),
    )
    conn.execute(
        """
        INSERT INTO processing_run (id, batch_id, client_id, status)
        VALUES (%s, %s, %s, 'succeeded')
        """,
        (run_id, batch_id, CLIENT_B),
    )
    conn.commit()
    return run_id


def test_migration_14_objects_exist(postgres_url: str) -> None:
    with connect(postgres_url, row_factory=dict_row) as conn:
        tables = {
            row["relname"]
            for row in conn.execute(
                """
                SELECT relname FROM pg_class
                WHERE relkind = 'r' AND relnamespace = 'public'::regnamespace
                """
            )
        }
        views = {
            row["viewname"]
            for row in conn.execute("SELECT viewname FROM pg_views WHERE schemaname = 'public'")
        }
        forced = conn.execute(
            """
            SELECT relrowsecurity AND relforcerowsecurity AS forced
            FROM pg_class
            WHERE relname = 'qa_finding' AND relnamespace = 'public'::regnamespace
            """
        ).fetchone()
    assert "qa_finding" in tables
    assert "rpt_dim_date" in tables
    assert {
        "rpt_published_fact",
        "rpt_dim_client",
        "rpt_dim_campaign",
        "rpt_dim_variation",
    } <= views
    assert forced is not None
    assert forced["forced"] is True


def test_date_dimension_matches_p4_month_label(postgres_url: str) -> None:
    with connect(postgres_url, row_factory=dict_row) as conn:
        row = conn.execute(
            """
            SELECT month_start, month_label, year, quarter, day_of_week
            FROM rpt_dim_date
            WHERE day = DATE '2025-08-01'
            """
        ).fetchone()
        count = conn.execute("SELECT COUNT(*) AS n FROM rpt_dim_date").fetchone()
    assert row is not None
    assert row["month_label"] == month_label(date(2025, 8, 1))
    assert row["month_start"] == date(2025, 8, 1)
    assert int(row["year"]) == 2025
    assert int(row["quarter"]) == 3
    assert int(row["day_of_week"]) == 5
    assert count is not None
    assert int(count["n"]) == 5844


def test_published_facts_visible_unpublished_and_working_set_hidden(
    pg_conn, pg_stores, postgres_url
) -> None:
    _ingest, facts, pubs, _read = pg_stores
    seed_working_set(pg_conn)
    facts.upsert(sample_fact(sent=10, delivered=8, unique_clicks=2))
    unpublished = sample_fact(
        campaign_id="camp-unpublished",
        day=date(2025, 8, 2),
        processing_run_id=RUN_A,
    )
    facts.upsert(unpublished)
    pubs.create(
        client_id=CLIENT_A,
        processing_run_id=RUN_A,
        period_start=date(2025, 8, 1),
        period_end=date(2025, 8, 1),
        published_by="dev",
        notes=None,
    )
    with connect(postgres_url, row_factory=dict_row) as conn:
        _as_api(conn, role="reader", client_ids=CLIENT_A)
        published = conn.execute(
            "SELECT campaign_id FROM rpt_published_fact ORDER BY campaign_id"
        ).fetchall()
        working = conn.execute("SELECT COUNT(*) AS n FROM fact_campaign_day").fetchone()
        definition = conn.execute(
            """
            SELECT pg_get_viewdef('rpt_published_fact'::regclass, true) AS sql
            """
        ).fetchone()
        conn.execute("ROLLBACK")
    assert [row["campaign_id"] for row in published] == ["camp-1"]
    assert working is not None
    assert int(working["n"]) == 0
    assert definition is not None
    sql = definition["sql"].lower()
    assert " as ctr" not in sql
    assert " as roas" not in sql
    assert " as conversion_rate" not in sql


def test_dimensions_match_published_data_and_preserve_empty_variation(
    pg_conn, pg_stores, postgres_url
) -> None:
    _ingest, facts, pubs, _read = pg_stores
    seed_working_set(pg_conn)
    facts.upsert(
        sample_fact(
            variation_id=None,
            variation_id_key="",
            campaign_name=" leading",
            channel="SMS",
            amc_product_cat_filter_logic_5="RO",
        )
    )
    facts.upsert(
        sample_fact(
            campaign_id="camp-hidden",
            day=date(2025, 8, 2),
            variation_id="hidden",
            variation_id_key="hidden",
        )
    )
    pubs.create(
        client_id=CLIENT_A,
        processing_run_id=RUN_A,
        period_start=date(2025, 8, 1),
        period_end=date(2025, 8, 1),
        published_by="dev",
        notes=None,
    )
    with connect(postgres_url, row_factory=dict_row) as conn:
        _as_api(conn, role="reader", client_ids=CLIENT_A)
        campaigns = conn.execute(
            "SELECT campaign_id, amc_product_cat_filter_logic_5 FROM rpt_dim_campaign"
        ).fetchall()
        variations = conn.execute(
            "SELECT variation_id, variation_id_key FROM rpt_dim_variation"
        ).fetchall()
        clients = conn.execute("SELECT client_id::text AS client_id FROM rpt_dim_client").fetchall()
        conn.execute("ROLLBACK")
    assert [row["campaign_id"] for row in campaigns] == ["camp-1"]
    assert campaigns[0]["amc_product_cat_filter_logic_5"] == "RO"
    assert len(variations) == 1
    assert variations[0]["variation_id"] is None
    assert variations[0]["variation_id_key"] == ""
    assert {row["client_id"] for row in clients} == {CLIENT_A}


def test_qa_findings_persist_and_survive_reconnect(pg_conn, pg_pool, pg_stores) -> None:
    _ingest, facts, _pubs, _read = pg_stores
    seed_working_set(pg_conn)
    facts.upsert(sample_fact(sent=-1, delivered=8))
    stored = facts.get(sample_fact().key)
    assert stored is not None
    findings = evaluate_qa(
        QaContext(
            client_id=CLIENT_A,
            processing_run_id=RUN_A,
            batch_id=stored.batch_id,
            run_status="succeeded",
            facts=[stored],
        )
    )
    repo = PostgresAnalyticsRepository(pg_pool)
    repo.replace_findings(findings)
    listed = repo.list_findings(RUN_A)
    assert any(row["rule_id"] == "QA-NEGATIVE-COUNT" for row in listed)
    again = PostgresAnalyticsRepository(pg_pool).list_findings(RUN_A)
    assert len(again) == len(listed)


def test_reader_cannot_read_qa_findings(pg_conn, pg_pool, pg_stores, postgres_url) -> None:
    seed_working_set(pg_conn)
    repo = PostgresAnalyticsRepository(pg_pool)
    repo.replace_findings(
        [
            QaFinding(
                rule_id="QA-RUN-FAILED",
                severity="error",
                description="processing_run.status is failed",
                entity_type="run",
                entity_key=RUN_A,
                message="failed",
                client_id=CLIENT_A,
                processing_run_id=RUN_A,
            )
        ]
    )
    with connect(postgres_url, row_factory=dict_row) as conn:
        _as_api(conn, role="reader", client_ids=CLIENT_A)
        rows = conn.execute("SELECT COUNT(*) AS n FROM qa_finding").fetchone()
        conn.execute("ROLLBACK")
    assert rows is not None
    assert int(rows["n"]) == 0


def test_25000_qa_findings_persist_without_timeout(pg_conn, pg_pool, pg_stores) -> None:
    seed_working_set(pg_conn)
    repo = PostgresAnalyticsRepository(pg_pool)
    findings = [
        QaFinding(
            rule_id="QA-RATIO-GT-ONE",
            severity="warning",
            description="A count relationship that should be <= 1 is inverted",
            entity_type="fact",
            entity_key=f"camp-{index}|var|2025-08-01:unique_clicks:unique_impressions",
            message="unique_clicks exceeds unique_impressions",
            client_id=CLIENT_A,
            processing_run_id=RUN_A,
            batch_id=BATCH_A,
            diagnostics={"i": index},
        )
        for index in range(25_000)
    ]
    started = time.perf_counter()
    repo.replace_for_run(RUN_A, findings)
    elapsed = time.perf_counter() - started
    listed = repo.list_for_run(RUN_A)
    assert len(listed) == 25_000
    assert elapsed < 120
    keys = {(row["rule_id"], row["entity_key"]) for row in listed}
    assert len(keys) == 25_000
    repo.replace_for_run(RUN_A, findings)
    again = repo.list_for_run(RUN_A)
    assert len(again) == 25_000
    other_run = _seed_client_b(pg_conn)
    other = [
        QaFinding(
            rule_id="QA-IMPOSSIBLE-FAILED",
            severity="warning",
            description="Failed exceeds sent",
            entity_type="fact",
            entity_key="other|var|2025-08-01:failed",
            message="failed exceeds sent",
            client_id=CLIENT_B,
            processing_run_id=other_run,
            diagnostics={"sent": 1, "failed": 2},
        )
    ]
    PostgresAnalyticsRepository(pg_pool).replace_for_run(other_run, other)
    assert len(repo.list_for_run(RUN_A)) == 25_000
    assert len(PostgresAnalyticsRepository(pg_pool).list_for_run(other_run)) == 1
