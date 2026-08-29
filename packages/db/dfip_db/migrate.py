"""Apply DFIP SQL migrations to a PostgreSQL database."""

from __future__ import annotations

from pathlib import Path

from psycopg import Connection, Error
from psycopg.errors import DuplicateObject

from dfip_db.paths import migration_files
from dfip_db.sql_inspect import split_statements

_IGNORABLE = frozenset({"42710"})  # duplicate_object (CREATE ROLE re-apply)

LEDGER_TABLE = "dfip_schema_migration"
BASELINE_CUTOFF = "20260823000014"
BASELINE_VIEW = "rpt_published_fact"

_LEDGER_DDL = f"""
CREATE TABLE IF NOT EXISTS {LEDGER_TABLE} (
    filename text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
)
"""


def _filename(row: object) -> str:
    if isinstance(row, dict):
        return str(row["filename"])
    return str(row[0])  # type: ignore[index]


def _ensure_ledger(conn: Connection[object]) -> None:
    conn.execute(_LEDGER_DDL)


def _applied(conn: Connection[object]) -> set[str]:
    rows = conn.execute(f"SELECT filename FROM {LEDGER_TABLE}").fetchall()
    return {_filename(row) for row in rows}


def _baseline_present(conn: Connection[object]) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM information_schema.views
        WHERE table_schema = 'public' AND table_name = %s
        """,
        (BASELINE_VIEW,),
    ).fetchone()
    return row is not None


def _record(conn: Connection[object], filename: str) -> None:
    conn.execute(
        f"INSERT INTO {LEDGER_TABLE} (filename) VALUES (%s) ON CONFLICT DO NOTHING",
        (filename,),
    )


def apply_migrations(conn: Connection[object], *, files: list[Path] | None = None) -> list[str]:
    """Apply pending migration files. Safe on an empty PostgreSQL 16 database.

    Already-applied files are skipped via ``dfip_schema_migration``. Roles
    survive ``DROP SCHEMA public CASCADE``. Re-applying ``CREATE ROLE`` is
    ignored without aborting the rest of a newly applied file.

    A database that already has ``rpt_published_fact`` but no ledger is treated
    as having files through ``20260823000014`` applied, so those SQL files are
    not re-executed.
    """
    targets = files if files is not None else migration_files()
    _ensure_ledger(conn)
    conn.commit()
    if not _applied(conn) and _baseline_present(conn):
        for path in targets:
            if path.name[:14] <= BASELINE_CUTOFF:
                _record(conn, path.name)
        conn.commit()

    recorded = _applied(conn)
    applied: list[str] = []
    for path in targets:
        if path.name in recorded:
            continue
        sql = path.read_text(encoding="utf-8")
        for statement in split_statements(sql):
            conn.execute("SAVEPOINT dfip_migrate")
            try:
                conn.execute(statement)
            except DuplicateObject:
                conn.execute("ROLLBACK TO SAVEPOINT dfip_migrate")
            except Error as exc:
                if getattr(exc, "sqlstate", None) in _IGNORABLE:
                    conn.execute("ROLLBACK TO SAVEPOINT dfip_migrate")
                else:
                    conn.execute("ROLLBACK TO SAVEPOINT dfip_migrate")
                    raise
            else:
                conn.execute("RELEASE SAVEPOINT dfip_migrate")
        _record(conn, path.name)
        applied.append(path.name)
        conn.commit()
    return applied
