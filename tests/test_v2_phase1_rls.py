"""RLS denies cross-tenant access independently of FastAPI."""

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


def test_publisher_cannot_select_another_clients_facts(pg_conn, pg_stores, postgres_url) -> None:
    _ingest, facts, _pubs, _read = pg_stores
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
    with connect(postgres_url, row_factory=dict_row) as conn:
        _as_api(conn, role="publisher", client_ids=CLIENT_A)
        rows = conn.execute("SELECT client_id::text AS client_id FROM fact_campaign_day").fetchall()
        conn.execute("ROLLBACK")
    assert {row["client_id"] for row in rows} == {CLIENT_A}


def test_reader_cannot_select_working_set(pg_conn, pg_stores, postgres_url) -> None:
    _ingest, facts, pubs, _read = pg_stores
    seed_working_set(pg_conn)
    facts.upsert(sample_fact())
    pubs.create(
        client_id=CLIENT_A,
        processing_run_id=RUN_A,
        period_start=None,
        period_end=None,
        published_by="dev",
        notes=None,
    )
    with connect(postgres_url, row_factory=dict_row) as conn:
        _as_api(conn, role="reader", client_ids=CLIENT_A)
        working = conn.execute("SELECT COUNT(*) AS n FROM fact_campaign_day").fetchone()
        published = conn.execute("SELECT COUNT(*) AS n FROM published_fact_campaign_day").fetchone()
        staging = conn.execute("SELECT COUNT(*) AS n FROM stg_source_row").fetchone()
        conn.execute("ROLLBACK")
    assert working is not None
    assert int(working["n"]) == 0
    assert published is not None
    assert int(published["n"]) == 1
    assert staging is not None
    assert int(staging["n"]) == 0


def test_unset_gucs_hide_published_view(pg_conn, pg_stores, postgres_url) -> None:
    _ingest, facts, pubs, _read = pg_stores
    seed_working_set(pg_conn)
    facts.upsert(sample_fact())
    pubs.create(
        client_id=CLIENT_A,
        processing_run_id=RUN_A,
        period_start=None,
        period_end=None,
        published_by="dev",
        notes=None,
    )
    with connect(postgres_url, row_factory=dict_row) as conn:
        conn.execute("BEGIN")
        conn.execute("SET LOCAL ROLE dfip_api")
        rows = conn.execute("SELECT COUNT(*) AS n FROM published_fact_campaign_day").fetchone()
        conn.execute("ROLLBACK")
    assert rows is not None
    assert int(rows["n"]) == 0


def test_gucs_are_transaction_local(postgres_url: str) -> None:
    with connect(postgres_url, row_factory=dict_row) as conn:
        conn.execute("BEGIN")
        conn.execute("SET LOCAL ROLE dfip_api")
        conn.execute("SELECT set_config('dfip.role', 'publisher', true)")
        inside = conn.execute("SELECT current_setting('dfip.role', true) AS v").fetchone()
        conn.execute("COMMIT")
        after = conn.execute("SELECT current_setting('dfip.role', true) AS v").fetchone()
    assert inside is not None
    assert inside["v"] == "publisher"
    assert after is not None
    assert after["v"] in {"", None}


def test_publisher_isolation_covers_staging_runs_and_publications(
    pg_conn, pg_stores, postgres_url
) -> None:
    _ingest, facts, pubs, _read = pg_stores
    seed_working_set(pg_conn)
    _seed_client_b(pg_conn)
    facts.upsert(sample_fact())
    pubs.create(
        client_id=CLIENT_A,
        processing_run_id=RUN_A,
        period_start=None,
        period_end=None,
        published_by="dev",
        notes=None,
    )
    with connect(postgres_url, row_factory=dict_row) as conn:
        _as_api(conn, role="publisher", client_ids=CLIENT_A)
        staging = conn.execute("SELECT COUNT(*) AS n FROM stg_source_row").fetchone()
        runs = conn.execute("SELECT client_id::text AS client_id FROM processing_run").fetchall()
        publications = conn.execute(
            "SELECT client_id::text AS client_id FROM publication"
        ).fetchall()
        facts_b = conn.execute(
            "SELECT COUNT(*) AS n FROM fact_campaign_day WHERE client_id = %s",
            (CLIENT_B,),
        ).fetchone()
        conn.execute("ROLLBACK")
    assert staging is not None
    assert int(staging["n"]) == 0
    assert {row["client_id"] for row in runs} == {CLIENT_A}
    assert {row["client_id"] for row in publications} == {CLIENT_A}
    assert facts_b is not None
    assert int(facts_b["n"]) == 0


def test_platform_admin_can_read_both_clients(pg_conn, pg_stores, postgres_url) -> None:
    _ingest, facts, _pubs, _read = pg_stores
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
    with connect(postgres_url, row_factory=dict_row) as conn:
        _as_api(conn, role="admin", client_ids="", platform_admin=True)
        rows = conn.execute("SELECT client_id::text AS client_id FROM fact_campaign_day").fetchall()
        conn.execute("ROLLBACK")
    assert {row["client_id"] for row in rows} == {CLIENT_A, CLIENT_B}
