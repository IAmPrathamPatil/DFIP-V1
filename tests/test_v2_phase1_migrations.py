"""Phase 1 migration shape against a live PostgreSQL 16 database."""

from __future__ import annotations

from dfip_db.migrate import LEDGER_TABLE, apply_migrations
from psycopg import connect
from psycopg.rows import dict_row

from postgres_support import postgres_only, requires_postgres

pytestmark = [postgres_only, requires_postgres]


def test_v1_and_phase1_migrations_apply(postgres_url: str) -> None:
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
    for name in (
        "client",
        "source_file",
        "batch",
        "processing_run",
        "stg_source_row",
        "stg_rejected_row",
        "fact_campaign_day",
        "fact_campaign_day_history",
        "publication",
        "publication_current",
        "publication_fact",
        "publication_history_grain",
        "app_user",
        "client_membership",
        "excel_workbook_grant",
        "analytics_saved_analysis",
        "audit_log",
    ):
        assert name in tables


def test_identity_and_sha_constraints(postgres_url: str) -> None:
    with connect(postgres_url, row_factory=dict_row) as conn:
        app_user = conn.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'app_user'
            """
        ).fetchall()
        columns = {row["column_name"] for row in app_user}
        assert {
            "id",
            "subject",
            "is_platform_admin",
            "created_at",
            "updated_at",
            "password_hash",
            "token_version",
        } <= columns
        membership_check = conn.execute(
            """
            SELECT pg_get_constraintdef(oid) AS def
            FROM pg_constraint
            WHERE conrelid = 'client_membership'::regclass AND contype = 'c'
            """
        ).fetchone()
        assert membership_check is not None
        assert "admin" in membership_check["def"]
        sha = conn.execute(
            """
            SELECT pg_get_constraintdef(oid) AS def
            FROM pg_constraint
            WHERE conname = 'source_file_client_sha256_key'
            """
        ).fetchone()
        assert sha is not None
        assert "client_id" in sha["def"]
        assert "sha256" in sha["def"]
        gone = conn.execute(
            """
            SELECT 1 FROM pg_constraint WHERE conname = 'source_file_sha256_key'
            """
        ).fetchone()
        assert gone is None
        nullable = conn.execute(
            """
            SELECT is_nullable FROM information_schema.columns
            WHERE table_name = 'processing_run' AND column_name = 'client_id'
            """
        ).fetchone()
        assert nullable is not None
        assert nullable["is_nullable"] == "NO"


def test_roles_rls_policies_and_view(postgres_url: str) -> None:
    with connect(postgres_url, row_factory=dict_row) as conn:
        roles = {
            row["rolname"]
            for row in conn.execute("SELECT rolname FROM pg_roles WHERE rolname LIKE 'dfip_%'")
        }
        assert {"dfip_migrator", "dfip_api", "dfip_worker"} <= roles
        api = conn.execute(
            "SELECT rolbypassrls FROM pg_roles WHERE rolname = 'dfip_api'"
        ).fetchone()
        assert api is not None
        assert api["rolbypassrls"] is False
        forced = conn.execute(
            """
            SELECT relname FROM pg_class
            WHERE relnamespace = 'public'::regnamespace
              AND relrowsecurity
              AND relforcerowsecurity
            """
        ).fetchall()
        forced_names = {row["relname"] for row in forced}
        for name in (
            "client",
            "source_file",
            "batch",
            "processing_run",
            "stg_source_row",
            "stg_rejected_row",
            "fact_campaign_day",
            "publication",
            "publication_current",
            "publication_fact",
            "publication_history_grain",
        ):
            assert name in forced_names
        policies = conn.execute("SELECT COUNT(*) AS n FROM pg_policies").fetchone()
        assert policies is not None
        assert int(policies["n"]) >= 10
        view = conn.execute(
            """
            SELECT 1 FROM pg_views
            WHERE schemaname = 'public' AND viewname = 'published_fact_campaign_day'
            """
        ).fetchone()
        assert view is not None


def test_indexes_for_read_paths_exist(postgres_url: str) -> None:
    with connect(postgres_url, row_factory=dict_row) as conn:
        indexes = {
            row["indexname"]
            for row in conn.execute("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")
        }
    assert "fact_campaign_day_published_slice_idx" in indexes
    assert "processing_run_client_idx" in indexes
    assert "source_file_client_idx" in indexes
    assert "publication_history_grain_page_idx" in indexes


def test_apply_migrations_is_idempotent(postgres_url: str) -> None:
    with connect(postgres_url) as conn:
        again = apply_migrations(conn)
        assert again == []
        count = conn.execute(f"SELECT count(*) FROM {LEDGER_TABLE}").fetchone()
        assert count is not None
        assert int(count[0]) >= 14
