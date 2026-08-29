"""Pytest fixtures. Memory tests do not require PostgreSQL."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from dfip_db.connection import create_pool
from dfip_db.fact_store import PostgresFactStore
from dfip_db.ingest_store import PostgresIngestStore
from dfip_db.publication_store import PostgresPublicationStore
from dfip_db.read_repository import PostgresReadRepository
from psycopg import Connection

from postgres_support import POSTGRES_URL, admin_connect, reset_schema, truncate_tenant


@pytest.fixture(scope="session")
def postgres_url() -> str:
    if not POSTGRES_URL:
        pytest.skip("DFIP_TEST_DATABASE_URL is not set")
    reset_schema(POSTGRES_URL)
    return POSTGRES_URL


@pytest.fixture
def pg_conn(postgres_url: str) -> Iterator[Connection]:
    with admin_connect(postgres_url) as conn:
        truncate_tenant(conn)
        yield conn


@pytest.fixture
def pg_pool(postgres_url: str, pg_conn: Connection) -> Iterator[object]:
    del pg_conn
    pool = create_pool(postgres_url)
    try:
        yield pool
    finally:
        pool.close()


@pytest.fixture
def pg_stores(pg_pool: object):
    return (
        PostgresIngestStore(pg_pool),
        PostgresFactStore(pg_pool),
        PostgresPublicationStore(pg_pool),
        PostgresReadRepository(pg_pool),
    )
