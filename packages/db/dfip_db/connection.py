"""PostgreSQL connection helpers for V2 Phase 1.

Does not open a connection until a store checks out a pooled connection.
Maps unreachable-database failures to DatabaseUnavailableError.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from urllib.parse import parse_qs, urlparse

from psycopg import Connection, Error, InterfaceError, OperationalError
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from dfip_db.local_demo_guard import ALLOWED_LOCAL_HOSTS, hostname_of
from dfip_db.rls import RlsContext, apply_rls_settings

ALLOWED_SSL_MODES = frozenset({"require", "verify-ca", "verify-full"})
_POSTGRES_SCHEMES = frozenset({"postgres", "postgresql"})


class DatabaseUnavailableError(Exception):
    """PostgreSQL cannot be reached or cannot assume the API role."""


def redact_dsn(database_url: str) -> str:
    """Return a DSN safe for operator-facing messages. Never include passwords."""
    raw = (database_url or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    if parsed.scheme.lower() in _POSTGRES_SCHEMES:
        host = parsed.hostname or "localhost"
        port = f":{parsed.port}" if parsed.port else ""
        userinfo = "***@" if (parsed.username or parsed.password) else ""
        path = parsed.path or ""
        sslmode = (parse_qs(parsed.query).get("sslmode") or [None])[0]
        query = f"?sslmode={sslmode}" if sslmode else ""
        return f"{parsed.scheme}://{userinfo}{host}{port}{path}{query}"
    parts: list[str] = []
    for part in raw.split():
        key, separator, _value = part.partition("=")
        if separator and key.lower() in {"password", "pwd"}:
            parts.append(f"{key}=***")
        else:
            parts.append(part)
    return " ".join(parts)


def dsn_sslmode(database_url: str) -> str | None:
    """Return sslmode from a URI query or libpq key-value string."""
    raw = (database_url or "").strip()
    if not raw:
        return None
    parsed = urlparse(raw)
    if parsed.scheme.lower() in _POSTGRES_SCHEMES:
        modes = parse_qs(parsed.query).get("sslmode") or parse_qs(parsed.query).get("ssl")
        if modes:
            return modes[0].strip().lower()
        return None
    for part in raw.split():
        key, separator, value = part.partition("=")
        if separator and key.lower() == "sslmode":
            return value.strip().lower()
    return None


def dsn_is_local_host(database_url: str) -> bool:
    """True for loopback, compose service names, or a PostgreSQL unix socket URI."""
    raw = (database_url or "").strip()
    if not raw:
        return False
    host = hostname_of(raw)
    if host in ALLOWED_LOCAL_HOSTS:
        return True
    parsed = urlparse(raw)
    if parsed.scheme.lower() in _POSTGRES_SCHEMES and not parsed.hostname:
        return True
    return False


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
