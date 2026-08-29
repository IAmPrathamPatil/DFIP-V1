"""Immutable publication snapshots. In-memory unless DFIP_TEST_DATABASE_URL is set."""

from __future__ import annotations

from datetime import date, timedelta
from uuid import uuid4

import pytest
from dfip_api.app import create_app
from dfip_api.errors import PersistenceUnavailableError
from dfip_api.publication_store import (
    SNAPSHOT_CHUNK_SIZE,
    SNAPSHOT_STATUS_COMPLETE,
    SNAPSHOT_STATUS_NONE,
    InMemoryPublicationStore,
    PublicationCurrentRecord,
    PublicationRecord,
)
from dfip_core.transform.fact import FactRecord
from fastapi.testclient import TestClient

from postgres_support import (
    CLIENT_A,
    CLIENT_B,
    postgres_only,
    requires_postgres,
    sample_fact,
    seed_working_set,
)
from test_p5_api import (
    AUTH,
    CLIENT_ID,
    JWT_SECRET,
    RUN_A,
    RUN_C,
    _encode_jwt,
    make_settings,
    seed_stores,
)
from test_p7_publication import _publish, publisher_app, publisher_settings
from test_publication_fact_scope import _publish_current

JAN_1 = date(2026, 1, 1)
JAN_5 = date(2026, 1, 5)
JAN_10 = date(2026, 1, 10)


def _days(start: date, end: date) -> list[date]:
    values = []
    current = start
    while current <= end:
        values.append(current)
        current += timedelta(days=1)
    return values


def _put_days(facts, *, start: date, end: date, processing_run_id: str, sent) -> None:
    for day in _days(start, end):
        value = sent(day) if callable(sent) else sent
        record = FactRecord(
            client_id=CLIENT_ID,
            campaign_id="camp-snap",
            variation_id="var-1",
            variation_id_key="var-1",
            day=day,
            sent=value,
            processing_run_id=processing_run_id,
        )
        facts.facts[record.key] = record


def _current_facts(client: TestClient, **params: object) -> dict:
    query = {"client_id": CLIENT_ID, "limit": 200, **params}
    response = client.get("/api/v1/publications/current/facts", headers=AUTH, params=query)
    assert response.status_code == 200
    return response.json()


def _publication_facts(client: TestClient, publication_id: str, **params: object) -> dict:
    query = {"client_id": CLIENT_ID, "limit": 200, **params}
    response = client.get(
        f"/api/v1/publications/{publication_id}/facts",
        headers=AUTH,
        params=query,
    )
    assert response.status_code == 200
    return response.json()


def _sent_on(page: dict, day: date) -> int:
    return next(item["sent"] for item in page["items"] if item["day"] == day.isoformat())


def test_processing_run_snapshot_matches_candidate_and_stays_immutable() -> None:
    app, _ingest, facts, store = publisher_app()
    facts.facts.clear()
    facts.history.clear()
    _put_days(facts, start=JAN_1, end=JAN_10, processing_run_id=RUN_A, sent=lambda day: 100)
    extra = FactRecord(
        client_id=CLIENT_ID,
        campaign_id="camp-other-run",
        variation_id="var-1",
        variation_id_key="var-1",
        day=JAN_1,
        sent=9,
        processing_run_id=RUN_C,
    )
    facts.facts[extra.key] = extra
    client = TestClient(app)
    created = _publish(client, notes="run-scoped")
    assert created.status_code == 201
    body = created.json()["publication"]
    assert body["fact_scope"] == "processing_run"
    assert body["snapshot_status"] == SNAPSHOT_STATUS_COMPLETE
    assert body["snapshot_row_count"] == 10
    pub_id = body["publication_id"]
    current = _current_facts(client)
    assert current["pagination"]["total"] == 10
    expected_days = {day.isoformat() for day in _days(JAN_1, JAN_10)}
    assert {item["day"] for item in current["items"]} == expected_days
    assert all(item["sent"] == 100 for item in current["items"])
    historical = _publication_facts(client, pub_id)
    assert historical["pagination"]["total"] == 10
    assert {item["sent"] for item in historical["items"]} == {100}

    jan5 = FactRecord(
        client_id=CLIENT_ID,
        campaign_id="camp-snap",
        variation_id="var-1",
        variation_id_key="var-1",
        day=JAN_5,
        sent=120,
        processing_run_id=RUN_A,
    )
    facts.upsert(jan5)
    after = _current_facts(client)
    assert after["pagination"]["total"] == 10
    by_day = {item["day"]: item["sent"] for item in after["items"]}
    assert by_day[JAN_5.isoformat()] == 100
    historical = _publication_facts(client, pub_id)
    hist_by_day = {item["day"]: item["sent"] for item in historical["items"]}
    assert hist_by_day[JAN_5.isoformat()] == 100
    working = client.get("/api/v1/facts", headers=AUTH, params={"limit": 200}).json()
    working_jan5 = next(
        item
        for item in working["items"]
        if item["day"] == JAN_5.isoformat() and item["campaign_id"] == "camp-snap"
    )
    assert working_jan5["sent"] == 120
    assert store.get(pub_id) is not None
    assert store.get(pub_id).snapshot_row_count == 10


def test_client_current_snapshot_restatement_and_new_publication() -> None:
    app, _ingest, facts, store = publisher_app()
    facts.facts.clear()
    _put_days(facts, start=JAN_1, end=JAN_10, processing_run_id=RUN_A, sent=lambda day: 100)
    client = TestClient(app)
    first = _publish_current(client, RUN_A, notes="A")
    assert first.status_code == 201
    pub_a = first.json()["publication"]["publication_id"]
    assert first.json()["publication"]["snapshot_status"] == SNAPSHOT_STATUS_COMPLETE
    assert first.json()["publication"]["snapshot_row_count"] == 10
    assert {item["sent"] for item in _current_facts(client)["items"]} == {100}

    facts.upsert(
        FactRecord(
            client_id=CLIENT_ID,
            campaign_id="camp-snap",
            variation_id="var-1",
            variation_id_key="var-1",
            day=JAN_5,
            sent=120,
            processing_run_id=RUN_A,
        )
    )
    assert _sent_on(_publication_facts(client, pub_a), JAN_5) == 100
    assert _sent_on(_current_facts(client), JAN_5) == 100

    second = _publish_current(client, RUN_A, notes="B")
    assert second.status_code == 201
    pub_b = second.json()["publication"]["publication_id"]
    assert pub_b != pub_a
    current = client.get(
        "/api/v1/publications/current",
        headers=AUTH,
        params={"client_id": CLIENT_ID},
    ).json()
    assert current["publication"]["publication_id"] == pub_b
    assert current["current"]["publication_id"] == pub_b
    assert _sent_on(_current_facts(client), JAN_5) == 120
    assert _sent_on(_publication_facts(client, pub_b), JAN_5) == 120
    assert _sent_on(_publication_facts(client, pub_a), JAN_5) == 100
    history = store.list_for_client(CLIENT_ID)
    assert [item.id for item in history] == [pub_a, pub_b]
    assert history[0].snapshot_row_count == 10
    listed = client.get(
        "/api/v1/publications",
        headers=AUTH,
        params={"client_id": CLIENT_ID},
    ).json()
    assert [item["publication_id"] for item in listed["items"]] == [pub_b, pub_a]


def test_period_clip_is_preserved_in_snapshot() -> None:
    app, _ingest, facts, _store = publisher_app()
    facts.facts.clear()
    _put_days(facts, start=JAN_1, end=JAN_10, processing_run_id=RUN_A, sent=1)
    client = TestClient(app)
    created = _publish(client, period_start=JAN_1.isoformat(), period_end=JAN_5.isoformat())
    assert created.status_code == 201
    assert created.json()["publication"]["snapshot_row_count"] == 5
    body = _current_facts(client)
    assert body["pagination"]["total"] == 5
    assert max(item["day"] for item in body["items"]) == JAN_5.isoformat()
    facts.upsert(
        FactRecord(
            client_id=CLIENT_ID,
            campaign_id="camp-snap",
            variation_id="var-1",
            variation_id_key="var-1",
            day=JAN_10,
            sent=99,
            processing_run_id=RUN_A,
        )
    )
    assert _current_facts(client)["pagination"]["total"] == 5


def test_cross_client_snapshot_isolation() -> None:
    app, ingest, facts, _store = publisher_app()
    facts.facts.clear()
    _put_days(facts, start=JAN_1, end=JAN_10, processing_run_id=RUN_A, sent=1)
    facts.facts[
        FactRecord(
            client_id=CLIENT_B,
            campaign_id="camp-b",
            variation_id="var-1",
            variation_id_key="var-1",
            day=JAN_1,
            sent=77,
            processing_run_id=RUN_A,
        ).key
    ] = FactRecord(
        client_id=CLIENT_B,
        campaign_id="camp-b",
        variation_id="var-1",
        variation_id_key="var-1",
        day=JAN_1,
        sent=77,
        processing_run_id=RUN_A,
    )
    client = TestClient(app)
    created = _publish(client)
    pub_id = created.json()["publication"]["publication_id"]
    current = _current_facts(client)
    assert all(item["client_id"] == CLIENT_ID for item in current["items"])
    assert all(item["campaign_id"] != "camp-b" for item in current["items"])
    other = create_app(
        settings=make_settings(
            dfip_auth_mode="jwt",
            dfip_auth_secret=JWT_SECRET,
        ),
        ingest_store=ingest,
        fact_store=facts,
        publication_store=app.state.publication_store,
    )
    headers = {"Authorization": f"Bearer {_encode_jwt(role='reader', client_id=CLIENT_B)}"}
    leak_current = TestClient(other).get("/api/v1/publications/current/facts", headers=headers)
    assert leak_current.status_code == 200
    assert leak_current.json()["items"] == []
    leak_hist = TestClient(other).get(
        f"/api/v1/publications/{pub_id}/facts",
        headers=headers,
    )
    assert leak_hist.status_code == 404


def test_failed_snapshot_does_not_publish() -> None:
    ingest, facts = seed_stores()

    class BoomStore(InMemoryPublicationStore):
        def create(self, **_kwargs):
            raise PersistenceUnavailableError("Publication snapshot was incomplete.")

    store = BoomStore()
    app = create_app(
        settings=publisher_settings(),
        ingest_store=ingest,
        fact_store=facts,
        publication_store=store,
    )
    client = TestClient(app)
    response = _publish(client)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "PERSISTENCE_UNAVAILABLE"
    current = client.get(
        "/api/v1/publications/current",
        headers=AUTH,
        params={"client_id": CLIENT_ID},
    ).json()
    assert current == {"publication": None, "current": None}
    listed = client.get(
        "/api/v1/publications",
        headers=AUTH,
        params={"client_id": CLIENT_ID},
    ).json()
    assert listed["pagination"]["total"] == 0
    assert store.publications == {}
    assert store.current == {}
    assert store.snapshots == {}


def test_duplicate_snapshot_grains_are_rejected_before_pointer() -> None:
    store = InMemoryPublicationStore()
    fact = FactRecord(
        client_id=CLIENT_ID,
        campaign_id="camp-snap",
        variation_id="var-1",
        variation_id_key="var-1",
        day=JAN_5,
        sent=100,
        processing_run_id=RUN_A,
    )
    with pytest.raises(PersistenceUnavailableError):
        store.create(
            client_id=CLIENT_ID,
            processing_run_id=RUN_A,
            period_start=None,
            period_end=None,
            published_by="dev",
            notes=None,
            snapshot_facts=[fact, fact],
        )
    assert store.publications == {}
    assert store.current == {}
    assert store.snapshots == {}


def test_repeated_publication_does_not_duplicate_snapshot_rows() -> None:
    app, _ingest, facts, store = publisher_app()
    facts.facts.clear()
    _put_days(facts, start=JAN_1, end=JAN_10, processing_run_id=RUN_A, sent=5)
    client = TestClient(app)
    first = _publish(client, notes="one")
    second = _publish(client, notes="two")
    pub_a = first.json()["publication"]["publication_id"]
    pub_b = second.json()["publication"]["publication_id"]
    assert first.json()["publication"]["snapshot_row_count"] == 10
    assert second.json()["publication"]["snapshot_row_count"] == 10
    assert len(store.snapshots[pub_a]) == 10
    assert len(store.snapshots[pub_b]) == 10
    keys_a = {
        (item.campaign_id, item.variation_id_key, item.day) for item in store.snapshots[pub_a]
    }
    keys_b = {
        (item.campaign_id, item.variation_id_key, item.day) for item in store.snapshots[pub_b]
    }
    assert len(keys_a) == 10
    assert keys_a == keys_b
    sent_a = {(item.day, item.sent) for item in store.snapshots[pub_a]}
    sent_b = {(item.day, item.sent) for item in store.snapshots[pub_b]}
    assert sent_a == sent_b


def test_legacy_publication_without_snapshot_is_not_claimed_immutable() -> None:
    app, _ingest, facts, store = publisher_app()
    facts.facts.clear()
    _put_days(facts, start=JAN_1, end=JAN_10, processing_run_id=RUN_A, sent=100)
    pub_id = str(uuid4())
    store.publications[pub_id] = PublicationRecord(
        id=pub_id,
        client_id=CLIENT_ID,
        processing_run_id=RUN_A,
        period_start=None,
        period_end=None,
        published_at=facts.facts[next(iter(facts.facts))].first_seen_at,
        published_by="legacy",
        notes="pre-snapshot",
        fact_scope="processing_run",
        snapshot_status=SNAPSHOT_STATUS_NONE,
        snapshot_row_count=None,
    )
    store.current[CLIENT_ID] = PublicationCurrentRecord(
        client_id=CLIENT_ID,
        publication_id=pub_id,
        updated_at=store.publications[pub_id].published_at,
    )
    client = TestClient(app)
    before = _current_facts(client)
    assert before["pagination"]["total"] == 10
    facts.upsert(
        FactRecord(
            client_id=CLIENT_ID,
            campaign_id="camp-snap",
            variation_id="var-1",
            variation_id_key="var-1",
            day=JAN_5,
            sent=120,
            processing_run_id=RUN_A,
        )
    )
    after = _current_facts(client)
    by_day = {item["day"]: item["sent"] for item in after["items"]}
    assert by_day[JAN_5.isoformat()] == 120
    historical = _publication_facts(client, pub_id)
    hist_day = {item["day"]: item["sent"] for item in historical["items"]}
    assert hist_day[JAN_5.isoformat()] == 120
    meta = client.get(
        "/api/v1/publications/current",
        headers=AUTH,
        params={"client_id": CLIENT_ID},
    ).json()["publication"]
    assert meta["snapshot_status"] == SNAPSHOT_STATUS_NONE
    assert meta["snapshot_row_count"] is None


def test_current_facts_contract_and_snapshot_status() -> None:
    app, _ingest, facts, _store = publisher_app()
    client = TestClient(app)
    created = _publish(client)
    assert created.status_code == 201
    assert created.json()["publication"]["snapshot_status"] == SNAPSHOT_STATUS_COMPLETE
    current = _current_facts(client)
    assert "items" in current
    assert "pagination" in current
    assert current["pagination"]["total"] == created.json()["publication"]["snapshot_row_count"]


def test_50k_snapshot_persists_through_bounded_batches() -> None:
    app, _ingest, facts, store = publisher_app()
    facts.facts.clear()
    day = JAN_1
    for index in range(50_000):
        record = FactRecord(
            client_id=CLIENT_ID,
            campaign_id=f"camp-{index:05d}",
            variation_id="var-1",
            variation_id_key="var-1",
            day=day,
            sent=index,
            processing_run_id=RUN_A,
        )
        facts.facts[record.key] = record
    client = TestClient(app)
    created = _publish(client, notes="50k")
    assert created.status_code == 201
    body = created.json()["publication"]
    assert body["snapshot_status"] == SNAPSHOT_STATUS_COMPLETE
    assert body["snapshot_row_count"] == 50_000
    assert SNAPSHOT_CHUNK_SIZE == 500
    assert 50_000 / SNAPSHOT_CHUNK_SIZE == 100
    pub_id = body["publication_id"]
    assert len(store.snapshots[pub_id]) == 50_000
    page = _publication_facts(client, pub_id, limit=200, offset=0)
    assert page["pagination"]["total"] == 50_000
    assert len(page["items"]) == 200
    current = _current_facts(client, limit=200)
    assert current["pagination"]["total"] == 50_000


@postgres_only
@requires_postgres
def test_postgres_snapshot_and_rpt_isolation(pg_conn, pg_stores, postgres_url) -> None:
    from psycopg import connect
    from psycopg.rows import dict_row

    from test_v2_phase2a_reporting import _as_api

    _ingest, facts, pubs, _read = pg_stores
    seed_working_set(pg_conn)
    first_fact = sample_fact(day=JAN_1, campaign_id="camp-jan", sent=100)
    facts.upsert(first_fact)
    first, pointer = pubs.create(
        client_id=CLIENT_A,
        processing_run_id=RUN_A,
        period_start=None,
        period_end=None,
        published_by="dev",
        notes="A",
        fact_scope="processing_run",
        snapshot_facts=[first_fact],
    )
    assert pointer.publication_id == first.id
    assert first.snapshot_status == SNAPSHOT_STATUS_COMPLETE
    assert first.snapshot_row_count == 1
    facts.upsert(sample_fact(day=JAN_1, campaign_id="camp-jan", sent=120))
    page, total = pubs.list_snapshot(first.id, client_id=CLIENT_A, limit=10, offset=0)
    assert total == 1
    assert page[0].sent == 100
    second, current = pubs.create(
        client_id=CLIENT_A,
        processing_run_id=RUN_A,
        period_start=None,
        period_end=None,
        published_by="dev",
        notes="B",
        fact_scope="processing_run",
    )
    assert current.publication_id == second.id
    later, later_total = pubs.list_snapshot(second.id, client_id=CLIENT_A, limit=10, offset=0)
    assert later_total == 1
    assert later[0].sent == 120
    old, _ = pubs.list_snapshot(first.id, client_id=CLIENT_A, limit=10, offset=0)
    assert old[0].sent == 100
    with connect(postgres_url, row_factory=dict_row) as conn:
        _as_api(conn, role="reader", client_ids=CLIENT_A)
        published = conn.execute(
            "SELECT sent, campaign_id FROM published_fact_campaign_day"
        ).fetchall()
        rpt = conn.execute("SELECT sent FROM rpt_published_fact").fetchall()
        working = conn.execute("SELECT COUNT(*) AS n FROM fact_campaign_day").fetchone()
        conn.execute("ROLLBACK")
    assert [int(row["sent"]) for row in published] == [120]
    assert [int(row["sent"]) for row in rpt] == [120]
    assert working is not None
    assert int(working["n"]) == 0
    with connect(postgres_url, row_factory=dict_row) as conn:
        _as_api(conn, role="reader", client_ids=CLIENT_B)
        other = conn.execute("SELECT COUNT(*) AS n FROM published_fact_campaign_day").fetchone()
        snap = conn.execute("SELECT COUNT(*) AS n FROM publication_fact").fetchone()
        conn.execute("ROLLBACK")
    assert other is not None
    assert int(other["n"]) == 0
    assert snap is not None
    assert int(snap["n"]) == 0
