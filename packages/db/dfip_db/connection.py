"""PostgreSQL connection helpers for V2 Phase 1.

Does not open a connection until a store checks out a pooled connection.
Maps unreachable-database failures to DatabaseUnavailableError.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from psycopg import Connection, Error, InterfaceError, OperationalError
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from dfip_db.rls import RlsContext, apply_rls_settings


class DatabaseUnavailableError(Exception):
    """PostgreSQL cannot be reached or cannot assume the API role."""


def create_pool(database_url: str) -> ConnectionPool:
    """Build a closed pool. The first checkout opens it."""
    conninfo = database_url.strip()
    if not conninfo:
        raise DatabaseUnavailableError("DATABASE_URL is empty.")
    return ConnectionPool(
        conninfo=conninfo,
        min_size=0,
        max_size=8,
        timeout=30,
        open=False,
        kwargs={"autocommit": False, "row_factory": dict_row},
    )


def open_pool(pool: ConnectionPool) -> None:
    if not pool.closed:
        return
    try:
        pool.open()
    except (OperationalError, InterfaceError, Error, OSError) as exc:
        raise DatabaseUnavailableError("Persistence is unavailable.") from exc


def close_pool(pool: ConnectionPool | None) -> None:
    if pool is None:
        return
    try:
        pool.close()
    except Exception:
        return


def _is_unavailable(exc: BaseException) -> bool:
    if isinstance(exc, DatabaseUnavailableError):
        return True
    if isinstance(exc, OperationalError | InterfaceError):
        return True
    if isinstance(exc, Error) and getattr(exc, "sqlstate", None) in {
        "08000",
        "08001",
        "08003",
        "08006",
    }:
        return True
    return False


@contextmanager
def transaction(pool: ConnectionPool, rls: RlsContext | None = None) -> Iterator[Connection[Any]]:
    try:
        open_pool(pool)
    except DatabaseUnavailableError:
        raise
    except (OperationalError, InterfaceError, Error, OSError) as exc:
        raise DatabaseUnavailableError("Persistence is unavailable.") from exc
    try:
        with pool.connection() as conn:
            with conn.transaction():
                apply_rls_settings(conn, rls)
                yield conn
    except DatabaseUnavailableError:
        raise
    except RuntimeError as exc:
        if "dfip_api role" in str(exc):
            raise DatabaseUnavailableError("Persistence is unavailable.") from exc
        raise
    except Exception as exc:
        if _is_unavailable(exc):
            raise DatabaseUnavailableError("Persistence is unavailable.") from exc
        raise
