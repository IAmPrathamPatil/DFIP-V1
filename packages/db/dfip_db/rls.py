"""Request-scoped PostgreSQL RLS settings.

Identity is applied with SET LOCAL / set_config(..., true) inside a
transaction. Persistent session GUCs are not used for request identity.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from psycopg import Connection
from psycopg.errors import InsufficientPrivilege


@dataclass(frozen=True)
class RlsContext:
    user_id: str
    role: str
    client_ids: tuple[str, ...]
    platform_admin: bool
    subject: str = ""


_rls_context: ContextVar[RlsContext | None] = ContextVar("dfip_rls_context", default=None)


def current_rls() -> RlsContext | None:
    return _rls_context.get()


def bind_rls(context: RlsContext | None) -> None:
    _rls_context.set(context)


def reset_rls() -> None:
    _rls_context.set(None)


def apply_rls_settings(conn: Connection[Any], context: RlsContext | None) -> None:
    """Assume dfip_api and set transaction-local identity GUCs."""
    if context is None:
        return
    conn.execute("SAVEPOINT dfip_set_role")
    try:
        conn.execute("SET LOCAL ROLE dfip_api")
        conn.execute("RELEASE SAVEPOINT dfip_set_role")
    except Exception as exc:
        conn.execute("ROLLBACK TO SAVEPOINT dfip_set_role")
        if not isinstance(exc, InsufficientPrivilege):
            raise RuntimeError("Unable to assume dfip_api role.") from exc
        # Migration-owner / BYPASSRLS connections cannot SET ROLE dfip_api.
        # Keep the current role and still apply request GUCs. FORCE RLS plus
        # BYPASSRLS continues to see tenant rows; dfip_api connections still
        # assume the role when permitted.
    conn.execute("SELECT set_config('dfip.user_id', %s, true)", (context.user_id,))
    conn.execute("SELECT set_config('dfip.role', %s, true)", (context.role,))
    conn.execute(
        "SELECT set_config('dfip.client_ids', %s, true)",
        (",".join(context.client_ids),),
    )
    conn.execute(
        "SELECT set_config('dfip.platform_admin', %s, true)",
        ("true" if context.platform_admin else "false",),
    )
    conn.execute("SELECT set_config('dfip.subject', %s, true)", (context.subject,))
