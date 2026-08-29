"""Phase 2A reporting views stay inside the Phase 1 tenant boundary."""

from __future__ import annotations

from uuid import uuid4

from psycopg import connect
from psycopg.rows import dict_row

from postgres_support import (
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
        (file_id, CLIENT_B, "c" * 64),
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


def _publish(pubs, client_id: str, run_id: str) -> None:
    pubs.create(
        client_id=client_id,
        processing_run_id=run_id,
        period_start=None,
        period_end=None,
        published_by="dev",
        notes=None,
    )


def test_client_a_cannot_see_client_b_reporting_rows(pg_conn, pg_stores, postgres_url) -> None:
    _ingest, facts, pubs, _read = pg_stores
    seed_working_set(pg_conn)
    run_b = _seed_client_b(pg_conn)
    facts.upsert(sample_fact())
    facts.upsert(
        sample_fact(
            client_id=CLIENT_B,
            campaign_id="camp-b",
            processing_run_id=run_b,
            batch_id=None,
        )
    )
    _publish(pubs, CLIENT_A, RUN_A)
    _publish(pubs, CLIENT_B, run_b)
    with connect(postgres_url, row_factory=dict_row) as conn:
        _as_api(conn, role="reader", client_ids=CLIENT_A)
        facts_a = conn.execute(
            "SELECT client_id::text AS client_id FROM rpt_published_fact"
        ).fetchall()
        campaigns = conn.execute(
            "SELECT client_id::text AS client_id FROM rpt_dim_campaign"
        ).fetchall()
        conn.execute("ROLLBACK")
    assert {row["client_id"] for row in facts_a} == {CLIENT_A}
    assert {row["client_id"] for row in campaigns} == {CLIENT_A}


def test_platform_admin_can_see_both_clients_in_reporting(pg_conn, pg_stores, postgres_url) -> None:
    _ingest, facts, pubs, _read = pg_stores
    seed_working_set(pg_conn)
    run_b = _seed_client_b(pg_conn)
    facts.upsert(sample_fact())
    facts.upsert(
        sample_fact(
            client_id=CLIENT_B,
            campaign_id="camp-b",
            processing_run_id=run_b,
            batch_id=None,
        )
    )
    _publish(pubs, CLIENT_A, RUN_A)
    _publish(pubs, CLIENT_B, run_b)
    with connect(postgres_url, row_factory=dict_row) as conn:
        _as_api(conn, role="admin", client_ids="", platform_admin=True)
        rows = conn.execute(
            "SELECT client_id::text AS client_id FROM rpt_published_fact"
        ).fetchall()
        conn.execute("ROLLBACK")
    assert {row["client_id"] for row in rows} == {CLIENT_A, CLIENT_B}


def test_unset_gucs_hide_tenant_reporting_rows(pg_conn, pg_stores, postgres_url) -> None:
    _ingest, facts, pubs, _read = pg_stores
    seed_working_set(pg_conn)
    facts.upsert(sample_fact())
    _publish(pubs, CLIENT_A, RUN_A)
    with connect(postgres_url, row_factory=dict_row) as conn:
        conn.execute("BEGIN")
        conn.execute("SET LOCAL ROLE dfip_api")
        published = conn.execute("SELECT COUNT(*) AS n FROM rpt_published_fact").fetchone()
        clients = conn.execute("SELECT COUNT(*) AS n FROM rpt_dim_client").fetchone()
        calendar = conn.execute("SELECT COUNT(*) AS n FROM rpt_dim_date").fetchone()
        findings = conn.execute("SELECT COUNT(*) AS n FROM qa_finding").fetchone()
        conn.execute("ROLLBACK")
    assert published is not None
    assert int(published["n"]) == 0
    assert clients is not None
    assert int(clients["n"]) == 0
    assert calendar is not None
    assert int(calendar["n"]) > 0
    assert findings is not None
    assert int(findings["n"]) == 0
