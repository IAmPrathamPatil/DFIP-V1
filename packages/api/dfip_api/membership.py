"""Database membership authorization used only when PostgreSQL mode is enabled."""

from __future__ import annotations

from dataclasses import replace

from dfip_config.settings import Settings
from dfip_db.identity import IdentityRecord
from dfip_db.rls import RlsContext
from psycopg_pool import ConnectionPool

from dfip_api.auth import Principal
from dfip_api.errors import AuthenticationError, AuthorizationError
from dfip_api.identity_store import IdentityStore, PostgresIdentityStore
from dfip_api.roles import ALLOWED_ROLES

_ROLE_RANK = {"client": 0, "reader": 0, "publisher": 1, "admin": 2}


def rls_context_for(principal: Principal, *, db_mode: bool) -> RlsContext | None:
    if not db_mode:
        return None
    client_ids = principal.membership_client_ids or ()
    if principal.client_id:
        client_ids = (principal.client_id,)
    return RlsContext(
        user_id=principal.user_id or "",
        role=principal.role,
        client_ids=client_ids,
        platform_admin=principal.platform_admin,
        subject=principal.subject,
    )


def enrich_principal(
    store: IdentityStore,
    principal: Principal,
    settings: Settings,
) -> Principal:
    """JWT subject -> app_user -> memberships. JWT claims cannot exceed membership.

    When the store is not an authoritative directory and the subject is unknown,
    claim-based JWT principals stay unchanged (in-memory tests).
    """
    if principal.auth_mode != "jwt":
        return principal
    identity = store.get_by_subject(principal.subject)
    if identity is None:
        if store.requires_directory:
            raise AuthorizationError("Not authorized to access this resource.")
        return principal
    if principal.token_version is not None and principal.token_version != identity.token_version:
        raise AuthenticationError("Invalid authentication credentials.")
    if not identity.is_platform_admin and not identity.memberships:
        raise AuthorizationError("Not authorized to access this resource.")
    return apply_identity(principal, identity, settings)


def enrich_principal_from_membership(
    pool: ConnectionPool,
    principal: Principal,
    settings: Settings,
) -> Principal:
    """JWT subject -> app_user -> memberships. JWT claims cannot exceed membership."""
    return enrich_principal(PostgresIdentityStore(pool), principal, settings)


def apply_identity(principal: Principal, identity: IdentityRecord, settings: Settings) -> Principal:
    del settings
    if identity.is_platform_admin:
        return replace(
            principal,
            role="admin",
            platform_admin=True,
            membership_client_ids=None,
            user_id=identity.user_id,
            token_version=identity.token_version,
        )
    memberships = identity.memberships
    if principal.client_id:
        memberships = tuple(item for item in memberships if item.client_id == principal.client_id)
        if not memberships:
            raise AuthorizationError("Not authorized to access this client.")
    roles = [item.role for item in memberships if item.role in ALLOWED_ROLES]
    if not roles:
        raise AuthorizationError("Not authorized to access this resource.")
    effective = max(roles, key=lambda role: _ROLE_RANK.get(role, 0))
    return replace(
        principal,
        role=effective,
        platform_admin=False,
        membership_client_ids=tuple(item.client_id for item in memberships),
        user_id=identity.user_id,
        token_version=identity.token_version,
    )
