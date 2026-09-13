"""Helpers for optional PostgreSQL 16 Phase 1 tests.

Default `python -m pytest` skips these unless DFIP_TEST_DATABASE_URL is set.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from dfip_api.publication_store import InMemoryPublicationStore
from dfip_core.ingest.store import InMemoryIngestStore
from dfip_core.transform.fact import FactRecord
from dfip_core.transform.store import InMemoryFactStore
from dfip_db.connection import create_pool
from dfip_db.fact_store import PostgresFactStore
from dfip_db.ingest_store import PostgresIngestStore
from dfip_db.migrate import apply_migrations
from dfip_db.publication_store import PostgresPublicationStore
from dfip_db.read_repository import PostgresReadRepository
from psycopg import Connection, connect
from psycopg.rows import dict_row

POSTGRES_URL = os.environ.get("DFIP_TEST_DATABASE_URL", "").strip()
if os.environ.get("CI", "").strip().lower() in {"1", "true"} and not POSTGRES_URL:
    raise RuntimeError("DFIP_TEST_DATABASE_URL must be set in CI so PostgreSQL Phase 1 tests run.")
requires_postgres = pytest.mark.skipif(not POSTGRES_URL, reason="DFIP_TEST_DATABASE_URL is not set")
postgres_only = pytest.mark.postgres

CLIENT_A = "a0000000-0000-4000-8000-000000000001"
CLIENT_B = "a0000000-0000-4000-8000-000000000002"
FILE_A = "b0000000-0000-4000-8000-000000000001"
BATCH_A = "c0000000-0000-4000-8000-000000000001"
RUN_A = "d0000000-0000-4000-8000-000000000001"

_TENANT_TABLES = (
    "qa_finding",
    "publication_history_grain",
    "publication_fact",
    "publication_current",
    "publication",
    "fact_campaign_day_history",
    "fact_campaign_day",
    "stg_source_row",
    "stg_rejected_row",
    "processing_run",
    "batch",
    "source_file",
    "client_membership",
    "excel_workbook_grant",
    "analytics_saved_analysis",
    "app_user",
    "audit_log",
)


def admin_connect(url: str | None = None) -> Connection:
    return connect(url or POSTGRES_URL, row_factory=dict_row, autocommit=False)


def reset_schema(url: str | None = None) -> None:
    dsn = url or POSTGRES_URL
    with connect(dsn, autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS public CASCADE")
        conn.execute("CREATE SCHEMA public")
    with connect(dsn, autocommit=False) as conn:
        apply_migrations(conn)


def truncate_tenant(conn: Connection) -> None:
    """Remove operational tenant rows without touching P2 configuration catalogs.

    ``TRUNCATE source_file CASCADE`` would also wipe ``campaign_label_version``
    because that catalog has a nullable FK to ``source_file``.
    """
    for table in _TENANT_TABLES:
        conn.execute(f"DELETE FROM {table}")
    conn.execute(
        """
        DELETE FROM campaign_label_row
        WHERE version_id IN (
            SELECT id FROM campaign_label_version WHERE notes = 'v2c-publisher-upload'
        )
        """
    )
    conn.execute("DELETE FROM campaign_label_version WHERE notes = 'v2c-publisher-upload'")
    conn.execute(
        """
        DELETE FROM label_group_member
        WHERE version_id IN (
            SELECT id FROM label_group_version WHERE notes = 'v2c-publisher-upload'
        )
        """
    )
    conn.execute("DELETE FROM label_group_version WHERE notes = 'v2c-publisher-upload'")
    conn.execute(
        """
        INSERT INTO client (id, code, name)
        VALUES (%s, 'client-b', 'Client B')
        ON CONFLICT (id) DO NOTHING
        """,
        (CLIENT_B,),
    )
    conn.commit()


def _ts(hour: int) -> datetime:
    return datetime(2025, 8, 1, hour, 0, 0, tzinfo=UTC)


def seed_identity(
    conn: Connection,
    *,
    subject: str,
    role: str,
    client_id: str = CLIENT_A,
    platform_admin: bool = False,
) -> str:
    user_id = str(uuid4())
    conn.execute(
        """
        INSERT INTO app_user (id, subject, is_platform_admin)
        VALUES (%s, %s, %s)
        """,
        (user_id, subject, platform_admin),
    )
    if not platform_admin:
        conn.execute(
            """
            INSERT INTO client_membership (user_id, client_id, role)
            VALUES (%s, %s, %s)
            """,
            (user_id, client_id, role),
        )
    conn.commit()
    return user_id


def seed_working_set(conn: Connection) -> None:
    conn.execute(
        """
        INSERT INTO source_file (
            id, client_id, sha256, original_filename, byte_size, source_kind, uploaded_at
        )
        VALUES (%s, %s, %s, 'alpha.xlsx', 10, 'native_export', %s)
        """,
        (FILE_A, CLIENT_A, "a" * 64, _ts(1)),
    )
    conn.execute(
        """
        INSERT INTO batch (
            id, source_file_id, client_id, status, row_count_declared, row_count_staged,
            row_count_rejected, observed_day_min, observed_day_max, created_at, completed_at,
            worksheet_name, header_row, source_start_column, empty_row_count
        )
        VALUES (
            %s, %s, %s, 'processed', 3, 3, 0, %s, %s, %s, %s,
            'Web-Engage Raw', 1, 'K', 0
        )
        """,
        (BATCH_A, FILE_A, CLIENT_A, date(2025, 8, 1), date(2025, 8, 3), _ts(1), _ts(4)),
    )
    conn.execute(
        """
        INSERT INTO processing_run (
            id, batch_id, client_id, engine_version, started_at, finished_at, status
        )
        VALUES (%s, %s, %s, '0.4.0', %s, %s, 'succeeded')
        """,
        (RUN_A, BATCH_A, CLIENT_A, _ts(1), _ts(4)),
    )
    conn.commit()


def sample_fact(**overrides: object) -> FactRecord:
    values: dict[str, object] = {
        "client_id": CLIENT_A,
        "campaign_id": "camp-1",
        "variation_id": "var-1",
        "variation_id_key": "var-1",
        "day": date(2025, 8, 1),
        "campaign_name": " leading",
        "filter_logic_1": None,
        "template_status": "",
        "total_cost": Decimal("2.50"),
        "processing_run_id": RUN_A,
        "batch_id": BATCH_A,
        "first_seen_at": _ts(1),
        "last_seen_at": _ts(1),
    }
    values.update(overrides)
    return FactRecord(**values)  # type: ignore[arg-type]


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
def pg_stores(
    pg_pool: object,
) -> tuple[
    PostgresIngestStore, PostgresFactStore, PostgresPublicationStore, PostgresReadRepository
]:
    return (
        PostgresIngestStore(pg_pool),
        PostgresFactStore(pg_pool),
        PostgresPublicationStore(pg_pool),
        PostgresReadRepository(pg_pool),
    )


def memory_pair() -> tuple[InMemoryIngestStore, InMemoryFactStore, InMemoryPublicationStore]:
    return InMemoryIngestStore(), InMemoryFactStore(), InMemoryPublicationStore()
