"""Newest-wins publication_history_grain serving table vs DISTINCT ON.

Does not change Excel, mashup, page size 200, or JWT renewal.
PostgreSQL cases require DFIP_TEST_DATABASE_URL and drop ``public``.
Live Eureka comparison is opt-in via DFIP_HISTORY_LIVE_COMPARE and uses
DATABASE_URL only when it is not the test DSN.
"""

from __future__ import annotations

import os
from datetime import date
from uuid import uuid4

import pytest
from dfip_api.errors import PersistenceUnavailableError
from dfip_db.mapping import FACT_COLUMNS
from dfip_db.publication_store import PostgresPublicationStore
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
from test_v2_phase1_rls import _as_api, _seed_client_b

pytestmark = [postgres_only, requires_postgres]

PAGE = 200
EUREKA_CLIENT_ID = "2e6f0a16-269c-42db-aad4-3b1735d9977d"
LIVE_COMPARE = os.environ.get("DFIP_HISTORY_LIVE_COMPARE", "").strip().lower() in {
    "1",
    "true",
    "yes",
}

MONTHS = (
    (date(2025, 6, 2), "Jun-25", "camp-jun"),
    (date(2025, 7, 3), "Jul-25", "camp-jul"),
    (date(2025, 8, 4), "Aug-25", "camp-aug"),
    (date(2025, 9, 5), "Sep-25", "camp-sep"),
    (date(2025, 10, 6), "Oct-25", "camp-oct"),
    (date(2025, 11, 7), "Nov-25", "camp-nov"),
)


def _fingerprint(record) -> tuple:
    return tuple(getattr(record, name) for name in FACT_COLUMNS)


def _collect(fetch, client_id: str, *, limit: int = PAGE) -> tuple[list, int]:
    first, total = fetch(client_id, limit=limit, offset=0)
    rows = list(first)
    offset = len(rows)
    while offset < total:
        chunk, again = fetch(client_id, limit=limit, offset=offset)
        assert again == total
        if not chunk:
            break
        rows.extend(chunk)
        offset += len(chunk)
    return rows, total


def _assert_same(left: list, right: list, *, total_left: int, total_right: int) -> None:
    assert total_left == total_right
    assert len(left) == len(right) == total_left
    assert [_fingerprint(item) for item in left] == [_fingerprint(item) for item in right]


def _publish(store: PostgresPublicationStore, facts: list, *, run_id: str = RUN_A):
    return store.create(
        client_id=facts[0].client_id,
        processing_run_id=run_id,
        period_start=min(item.day for item in facts),
        period_end=max(item.day for item in facts),
        published_by="publisher",
        notes=None,
        snapshot_facts=facts,
    )


def test_serving_table_matches_distinct_on_months_and_replacement(pg_conn, pg_stores) -> None:
    _ingest, _facts, store, _read = pg_stores
    seed_working_set(pg_conn)
    for day, label, campaign in MONTHS:
        _publish(
            store,
            [
                sample_fact(
                    campaign_id=campaign,
                    day=day,
                    month_start=day.replace(day=1),
                    month_label=label,
                    sent=10,
                )
            ],
        )
    _publish(
        store,
        [
            sample_fact(
                campaign_id="camp-nov",
                day=date(2025, 11, 7),
                month_start=date(2025, 11, 1),
                month_label="Nov-25",
                sent=175,
            )
        ],
    )
    serving, serving_total = _collect(store.list_published_history, CLIENT_A)
    computed, computed_total = _collect(store.list_published_history_distinct_on, CLIENT_A)
    _assert_same(serving, computed, total_left=serving_total, total_right=computed_total)
    assert serving_total == 6
    months = {item.month_label for item in serving}
    assert months == {"Jun-25", "Jul-25", "Aug-25", "Sep-25", "Oct-25", "Nov-25"}
    by_campaign = {item.campaign_id: item for item in serving}
    assert by_campaign["camp-nov"].sent == 175
    assert len({(item.campaign_id, item.variation_id_key, item.day) for item in serving}) == 6


def test_fallback_distinct_on_flag_matches_serving_table(pg_conn, pg_pool) -> None:
    seed_working_set(pg_conn)
    serving_store = PostgresPublicationStore(pg_pool, use_history_serving_table=True)
    fallback_store = PostgresPublicationStore(pg_pool, use_history_serving_table=False)
    _publish(
        serving_store,
        [sample_fact(campaign_id="camp-a", day=date(2025, 10, 1), month_label="Oct-25", sent=3)],
    )
    _publish(
        serving_store,
        [sample_fact(campaign_id="camp-b", day=date(2025, 11, 1), month_label="Nov-25", sent=4)],
    )
    serving, serving_total = serving_store.list_published_history(CLIENT_A, limit=200, offset=0)
    fallback, fallback_total = fallback_store.list_published_history(CLIENT_A, limit=200, offset=0)
    _assert_same(serving, fallback, total_left=serving_total, total_right=fallback_total)
    assert serving_total == 2


def test_failed_publish_does_not_corrupt_serving_table(pg_conn, pg_stores) -> None:
    _ingest, _facts, store, _read = pg_stores
    seed_working_set(pg_conn)
    _publish(store, [sample_fact(sent=11)])
    before, before_total = _collect(store.list_published_history, CLIENT_A)
    with pytest.raises(PersistenceUnavailableError):
        _publish(store, [sample_fact(sent=99), sample_fact(sent=100)])
    after, after_total = _collect(store.list_published_history, CLIENT_A)
    _assert_same(before, after, total_left=before_total, total_right=after_total)
    assert after[0].sent == 11


def test_incomplete_publication_is_excluded_from_serving_table(pg_conn, pg_stores) -> None:
    _ingest, _facts, store, _read = pg_stores
    seed_working_set(pg_conn)
    _publish(
        store,
        [sample_fact(campaign_id="camp-complete", day=date(2025, 10, 1), month_label="Oct-25")],
    )
    incomplete_id = str(uuid4())
    pg_conn.execute(
        """
        INSERT INTO publication (
            id, client_id, processing_run_id, published_by, snapshot_status
        )
        VALUES (%s, %s, %s, 'tester', 'none')
        """,
        (incomplete_id, CLIENT_A, RUN_A),
    )
    pg_conn.execute(
        """
        INSERT INTO publication_fact (
            publication_id, client_id, campaign_id, variation_id_key, day,
            first_seen_at, last_seen_at, month_label, sent
        )
        VALUES (%s, %s, 'camp-incomplete', 'var-1', DATE '2025-09-01', now(), now(), 'Sep-25', 50)
        """,
        (incomplete_id, CLIENT_A),
    )
    pg_conn.commit()
    store.rebuild_published_history(CLIENT_A)
    serving, serving_total = _collect(store.list_published_history, CLIENT_A)
    computed, computed_total = _collect(store.list_published_history_distinct_on, CLIENT_A)
    _assert_same(serving, computed, total_left=serving_total, total_right=computed_total)
    assert serving_total == 1
    assert serving[0].campaign_id == "camp-complete"


def test_pagination_totals_and_page_edges(pg_conn, pg_stores) -> None:
    _ingest, _facts, store, _read = pg_stores
    seed_working_set(pg_conn)
    rows = [
        sample_fact(
            campaign_id=f"camp-{index:04d}",
            day=date(2025, 6, 1),
            month_start=date(2025, 6, 1),
            month_label="Jun-25",
            sent=index,
        )
        for index in range(650)
    ]
    _publish(store, rows)
    first, total = store.list_published_history(CLIENT_A, limit=PAGE, offset=0)
    middle, middle_total = store.list_published_history(CLIENT_A, limit=PAGE, offset=PAGE)
    last, last_total = store.list_published_history(CLIENT_A, limit=PAGE, offset=600)
    past, past_total = store.list_published_history(CLIENT_A, limit=PAGE, offset=800)
    assert total == middle_total == last_total == past_total == 650
    assert len(first) == PAGE
    assert len(middle) == PAGE
    assert len(last) == 50
    assert past == []
    old_first, old_total = store.list_published_history_distinct_on(CLIENT_A, limit=PAGE, offset=0)
    old_middle, _ignored = store.list_published_history_distinct_on(
        CLIENT_A, limit=PAGE, offset=PAGE
    )
    old_last, _ignored_last = store.list_published_history_distinct_on(
        CLIENT_A, limit=PAGE, offset=600
    )
    assert old_total == 650
    assert [_fingerprint(item) for item in first] == [_fingerprint(item) for item in old_first]
    assert [_fingerprint(item) for item in middle] == [_fingerprint(item) for item in old_middle]
    assert [_fingerprint(item) for item in last] == [_fingerprint(item) for item in old_last]


def test_serving_table_enforces_tenant_isolation(pg_conn, pg_stores, postgres_url) -> None:
    _ingest, _facts, store, _read = pg_stores
    seed_working_set(pg_conn)
    run_b = _seed_client_b(pg_conn)
    _publish(store, [sample_fact(campaign_id="camp-a", sent=1)])
    _publish(
        store,
        [
            sample_fact(
                client_id=CLIENT_B,
                campaign_id="camp-b",
                sent=2,
                processing_run_id=run_b,
                batch_id=None,
            )
        ],
        run_id=run_b,
    )
    a_rows, a_total = store.list_published_history(CLIENT_A, limit=200, offset=0)
    b_rows, b_total = store.list_published_history(CLIENT_B, limit=200, offset=0)
    assert a_total == 1 and a_rows[0].campaign_id == "camp-a"
    assert b_total == 1 and b_rows[0].campaign_id == "camp-b"
    with connect(postgres_url, row_factory=dict_row) as conn:
        _as_api(conn, role="client", client_ids=CLIENT_A)
        visible = conn.execute("SELECT campaign_id FROM publication_history_grain").fetchall()
        conn.execute("ROLLBACK")
    assert {row["campaign_id"] for row in visible} == {"camp-a"}


def _live_dsn() -> str:
    from dfip_config.settings import Settings

    live = Settings().database_url.strip()
    test = os.environ.get("DFIP_TEST_DATABASE_URL", "").strip()
    if not live:
        pytest.skip("DATABASE_URL is not set")
    if test and live == test:
        pytest.skip("live compare refused: DATABASE_URL equals DFIP_TEST_DATABASE_URL")
    return live


_LIVE_COMPARE_SQL = f"""
WITH computed AS (
    SELECT DISTINCT ON (pf.campaign_id, pf.variation_id_key, pf.day)
           {", ".join(f"pf.{column}" for column in FACT_COLUMNS)}
    FROM publication_fact pf
    INNER JOIN publication p ON p.id = pf.publication_id
    WHERE pf.client_id = %s
      AND p.client_id = %s
      AND p.snapshot_status = 'complete'
    ORDER BY pf.campaign_id, pf.variation_id_key, pf.day,
             p.published_at DESC, p.id DESC
),
serving AS (
    SELECT {", ".join(FACT_COLUMNS)}
    FROM publication_history_grain
    WHERE client_id = %s
)
SELECT
    (SELECT COUNT(*) FROM computed) AS computed_n,
    (SELECT COUNT(*) FROM serving) AS serving_n,
    (SELECT COUNT(*) FROM (
        SELECT {", ".join(FACT_COLUMNS)} FROM computed
        EXCEPT
        SELECT {", ".join(FACT_COLUMNS)} FROM serving
    ) missing) AS missing_from_serving,
    (SELECT COUNT(*) FROM (
        SELECT {", ".join(FACT_COLUMNS)} FROM serving
        EXCEPT
        SELECT {", ".join(FACT_COLUMNS)} FROM computed
    ) extra) AS extra_in_serving
"""


@pytest.mark.skipif(not LIVE_COMPARE, reason="DFIP_HISTORY_LIVE_COMPARE is not set")
def test_live_eureka_serving_table_equals_distinct_on() -> None:
    from dfip_db.connection import create_pool

    dsn = _live_dsn()
    pool = create_pool(dsn)
    try:
        store = PostgresPublicationStore(pool, use_history_serving_table=True)
        store.rebuild_published_history(EUREKA_CLIENT_ID)
        with store._tx() as conn:
            row = conn.execute(
                _LIVE_COMPARE_SQL,
                (EUREKA_CLIENT_ID, EUREKA_CLIENT_ID, EUREKA_CLIENT_ID),
            ).fetchone()
        assert int(row["computed_n"]) == 229883
        assert int(row["serving_n"]) == 229883
        assert int(row["missing_from_serving"]) == 0
        assert int(row["extra_in_serving"]) == 0
        first, total = store.list_published_history(EUREKA_CLIENT_ID, limit=PAGE, offset=0)
        middle, middle_total = store.list_published_history(
            EUREKA_CLIENT_ID, limit=PAGE, offset=100000
        )
        last, last_total = store.list_published_history(EUREKA_CLIENT_ID, limit=PAGE, offset=229800)
        assert total == middle_total == last_total == 229883
        assert len(first) == PAGE
        assert len(middle) == PAGE
        assert len(last) == 83
        old_first, old_total = store.list_published_history_distinct_on(
            EUREKA_CLIENT_ID, limit=PAGE, offset=0
        )
        assert old_total == 229883
        assert [_fingerprint(item) for item in first] == [_fingerprint(item) for item in old_first]
        with store._tx() as conn:
            nov_row = conn.execute(
                """
                SELECT COUNT(*) AS n
                FROM publication_history_grain
                WHERE client_id = %s AND month_label = 'Nov-25' AND sent = 175
                """,
                (EUREKA_CLIENT_ID,),
            ).fetchone()
            months = conn.execute(
                """
                SELECT DISTINCT month_label
                FROM publication_history_grain
                WHERE client_id = %s
                """,
                (EUREKA_CLIENT_ID,),
            ).fetchall()
        assert int(nov_row["n"]) >= 1
        labels = {item["month_label"] for item in months}
        assert {"Jun-25", "Jul-25", "Aug-25", "Sep-25", "Oct-25", "Nov-25"} <= labels
    finally:
        pool.close()
