"""Client/report Excel workbook grants.

The access JWT stamped into Settings still expires. This grant is what remains
usable after expiry so POST /auth/refresh can mint a new short-lived access JWT
in memory. Power Query cannot write the new access JWT back to Settings.

Publisher/admin downloads mint a client-scoped grant for the selected
company. The stamped JWT always has role=client and platform_admin=false.
Data routes must bind typ=excel tokens through principal_from_excel_grant so
membership lookup cannot restore inspector privileges. Website logout does
not revoke workbook grants; grant expires_at and revoked_at are the kill
switches. typ=excel does not use app_user.token_version.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

from dfip_config.settings import Settings
from dfip_db.excel_grant import (
    ExcelWorkbookGrant,
    fetch_active_grant,
    fetch_grant,
    insert_grant,
    revoke_grants_for_user,
    touch_grant,
)
from psycopg_pool import ConnectionPool

from dfip_api.auth import (
    Principal,
    decode_jwt_claims,
    issue_access_token,
    principal_from_access_claims,
)
from dfip_api.errors import AuthenticationError, AuthorizationError, PersistenceUnavailableError
from dfip_api.identity_store import IdentityStore
from dfip_api.membership import apply_identity
from dfip_api.publication_service import _enforce_client_scope
from dfip_api.roles import can_inspect

EXCEL_TOKEN_TYP = "excel"
EXCEL_GRANT_ROLES = frozenset({"client", "reader"})
_INVALID = "Invalid authentication credentials."


class ExcelGrantStore(Protocol):
    def register(
        self,
        *,
        user_id: str,
        client_id: str,
        jti: str,
        expires_at: datetime,
        created_at: datetime | None = None,
    ) -> ExcelWorkbookGrant: ...

    def get(self, jti: str) -> ExcelWorkbookGrant | None: ...

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
        created_at: datetime | None = None,
    ) -> ExcelWorkbookGrant:
        now = created_at if created_at is not None else datetime.now(tz=UTC)
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
            existing = self._by_jti.get(jti)
            if existing is not None:
                return existing
            self._by_jti[jti] = grant
        return grant

    def get(self, jti: str) -> ExcelWorkbookGrant | None:
        with self._lock:
            return self._by_jti.get(jti)

    def get_active(self, jti: str) -> ExcelWorkbookGrant | None:
        now = datetime.now(tz=UTC)
        grant = self.get(jti)
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
        created_at: datetime | None = None,
    ) -> ExcelWorkbookGrant:
        return insert_grant(
            self._pool,
            user_id=user_id,
            client_id=client_id,
            jti=jti,
            expires_at=expires_at,
            created_at=created_at,
        )

    def get(self, jti: str) -> ExcelWorkbookGrant | None:
        return fetch_grant(self._pool, jti)

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
    *,
    client_id: str | None = None,
) -> str | None:
    """Stamp a client-scoped Excel access JWT and register its grant.

    Inspectors may mint a grant only for an authorized selected company.
    The JWT always carries role=client. Returns None when the caller is not a
    JWT identity with user_id (dev tokens, claim-only test JWTs). Never logs
    the returned token. Never falls back to a publisher session JWT.
    """
    if store is None or principal.auth_mode != "jwt":
        return None
    if not principal.user_id:
        return None
    target = client_id or principal.client_id
    if not target:
        return None
    try:
        UUID(target)
    except ValueError:
        return None
    _authorize_excel_grant_client(principal, target)
    jti = str(uuid4())
    access_ttl = max(1, int(settings.dfip_excel_access_ttl_seconds))
    grant_ttl = max(access_ttl, int(settings.dfip_excel_grant_ttl_seconds))
    expires_at = datetime.now(tz=UTC) + timedelta(seconds=grant_ttl)
    try:
        store.register(
            user_id=principal.user_id,
            client_id=target,
            jti=jti,
            expires_at=expires_at,
        )
    except (AuthenticationError, AuthorizationError, PersistenceUnavailableError):
        raise
    except Exception as exc:
        raise PersistenceUnavailableError() from exc
    if store.get_active(jti) is None:
        raise PersistenceUnavailableError()
    excel_principal = replace(
        principal,
        role="client",
        platform_admin=False,
        client_id=target,
        membership_client_ids=(target,),
    )
    token, _ttl = issue_access_token(
        settings,
        excel_principal,
        ttl_seconds=access_ttl,
        extra_claims={"jti": jti, "typ": EXCEL_TOKEN_TYP},
    )
    return token


def _authorize_excel_grant_client(principal: Principal, client_id: str) -> None:
    """Reuse publication tenant checks. Do not widen past membership."""
    if principal.role in EXCEL_GRANT_ROLES:
        if principal.client_id != client_id:
            raise AuthorizationError("Not authorized to access this client.")
        return
    if can_inspect(principal.role) or principal.platform_admin:
        _enforce_client_scope(principal, client_id)
        return
    raise AuthorizationError("Not authorized to access this resource.")


def principal_from_excel_grant(
    *,
    settings: Settings,
    identity_store: IdentityStore | None,
    grants: ExcelGrantStore | None,
    token: str,
    verify_exp: bool,
    restore_missing: bool = False,
) -> Principal:
    """Bind a typ=excel JWT to its live grant and force client scope.

    JWT exp may already have passed when verify_exp is False (POST /auth/refresh).
    Data routes pass verify_exp=True. Website token_version is ignored; the
    grant row is the workbook credential. Never mint publisher/admin/ops.
    """
    if grants is None:
        raise AuthenticationError(_INVALID)
    claims = decode_jwt_claims(settings, token, verify_exp=verify_exp)
    if claims.get("typ") != EXCEL_TOKEN_TYP:
        raise AuthenticationError(_INVALID)
    jti = claims.get("jti")
    if not isinstance(jti, str) or not jti:
        raise AuthenticationError(_INVALID)
    principal = principal_from_access_claims(claims)
    if principal.role not in EXCEL_GRANT_ROLES:
        raise AuthenticationError(_INVALID)
    if not principal.client_id:
        raise AuthenticationError(_INVALID)
    if identity_store is None:
        raise AuthenticationError(_INVALID)
    identity = identity_store.get_by_subject(principal.subject)
    if identity is None or not identity.user_id:
        raise AuthenticationError(_INVALID)
    principal = apply_identity(principal, identity, settings)
    grant = _resolve_excel_grant(
        grants,
        settings=settings,
        claims=claims,
        jti=jti,
        user_id=identity.user_id,
        client_id=principal.client_id,
        restore_missing=restore_missing,
    )
    if principal.client_id != grant.client_id:
        raise AuthenticationError(_INVALID)
    if identity.user_id != grant.user_id:
        raise AuthenticationError(_INVALID)
    grants.touch(jti)
    return replace(
        principal,
        role="client",
        platform_admin=False,
        client_id=grant.client_id,
        membership_client_ids=(grant.client_id,),
    )


def _resolve_excel_grant(
    grants: ExcelGrantStore,
    *,
    settings: Settings,
    claims: Mapping[str, object],
    jti: str,
    user_id: str,
    client_id: str,
    restore_missing: bool,
) -> ExcelWorkbookGrant:
    now = datetime.now(tz=UTC)
    existing = grants.get(jti)
    if existing is not None:
        if existing.revoked_at is not None or existing.expires_at <= now:
            raise AuthenticationError(_INVALID)
        return existing
    if not restore_missing:
        raise AuthenticationError(_INVALID)
    issued_at = _claim_issued_at(claims)
    if issued_at is None:
        raise AuthenticationError(_INVALID)
    access_ttl = max(1, int(settings.dfip_excel_access_ttl_seconds))
    grant_ttl = max(access_ttl, int(settings.dfip_excel_grant_ttl_seconds))
    expires_at = issued_at + timedelta(seconds=grant_ttl)
    if expires_at <= now:
        raise AuthenticationError(_INVALID)
    try:
        restored = grants.register(
            user_id=user_id,
            client_id=client_id,
            jti=jti,
            expires_at=expires_at,
            created_at=issued_at,
        )
    except (AuthenticationError, AuthorizationError):
        raise
    except Exception:
        restored = grants.get(jti)
        if restored is None:
            raise AuthenticationError(_INVALID) from None
    if restored.revoked_at is not None or restored.expires_at <= now:
        raise AuthenticationError(_INVALID)
    if restored.jti != jti or restored.client_id != client_id or restored.user_id != user_id:
        raise AuthenticationError(_INVALID)
    return restored


def _claim_issued_at(claims: Mapping[str, object]) -> datetime | None:
    raw = claims.get("iat")
    if isinstance(raw, datetime):
        if raw.tzinfo is None:
            return raw.replace(tzinfo=UTC)
        return raw.astimezone(UTC)
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return datetime.fromtimestamp(int(raw), tz=UTC)
    return None
