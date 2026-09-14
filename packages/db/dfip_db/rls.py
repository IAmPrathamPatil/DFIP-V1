"""Request-scoped PostgreSQL RLS settings.

Identity is applied with SET LOCAL / set_config(..., true) inside a
transaction. Persistent session GUCs are not used for request identity.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from psycopg import Connection
from psycopg.errors import InsufficientPrivilege

IDENTITY_LOOKUP_USER_ID = "dfip-identity-lookup"
INSPECTOR_ROLES = frozenset({"admin", "publisher"})
_REGISTRY_MEMBERSHIP_SQL = """
    SELECT client_id::text AS client_id
    FROM client_membership
    WHERE user_id = %s::uuid
      AND role IN ('admin', 'publisher')
      AND client_id = ANY(%s::uuid[])
"""


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


def identity_lookup_rls(
    *,
    client_ids: tuple[str, ...] = (),
    platform_admin: bool = False,
    role: str = "client",
    user_id: str = IDENTITY_LOOKUP_USER_ID,
    subject: str = IDENTITY_LOOKUP_USER_ID,
) -> RlsContext:
    """SET LOCAL ROLE dfip_api for identity/directory pool reads.

    Production API LOGIN (``dfip_app``) has no table grants. ``rls=None``
    skips SET ROLE and fails with InsufficientPrivilege on ``client``.
    This is not platform-admin recovery and not HTTP tenant isolation:
    callers pass membership ``client_ids`` when the ``client`` join must
    succeed. ``app_user`` / ``client_membership`` have no RLS.
    """
    return RlsContext(
        user_id=user_id,
        role=role,
        client_ids=client_ids,
        platform_admin=platform_admin,
        subject=subject,
    )


@contextmanager
def identity_lookup_bind(
    *,
    client_ids: tuple[str, ...] = (),
    platform_admin: bool = False,
    role: str = "client",
    user_id: str = IDENTITY_LOOKUP_USER_ID,
    subject: str = IDENTITY_LOOKUP_USER_ID,
) -> Iterator[RlsContext]:
    """Bind lookup RLS only when no HTTP/worker/recovery context is set.

    Login and ``enrich_principal`` run before ``get_principal`` binds.
    Authenticated handlers keep the existing principal context. Always
    restores the previous ContextVar, including on exception.
    """
    previous = current_rls()
    if previous is None:
        bind_rls(
            identity_lookup_rls(
                client_ids=client_ids,
                platform_admin=platform_admin,
                role=role,
                user_id=user_id,
                subject=subject,
            )
        )
    try:
        bound = current_rls()
        if bound is None:
            raise RuntimeError("identity lookup RLS was not bound.")
        yield bound
    finally:
        if previous is None:
            reset_rls()
        else:
            bind_rls(previous)


def require_inspector_rls() -> RlsContext:
    """Fail closed unless the bound HTTP/worker context is an inspector."""
    ctx = current_rls()
    if ctx is None or ctx.role not in INSPECTOR_ROLES or not str(ctx.user_id or "").strip():
        raise PermissionError("Inspector RLS context is required for company registry access.")
    return ctx


def expand_inspector_registry_clients(
    conn: Connection[Any], target_ids: Sequence[str]
) -> tuple[str, ...]:
    """Widen this transaction's dfip.client_ids to membership-authorized targets.

    Does not change the request ContextVar. Does not set platform_admin.
    Only ids with an inspector membership for ctx.user_id are added, so a
    selected-company JWT can still SELECT/UPDATE another authorized company.
    """
    ctx = require_inspector_rls()
    wanted = tuple(dict.fromkeys(str(item).strip() for item in target_ids if str(item or "").strip()))
    if not wanted:
        return ()
    rows = conn.execute(_REGISTRY_MEMBERSHIP_SQL, (ctx.user_id, list(wanted))).fetchall()
    authorized = tuple(str(row["client_id"]) for row in rows)
    merged = tuple(dict.fromkeys((*ctx.client_ids, *authorized)))
    conn.execute("SELECT set_config('dfip.client_ids', %s, true)", (",".join(merged),))
    return authorized


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
