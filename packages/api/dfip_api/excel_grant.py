"""Client/report Excel workbook grants.

The access JWT stamped into Settings still expires. This grant is what remains
usable after expiry so POST /auth/refresh can mint a new short-lived access JWT
in memory. Power Query cannot write the new access JWT back to Settings.
Publisher/admin principals never receive a grant.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

from dfip_config.settings import Settings
from psycopg_pool import ConnectionPool

from dfip_api.auth import Principal, issue_access_token
from dfip_api.roles import ADMIN_ROLES
from dfip_db.excel_grant import (
    ExcelWorkbookGrant,
    fetch_active_grant,
    insert_grant,
    revoke_grants_for_user,
    touch_grant,
)

EXCEL_TOKEN_TYP = "excel"
EXCEL_GRANT_ROLES = frozenset({"client", "reader"})


class ExcelGrantStore(Protocol):
    def register(
        self,
        *,
        user_id: str,
        client_id: str,
        jti: str,
        expires_at: datetime,
    ) -> ExcelWorkbookGrant: ...

    def get_active(self, jti: str) -> ExcelWorkbookGrant | None: ...

    def touch(self, jti: str) -> None: ...

    def revoke_for_user(self, user_id: str) -> int: ...


class InMemoryExcelGrantStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_jti: dict[str, ExcelWorkbookGrant] = {}

    def register(
        self,
        *,
        user_id: str,
        client_id: str,
        jti: str,
        expires_at: datetime,
    ) -> ExcelWorkbookGrant:
        now = datetime.now(tz=UTC)
        grant = ExcelWorkbookGrant(
            grant_id=str(uuid4()),
            user_id=user_id,
            client_id=client_id,
            jti=jti,
            created_at=now,
            expires_at=expires_at,
            revoked_at=None,
            last_used_at=None,
        )
        with self._lock:
            self._by_jti[jti] = grant
        return grant

    def get_active(self, jti: str) -> ExcelWorkbookGrant | None:
        now = datetime.now(tz=UTC)
        with self._lock:
            grant = self._by_jti.get(jti)
        if grant is None or grant.revoked_at is not None or grant.expires_at <= now:
            return None
        return grant

    def touch(self, jti: str) -> None:
        now = datetime.now(tz=UTC)
        with self._lock:
            grant = self._by_jti.get(jti)
            if grant is None or grant.revoked_at is not None:
                return
            self._by_jti[jti] = ExcelWorkbookGrant(
                grant_id=grant.grant_id,
                user_id=grant.user_id,
                client_id=grant.client_id,
                jti=grant.jti,
                created_at=grant.created_at,
                expires_at=grant.expires_at,
                revoked_at=grant.revoked_at,
                last_used_at=now,
            )

    def revoke_for_user(self, user_id: str) -> int:
        now = datetime.now(tz=UTC)
        changed = 0
        with self._lock:
            for jti, grant in list(self._by_jti.items()):
                if grant.user_id != user_id or grant.revoked_at is not None:
                    continue
                self._by_jti[jti] = ExcelWorkbookGrant(
                    grant_id=grant.grant_id,
                    user_id=grant.user_id,
                    client_id=grant.client_id,
                    jti=grant.jti,
                    created_at=grant.created_at,
                    expires_at=grant.expires_at,
                    revoked_at=now,
                    last_used_at=grant.last_used_at,
                )
                changed += 1
        return changed

    def purge_client(self, client_id: str) -> None:
        with self._lock:
            self._by_jti = {
                jti: grant for jti, grant in self._by_jti.items() if grant.client_id != client_id
            }


class PostgresExcelGrantStore:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def register(
        self,
        *,
        user_id: str,
        client_id: str,
        jti: str,
        expires_at: datetime,
    ) -> ExcelWorkbookGrant:
        return insert_grant(
            self._pool,
            user_id=user_id,
            client_id=client_id,
            jti=jti,
            expires_at=expires_at,
        )

    def get_active(self, jti: str) -> ExcelWorkbookGrant | None:
        return fetch_active_grant(self._pool, jti)

    def touch(self, jti: str) -> None:
        touch_grant(self._pool, jti)

    def revoke_for_user(self, user_id: str) -> int:
        return revoke_grants_for_user(self._pool, user_id)


def issue_excel_workbook_token(
    settings: Settings,
    principal: Principal,
    store: ExcelGrantStore | None,
) -> str | None:
    """Stamp a client/reader Excel access JWT and register its grant.

    Returns None for publisher/admin, missing identity, or missing client_id
    so callers keep the existing session-JWT stamp.
    Never log the returned token.
    """
    if store is None or principal.auth_mode != "jwt":
        return None
    if principal.role in ADMIN_ROLES or principal.platform_admin:
        return None
    if principal.role not in EXCEL_GRANT_ROLES:
        return None
    if not principal.user_id or not principal.client_id:
        return None
    try:
        UUID(principal.client_id)
    except ValueError:
        return None
    jti = str(uuid4())
    access_ttl = max(1, int(settings.dfip_excel_access_ttl_seconds))
    grant_ttl = max(access_ttl, int(settings.dfip_excel_grant_ttl_seconds))
    token, _ttl = issue_access_token(
        settings,
        principal,
        ttl_seconds=access_ttl,
        extra_claims={"jti": jti, "typ": EXCEL_TOKEN_TYP},
    )
    store.register(
        user_id=principal.user_id,
        client_id=principal.client_id,
        jti=jti,
        expires_at=datetime.now(tz=UTC) + timedelta(seconds=grant_ttl),
    )
    return token
