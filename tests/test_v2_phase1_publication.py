"""PostgreSQL publication history, current pointer, isolation, and period clip."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

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


def test_publication_history_current_and_replacement(pg_conn, pg_stores) -> None:
    _ingest, facts, pubs, _read = pg_stores
    seed_working_set(pg_conn)
    facts.upsert(sample_fact())
    first, pointer = pubs.create(
        client_id=CLIENT_A,
        processing_run_id=RUN_A,
        period_start=None,
        period_end=None,
        published_by="dev",
        notes="one",
    )
    assert pointer.publication_id == first.id
    current, current_pointer = pubs.get_current(CLIENT_A)
    assert current is not None
    assert current.id == first.id
    second, pointer2 = pubs.create(
        client_id=CLIENT_A,
        processing_run_id=RUN_A,
        period_start=date(2025, 8, 1),
        period_end=date(2025, 8, 1),
        published_by="dev",
        notes="two",
    )
    assert pointer2.publication_id == second.id
    history = pubs.list_for_client(CLIENT_A)
    assert [item.id for item in history] == [first.id, second.id]
    latest, _ = pubs.get_current(CLIENT_A)
    assert latest is not None
    assert latest.id == second.id
    assert latest.notes == "two"


def test_empty_current_and_client_isolation(pg_conn, pg_stores) -> None:
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
    empty, pointer = pubs.get_current(CLIENT_B)
    assert empty is None
    assert pointer is None
    page, total = facts.list_published_slice(
        client_id=CLIENT_B,
        processing_run_id=RUN_A,
        period_start=None,
        period_end=None,
        limit=50,
        offset=0,
    )
    assert page == []
    assert total == 0


def test_period_filtering_on_published_slice(pg_conn, pg_stores) -> None:
    _ingest, facts, pubs, _read = pg_stores
    seed_working_set(pg_conn)
    facts.upsert(sample_fact(day=date(2025, 8, 1), campaign_id="camp-1"))
    facts.upsert(
        sample_fact(
            day=date(2025, 8, 2),
            campaign_id="camp-2",
            variation_id="var-2",
            variation_id_key="var-2",
            total_cost=Decimal("0"),
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
    publication, _ = pubs.get_current(CLIENT_A)
    assert publication is not None
    page, total = facts.list_published_slice(
        client_id=publication.client_id,
        processing_run_id=publication.processing_run_id,
        period_start=publication.period_start,
        period_end=publication.period_end,
        limit=200,
        offset=0,
    )
    assert total == 1
    assert page[0].campaign_id == "camp-1"
