"""client table directory. id and code are immutable after insert.

Directory reads assume ``dfip_api`` via ``identity_lookup_bind``. Writes that
need INSERT/UPDATE still use ``transaction(..., rls=None)`` because
``dfip_api`` has SELECT-only grants on ``client``. Application authorization
remains the primary control.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from psycopg import Connection
from psycopg_pool import ConnectionPool

from dfip_db.connection import transaction
from dfip_db.mapping import as_uuid_text
from dfip_db.rls import current_rls, identity_lookup_bind


@dataclass(frozen=True)
class ClientRecord:
    client_id: str
    code: str
    name: str
    lifecycle_status: str = "active"
    deactivated_at: datetime | None = None
    purge_eligible_after: datetime | None = None


_CLIENT_SELECT = """
    id::text AS client_id, code, name,
    COALESCE(lifecycle_status, 'active') AS lifecycle_status,
    deactivated_at, purge_eligible_after
"""


def _from_row(row) -> ClientRecord:
    return ClientRecord(
        client_id=as_uuid_text(row["client_id"]),
        code=str(row["code"]),
        name=str(row["name"]),
        lifecycle_status=str(row["lifecycle_status"] or "active"),
        deactivated_at=row["deactivated_at"],
        purge_eligible_after=row["purge_eligible_after"],
    )


def fetch_client(conn: Connection[object], client_id: str) -> ClientRecord | None:
    row = conn.execute(
        f"""
        SELECT {_CLIENT_SELECT}
        FROM client
        WHERE id = %s
        """,
        (client_id,),
    ).fetchone()
    if row is None:
        return None
    return _from_row(row)


def fetch_client_by_code(conn: Connection[object], code: str) -> ClientRecord | None:
    row = conn.execute(
        f"""
        SELECT {_CLIENT_SELECT}
        FROM client
        WHERE code = %s
        """,
        (code,),
    ).fetchone()
    if row is None:
        return None
    return _from_row(row)


def list_clients(conn: Connection[object], client_ids: Sequence[str]) -> tuple[ClientRecord, ...]:
    if not client_ids:
        return ()
    rows = conn.execute(
        f"""
        SELECT {_CLIENT_SELECT}
        FROM client
        WHERE id = ANY(%s)
        ORDER BY name, id
        """,
        (list(client_ids),),
    ).fetchall()
    return tuple(_from_row(row) for row in rows)


def list_all_clients(conn: Connection[object]) -> tuple[ClientRecord, ...]:
    rows = conn.execute(
        f"""
        SELECT {_CLIENT_SELECT}
        FROM client
        ORDER BY name, id
        """
    ).fetchall()
    return tuple(_from_row(row) for row in rows)


def update_client_name(conn: Connection[object], client_id: str, name: str) -> ClientRecord | None:
    """Change display name only. Does not update id, code, or any tenant facts."""
    row = conn.execute(
        f"""
        UPDATE client
        SET name = %s
        WHERE id = %s
        RETURNING {_CLIENT_SELECT}
        """,
        (name, client_id),
    ).fetchone()
    if row is None:
        return None
    return _from_row(row)


def set_client_lifecycle(
    conn: Connection[object],
    client_id: str,
    *,
    lifecycle_status: str,
    deactivated_at: datetime | None,
    purge_eligible_after: datetime | None,
) -> ClientRecord | None:
    row = conn.execute(
        f"""
        UPDATE client
        SET lifecycle_status = %s,
            deactivated_at = %s,
            purge_eligible_after = %s
        WHERE id = %s
        RETURNING {_CLIENT_SELECT}
        """,
        (lifecycle_status, deactivated_at, purge_eligible_after, client_id),
    ).fetchone()
    if row is None:
        return None
    return _from_row(row)


def fetch_client_from_pool(pool: ConnectionPool, client_id: str) -> ClientRecord | None:
    with identity_lookup_bind(client_ids=(client_id,)) as rls:
        with transaction(pool, rls=rls) as conn:
            return fetch_client(conn, client_id)


def fetch_client_by_code_from_pool(pool: ConnectionPool, code: str) -> ClientRecord | None:
    with identity_lookup_bind() as rls:
        with transaction(pool, rls=rls) as conn:
            return fetch_client_by_code(conn, code)


def list_clients_from_pool(
    pool: ConnectionPool, client_ids: Sequence[str]
) -> tuple[ClientRecord, ...]:
    with identity_lookup_bind(client_ids=tuple(client_ids)) as rls:
        with transaction(pool, rls=rls) as conn:
            return list_clients(conn, client_ids)


def list_all_clients_from_pool(pool: ConnectionPool) -> tuple[ClientRecord, ...]:
    with transaction(pool, rls=None) as conn:
        return list_all_clients(conn)


def update_client_name_from_pool(
    pool: ConnectionPool, client_id: str, name: str
) -> ClientRecord | None:
    with transaction(pool, rls=None) as conn:
        return update_client_name(conn, client_id, name)


def set_client_lifecycle_from_pool(
    pool: ConnectionPool,
    client_id: str,
    *,
    lifecycle_status: str,
    deactivated_at: datetime | None,
    purge_eligible_after: datetime | None,
) -> ClientRecord | None:
    with transaction(pool, rls=None) as conn:
        return set_client_lifecycle(
            conn,
            client_id,
            lifecycle_status=lifecycle_status,
            deactivated_at=deactivated_at,
            purge_eligible_after=purge_eligible_after,
        )


def insert_client_for_inspector(
    conn: Connection[object],
    *,
    name: str,
    owner_user_id: str,
    owner_role: str,
) -> ClientRecord:
    """Insert a client row and membership for the creating inspector only.

    ``id`` uses ``uuid4()``. ``code`` is that same UUID text so it cannot
    collide with existing unique codes such as ``default``. Display ``name``
    is not unique in the schema. Does not clone catalogs or create client users.
    """
    client_id = str(uuid4())
    row = conn.execute(
        f"""
        INSERT INTO client (id, code, name)
        VALUES (%s, %s, %s)
        RETURNING {_CLIENT_SELECT}
        """,
        (client_id, client_id, name),
    ).fetchone()
    if row is None:
        raise RuntimeError("client insert returned no row.")
    conn.execute(
        """
        INSERT INTO client_membership (user_id, client_id, role)
        VALUES (%s, %s, %s)
        """,
        (owner_user_id, client_id, owner_role),
    )
    return _from_row(row)


def insert_client_for_inspector_from_pool(
    pool: ConnectionPool,
    *,
    name: str,
    owner_user_id: str,
    owner_role: str,
) -> ClientRecord:
    """Insert a company under the bound HTTP inspector RLS context.

    Production LOGIN has no table grants, so this must SET LOCAL ROLE
    dfip_api. A new client id is not in dfip.client_ids, so the INSERT
    policy is inspector-role based. Fail closed if the bound context is
    missing, is not admin/publisher, or does not match the owner.
    """
    ctx = current_rls()
    if (
        ctx is None
        or ctx.role not in {"admin", "publisher"}
        or not str(ctx.user_id or "").strip()
        or owner_user_id != ctx.user_id
        or owner_role != ctx.role
    ):
        raise PermissionError("Inspector RLS context is required to create a company.")
    with transaction(pool, rls=ctx) as conn:
        return insert_client_for_inspector(
            conn, name=name, owner_user_id=owner_user_id, owner_role=owner_role
        )
