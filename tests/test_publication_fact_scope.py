"""Cumulative publication fact_scope without changing run-scoped August behavior."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from dfip_api.app import create_app
from dfip_api.publication_store import InMemoryPublicationStore
from dfip_core.ingest.store import ProcessingRunRecord
from dfip_core.transform.fact import FactRecord
from fastapi.testclient import TestClient
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
from test_p5_api import AUTH, CLIENT_ID, RUN_A as MEMORY_RUN_A, RUN_C, make_settings, seed_stores
from test_p7_publication import _publish, publisher_app

CAMPAIGN = "camp-cumul"
VARIATION = "var-1"
JAN_1 = date(2026, 1, 1)
JAN_10 = date(2026, 1, 10)
JAN_11 = date(2026, 1, 11)
JAN_20 = date(2026, 1, 20)
JAN_21 = date(2026, 1, 21)
JAN_31 = date(2026, 1, 31)
FEB_1 = date(2026, 2, 1)
FEB_10 = date(2026, 2, 10)

RUN_JAN_A = "e0000000-0000-4000-8000-000000000001"
RUN_JAN_B = "e0000000-0000-4000-8000-000000000002"
RUN_JAN_C = "e0000000-0000-4000-8000-000000000003"
RUN_FEB = "e0000000-0000-4000-8000-000000000004"
RUN_CORR = "e0000000-0000-4000-8000-000000000005"


def _days(start: date, end: date) -> list[date]:
    values = []
    current = start
    while current <= end:
        values.append(current)
        current += timedelta(days=1)
    return values


def _succeeded_run(run_id: str) -> ProcessingRunRecord:
    return ProcessingRunRecord(
        id=run_id,
        batch_id=BATCH_A,
        campaign_label_version_id="a0000000-0000-4000-8000-000000000022",
        template_label_version_id="a0000000-0000-4000-8000-000000000034",
        rate_card_version_id="a0000000-0000-4000-8000-000000000012",
        label_group_version_id="a0000000-0000-4000-8000-000000000041",
        engine_version="0.4.0",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        finished_at=datetime(2026, 1, 1, 1, tzinfo=UTC),
        status="succeeded",
        qa_verdict="pass",
    )


def _fact(*, day: date, processing_run_id: str, sent: int, client_id: str = CLIENT_ID) -> FactRecord:
    return FactRecord(
        client_id=client_id,
        campaign_id=CAMPAIGN,
        variation_id=VARIATION,
        variation_id_key=VARIATION,
        day=day,
        sent=sent,
        processing_run_id=processing_run_id,
        batch_id=BATCH_A,
        first_seen_at=datetime(2026, 1, 1, tzinfo=UTC),
        last_seen_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _put_range(facts, *, start: date, end: date, processing_run_id: str) -> None:
    for day in _days(start, end):
        record = _fact(day=day, processing_run_id=processing_run_id, sent=day.day)
        facts.facts[record.key] = record


def _cumulative_app():
    ingest, facts = seed_stores()
    facts.facts.clear()
    facts.history.clear()
    for run_id in (RUN_JAN_A, RUN_JAN_B, RUN_JAN_C, RUN_FEB, RUN_CORR):
        ingest.processing_runs[run_id] = _succeeded_run(run_id)
    store = InMemoryPublicationStore()
    app = create_app(
        settings=make_settings(dfip_dev_auth_role="publisher"),
        ingest_store=ingest,
        fact_store=facts,
        publication_store=store,
    )
    return app, ingest, facts, store


def _published(client: TestClient) -> dict:
    response = client.get(
        "/api/v1/publications/current/facts",
        headers=AUTH,
        params={"client_id": CLIENT_ID, "limit": 200},
    )
    assert response.status_code == 200
    return response.json()


def _by_day(items: list[dict]) -> dict[str, dict]:
    return {item["day"]: item for item in items}


def _publish_current(client: TestClient, processing_run_id: str, **extra: object):
    return _publish(
        client,
        processing_run_id=processing_run_id,
        fact_scope="client_current",
        **extra,
    )


def test_client_current_accumulates_january_then_february() -> None:
    app, _ingest, facts, store = _cumulative_app()
    client = TestClient(app)

    _put_range(facts, start=JAN_1, end=JAN_10, processing_run_id=RUN_JAN_A)
    first = _publish_current(client, RUN_JAN_A)
    assert first.status_code == 201
    first_id = first.json()["publication"]["publication_id"]
    assert first.json()["publication"]["fact_scope"] == "client_current"
    body = _published(client)
    assert body["pagination"]["total"] == 10
    assert {item["day"] for item in body["items"]} == {day.isoformat() for day in _days(JAN_1, JAN_10)}
    jan_early = {item["day"]: item["sent"] for item in body["items"]}

    _put_range(facts, start=JAN_11, end=JAN_20, processing_run_id=RUN_JAN_B)
    second = _publish_current(client, RUN_JAN_B)
    assert second.status_code == 201
    second_id = second.json()["publication"]["publication_id"]
    body = _published(client)
    assert body["pagination"]["total"] == 20
    assert {item["day"] for item in body["items"]} == {day.isoformat() for day in _days(JAN_1, JAN_20)}
    later = _by_day(body["items"])
    for day, sent in jan_early.items():
        assert later[day]["sent"] == sent

    _put_range(facts, start=JAN_21, end=JAN_31, processing_run_id=RUN_JAN_C)
    third = _publish_current(client, RUN_JAN_C)
    assert third.status_code == 201
    third_id = third.json()["publication"]["publication_id"]
    body = _published(client)
    assert body["pagination"]["total"] == 31
    assert {item["day"] for item in body["items"]} == {day.isoformat() for day in _days(JAN_1, JAN_31)}

    _put_range(facts, start=FEB_1, end=FEB_10, processing_run_id=RUN_FEB)
    fourth = _publish_current(client, RUN_FEB)
    assert fourth.status_code == 201
    fourth_id = fourth.json()["publication"]["publication_id"]
    body = _published(client)
    assert body["pagination"]["total"] == 41
    days = {item["day"] for item in body["items"]}
    assert {day.isoformat() for day in _days(JAN_1, JAN_31)}.issubset(days)
    assert {day.isoformat() for day in _days(FEB_1, FEB_10)}.issubset(days)

    current = client.get(
        "/api/v1/publications/current",
        headers=AUTH,
        params={"client_id": CLIENT_ID},
    ).json()
    assert current["publication"]["publication_id"] == fourth_id
    assert current["current"]["publication_id"] == fourth_id
    history = store.list_for_client(CLIENT_ID)
    assert [item.id for item in history] == [first_id, second_id, third_id, fourth_id]
    listed = client.get(
        "/api/v1/publications",
        headers=AUTH,
        params={"client_id": CLIENT_ID},
    ).json()
    assert listed["pagination"]["total"] == 4
    assert {item["publication_id"] for item in listed["items"]} == {
        first_id,
        second_id,
        third_id,
        fourth_id,
    }


def test_client_current_overlapping_correction_restates_only_those_grains() -> None:
    app, _ingest, facts, _store = _cumulative_app()
    client = TestClient(app)
    _put_range(facts, start=JAN_1, end=JAN_31, processing_run_id=RUN_JAN_A)
    _put_range(facts, start=FEB_1, end=FEB_10, processing_run_id=RUN_FEB)
    _publish_current(client, RUN_FEB)
    before = _by_day(_published(client)["items"])
    feb_before = {day.isoformat(): before[day.isoformat()]["sent"] for day in _days(FEB_1, FEB_10)}

    history_before = len(facts.history)
    for day in _days(date(2026, 1, 5), date(2026, 1, 7)):
        outcome = facts.upsert(
            _fact(day=day, processing_run_id=RUN_CORR, sent=500 + day.day)
        )
        assert outcome == "restated"
    assert len(facts.history) == history_before + 3

    current_facts = {item.day: item for item in facts.list_current() if item.client_id == CLIENT_ID}
    for day in _days(JAN_1, date(2026, 1, 4)):
        assert current_facts[day].sent == day.day
        assert current_facts[day].processing_run_id == RUN_JAN_A
    for day in _days(date(2026, 1, 5), date(2026, 1, 7)):
        assert current_facts[day].sent == 500 + day.day
        assert current_facts[day].processing_run_id == RUN_CORR
    for day in _days(date(2026, 1, 8), JAN_31):
        assert current_facts[day].sent == day.day
        assert current_facts[day].processing_run_id == RUN_JAN_A
    for day in _days(FEB_1, FEB_10):
        assert current_facts[day].sent == day.day
        assert current_facts[day].processing_run_id == RUN_FEB

    published = _publish_current(client, RUN_CORR)
    assert published.status_code == 201
    items = _by_day(_published(client)["items"])
    assert _published(client)["pagination"]["total"] == 41
    for day in _days(JAN_1, date(2026, 1, 4)):
        assert items[day.isoformat()]["sent"] == day.day
    for day in _days(date(2026, 1, 5), date(2026, 1, 7)):
        assert items[day.isoformat()]["sent"] == 500 + day.day
    for day in _days(date(2026, 1, 8), JAN_31):
        assert items[day.isoformat()]["sent"] == day.day
    for day in _days(FEB_1, FEB_10):
        assert items[day.isoformat()]["sent"] == feb_before[day.isoformat()]


def test_identical_upsert_replay_does_not_duplicate_published_grains() -> None:
    app, _ingest, facts, _store = _cumulative_app()
    client = TestClient(app)
    original = _fact(day=JAN_1, processing_run_id=RUN_JAN_A, sent=9)
    assert facts.upsert(original) == "inserted"
    replay = _fact(day=JAN_1, processing_run_id=RUN_JAN_A, sent=9)
    assert facts.upsert(replay) == "unchanged"
    assert facts.history == []
    assert len(facts.facts) == 1
    created = _publish_current(client, RUN_JAN_A)
    assert created.status_code == 201
    assert _published(client)["pagination"]["total"] == 1
    assert facts.upsert(_fact(day=JAN_1, processing_run_id=RUN_JAN_A, sent=9)) == "unchanged"
    assert _published(client)["pagination"]["total"] == 1
    assert len(facts.facts) == 1


def test_processing_run_scope_returns_only_that_run() -> None:
    app, _ingest, facts, _store = publisher_app()
    other = FactRecord(
        client_id=CLIENT_ID,
        campaign_id="camp-other-run",
        variation_id="var-z",
        variation_id_key="var-z",
        day=date(2025, 8, 4),
        processing_run_id=RUN_C,
        total_cost=None,
        template_status="",
    )
    facts.facts[other.key] = other
    client = TestClient(app)
    omitted = _publish(client)
    assert omitted.status_code == 201
    assert omitted.json()["publication"]["fact_scope"] == "processing_run"
    body = _published(client)
    assert body["pagination"]["total"] == 3
    assert {item["campaign_id"] for item in body["items"]} == {"camp-1", "camp-2", "camp-3"}
    assert all(item["processing_run_id"] == MEMORY_RUN_A for item in body["items"])

    explicit = _publish(client, fact_scope="processing_run", notes="legacy")
    assert explicit.status_code == 201
    assert explicit.json()["publication"]["fact_scope"] == "processing_run"
    again = _published(client)
    assert again["pagination"]["total"] == 3
    assert {item["campaign_id"] for item in again["items"]} == {"camp-1", "camp-2", "camp-3"}


def test_client_current_period_clipping() -> None:
    app, _ingest, facts, _store = _cumulative_app()
    client = TestClient(app)
    _put_range(facts, start=JAN_1, end=JAN_31, processing_run_id=RUN_JAN_A)
    _put_range(facts, start=FEB_1, end=FEB_10, processing_run_id=RUN_FEB)
    created = _publish_current(
        client,
        RUN_FEB,
        period_start="2026-01-15",
        period_end="2026-02-05",
    )
    assert created.status_code == 201
    body = _published(client)
    days = {item["day"] for item in body["items"]}
    assert min(days) == "2026-01-15"
    assert max(days) == "2026-02-05"
    assert "2026-01-14" not in days
    assert "2026-02-06" not in days
    assert body["pagination"]["total"] == len(_days(date(2026, 1, 15), date(2026, 2, 5)))


def test_client_current_never_includes_another_client() -> None:
    app, _ingest, facts, _store = _cumulative_app()
    client = TestClient(app)
    _put_range(facts, start=JAN_1, end=JAN_10, processing_run_id=RUN_JAN_A)
    for day in _days(JAN_1, JAN_10):
        record = _fact(day=day, processing_run_id=RUN_JAN_A, sent=99, client_id=CLIENT_B)
        facts.facts[record.key] = record
    created = _publish_current(client, RUN_JAN_A)
    assert created.status_code == 201
    body = _published(client)
    assert body["pagination"]["total"] == 10
    assert all(item["client_id"] == CLIENT_ID for item in body["items"])
    assert all(item["sent"] != 99 for item in body["items"])


def test_invalid_fact_scope_is_rejected() -> None:
    app, *_rest = _cumulative_app()
    response = _publish(TestClient(app), processing_run_id=RUN_JAN_A, fact_scope="all_facts")
    assert response.status_code == 422


def _insert_run(conn, run_id: str) -> None:
    conn.execute(
        """
        INSERT INTO processing_run (
            id, batch_id, client_id, status, qa_verdict, engine_version, started_at, finished_at
        )
        VALUES (%s, %s, %s, 'succeeded', 'pass', '0.4.0', now(), now())
        """,
        (run_id, BATCH_A, CLIENT_A),
    )
    conn.commit()


def _as_api(conn, *, role: str, client_ids: str):
    conn.execute("BEGIN")
    conn.execute("SET LOCAL ROLE dfip_api")
    conn.execute("SELECT set_config('dfip.role', %s, true)", (role,))
    conn.execute("SELECT set_config('dfip.client_ids', %s, true)", (client_ids,))
    conn.execute("SELECT set_config('dfip.platform_admin', 'false', true)")
    return conn


@postgres_only
@requires_postgres
def test_postgres_existing_rows_default_to_processing_run(pg_conn, pg_stores) -> None:
    _ingest, facts, pubs, _read = pg_stores
    seed_working_set(pg_conn)
    facts.upsert(sample_fact())
    publication, _pointer = pubs.create(
        client_id=CLIENT_A,
        processing_run_id=RUN_A,
        period_start=None,
        period_end=None,
        published_by="dev",
        notes=None,
    )
    assert publication.fact_scope == "processing_run"
    row = pg_conn.execute(
        "SELECT fact_scope FROM publication WHERE id = %s",
        (publication.id,),
    ).fetchone()
    assert row is not None
    assert row["fact_scope"] == "processing_run"
    omitted = pg_conn.execute(
        """
        INSERT INTO publication (id, client_id, processing_run_id, published_by)
        VALUES (%s, %s, %s, 'dev')
        RETURNING fact_scope
        """,
        (str(uuid4()), CLIENT_A, RUN_A),
    ).fetchone()
    pg_conn.commit()
    assert omitted is not None
    assert omitted["fact_scope"] == "processing_run"


@postgres_only
@requires_postgres
def test_postgres_client_current_slice_and_run_scope_isolation(
    pg_conn, pg_stores, postgres_url
) -> None:
    _ingest, facts, pubs, _read = pg_stores
    seed_working_set(pg_conn)
    extra_run = str(uuid4())
    _insert_run(pg_conn, extra_run)
    facts.upsert(sample_fact(day=date(2026, 1, 1), campaign_id="camp-jan", sent=1))
    facts.upsert(
        sample_fact(
            day=date(2026, 1, 11),
            campaign_id="camp-jan-b",
            variation_id="var-b",
            variation_id_key="var-b",
            processing_run_id=extra_run,
            sent=2,
        )
    )
    facts.upsert(
        sample_fact(
            client_id=CLIENT_B,
            campaign_id="camp-b",
            day=date(2026, 1, 1),
            processing_run_id=extra_run,
            batch_id=None,
            sent=99,
        )
    )

    run_pub, _ = pubs.create(
        client_id=CLIENT_A,
        processing_run_id=RUN_A,
        period_start=None,
        period_end=None,
        published_by="dev",
        notes="legacy",
        fact_scope="processing_run",
    )
    run_page, run_total = facts.list_published_slice(
        client_id=CLIENT_A,
        processing_run_id=run_pub.processing_run_id,
        period_start=None,
        period_end=None,
        fact_scope=run_pub.fact_scope,
        limit=50,
        offset=0,
    )
    assert run_total == 1
    assert run_page[0].campaign_id == "camp-jan"

    cum_pub, pointer = pubs.create(
        client_id=CLIENT_A,
        processing_run_id=extra_run,
        period_start=None,
        period_end=None,
        published_by="dev",
        notes="cumulative",
        fact_scope="client_current",
    )
    assert pointer.publication_id == cum_pub.id
    remaining = pubs.list_for_client(CLIENT_A)
    assert [item.id for item in remaining] == [run_pub.id, cum_pub.id]
    cum_page, cum_total = facts.list_published_slice(
        client_id=CLIENT_A,
        processing_run_id=cum_pub.processing_run_id,
        period_start=None,
        period_end=None,
        fact_scope=cum_pub.fact_scope,
        limit=50,
        offset=0,
    )
    assert cum_total == 2
    assert {item.campaign_id for item in cum_page} == {"camp-jan", "camp-jan-b"}
    assert all(item.client_id == CLIENT_A for item in cum_page)

    clipped, clipped_total = facts.list_published_slice(
        client_id=CLIENT_A,
        processing_run_id=cum_pub.processing_run_id,
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 1),
        fact_scope="client_current",
        limit=50,
        offset=0,
    )
    assert clipped_total == 1
    assert clipped[0].campaign_id == "camp-jan"

    with connect(postgres_url, row_factory=dict_row) as conn:
        _as_api(conn, role="reader", client_ids=CLIENT_A)
        published = conn.execute(
            """
            SELECT campaign_id, client_id::text AS client_id
            FROM published_fact_campaign_day
            ORDER BY campaign_id
            """
        ).fetchall()
        working = conn.execute("SELECT COUNT(*) AS n FROM fact_campaign_day").fetchone()
        conn.execute("ROLLBACK")
    assert [row["campaign_id"] for row in published] == ["camp-jan", "camp-jan-b"]
    assert {row["client_id"] for row in published} == {CLIENT_A}
    assert working is not None
    assert int(working["n"]) == 0
