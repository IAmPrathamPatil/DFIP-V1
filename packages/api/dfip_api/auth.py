"""Authentication boundary for the P5 API.

Modes:

- ``dev_token`` — constant Bearer token. Allowed only in ``development`` and
  ``test``. An empty ``DFIP_DEV_AUTH_TOKEN`` is not a bypass: every request
  fails authentication. With ``DATABASE_URL``, ``DFIP_DEV_AUTH_CLIENT_ID``
  is required and the token is never a platform-wide RLS identity.
- ``jwt`` — HS256 JWT validated with PyJWT. ``exp`` and ``sub`` are required.
  ``alg=none`` is rejected. Password sign-in issues this JWT after verifying
  ``app_user`` membership.

In development/test, ``dev_token`` mode still accepts a matching development
token. If ``DFIP_AUTH_SECRET`` is also set, a valid access JWT is accepted on
the same process so local operators can keep curl/dev-token while the SPA
uses password sign-in. Production refuses ``dev_token`` and requires ``jwt``
plus a non-empty ``DFIP_AUTH_SECRET``. Production also disables OpenAPI
(``/docs``, ``/redoc``, ``/openapi.json``).
"""

from __future__ import annotations

import hmac
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import jwt
from dfip_config.settings import Settings
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt.exceptions import InvalidTokenError

from dfip_api.errors import AuthConfigurationError, AuthenticationError
from dfip_api.roles import ALLOWED_ROLES

DEV_TOKEN_ENVIRONMENTS = frozenset({"development", "test"})
ALLOWED_AUTH_MODES = frozenset({"dev_token", "jwt"})
DEFAULT_ROLE = "reader"

bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    """Authenticated caller. Role and client_id are the P6 authorization hooks."""

    subject: str
    auth_mode: str
    role: str = DEFAULT_ROLE
    client_id: str | None = None
    platform_admin: bool = False
    membership_client_ids: tuple[str, ...] | None = None
    user_id: str | None = None
    token_version: int | None = None


def validate_auth_settings(settings: Settings) -> None:
    """Refuse configurations that would silently disable authentication."""
    environment = settings.dfip_env.strip().lower()
    mode = settings.dfip_auth_mode.strip().lower()

    if mode not in ALLOWED_AUTH_MODES:
        raise AuthConfigurationError(
            f"Unsupported DFIP_AUTH_MODE {settings.dfip_auth_mode!r}. "
            "Allowed values: dev_token, jwt."
        )

    if environment == "production":
        if mode != "jwt":
            raise AuthConfigurationError(
                "Production refuses DFIP_AUTH_MODE=dev_token. Set DFIP_AUTH_MODE=jwt "
                "and DFIP_AUTH_SECRET."
            )
        if not settings.dfip_auth_secret:
            raise AuthConfigurationError(
                "Production JWT mode requires a non-empty DFIP_AUTH_SECRET."
            )

    if mode == "dev_token" and environment not in DEV_TOKEN_ENVIRONMENTS:
        raise AuthConfigurationError(
            "DFIP_AUTH_MODE=dev_token is allowed only when DFIP_ENV is development or test."
        )

    if mode == "jwt" and not settings.dfip_auth_secret:
        raise AuthConfigurationError("JWT mode requires a non-empty DFIP_AUTH_SECRET.")

    if mode == "dev_token":
        role = settings.dfip_dev_auth_role.strip().lower() or DEFAULT_ROLE
        if role not in ALLOWED_ROLES:
            raise AuthConfigurationError(
                "DFIP_DEV_AUTH_ROLE must be one of: reader, client, publisher, admin."
            )
        client_id = configured_dev_client_id(settings)
        if settings.database_url.strip() and client_id is None:
            raise AuthConfigurationError(
                "DFIP_AUTH_MODE=dev_token with DATABASE_URL requires DFIP_DEV_AUTH_CLIENT_ID."
            )


def authenticate_bearer(settings: Settings, authorization: str | None) -> Principal:
    """Validate an Authorization header value of the form ``Bearer <credential>``."""
    if authorization is None or not authorization.strip():
        raise AuthenticationError("Authentication required.")

    scheme, separator, credential = authorization.partition(" ")
    if separator == "" or scheme.lower() != "bearer" or not credential.strip():
        raise AuthenticationError("Malformed authorization header.")

    token = credential.strip()
    if any(ch.isspace() for ch in token):
        raise AuthenticationError("Malformed authorization header.")

    mode = settings.dfip_auth_mode.strip().lower()
    if mode == "dev_token":
        if _token_matches(token, settings.dfip_dev_auth_token):
            return _authenticate_dev_token(settings, token)
        if (
            settings.dfip_env.strip().lower() in DEV_TOKEN_ENVIRONMENTS
            and settings.dfip_auth_secret
        ):
            return _authenticate_jwt(settings, token)
        raise AuthenticationError("Invalid authentication credentials.")
    if mode == "jwt":
        return _authenticate_jwt(settings, token)
    raise AuthenticationError("Authentication required.")


def _token_matches(provided: str, expected: str) -> bool:
    if not expected:
        return False
    provided_bytes = provided.encode("utf-8")
    expected_bytes = expected.encode("utf-8")
    if len(provided_bytes) != len(expected_bytes):
        return False
    return hmac.compare_digest(provided_bytes, expected_bytes)


def configured_dev_client_id(settings: Settings) -> str | None:
    """Return the bound development client, or None when the token is unscoped.

    Unscoped tokens are allowed only for in-memory development/test. A database
    DSN requires this value at startup.
    """
    raw = settings.dfip_dev_auth_client_id.strip()
    if not raw:
        return None
    try:
        return str(UUID(raw))
    except ValueError:
        raise AuthConfigurationError("DFIP_DEV_AUTH_CLIENT_ID must be a UUID.") from None


def _authenticate_dev_token(settings: Settings, token: str) -> Principal:
    environment = settings.dfip_env.strip().lower()
    if environment not in DEV_TOKEN_ENVIRONMENTS:
        raise AuthenticationError("Authentication required.")
    if not _token_matches(token, settings.dfip_dev_auth_token):
        raise AuthenticationError("Invalid authentication credentials.")
    role = settings.dfip_dev_auth_role.strip().lower() or DEFAULT_ROLE
    client_id = configured_dev_client_id(settings)
    membership = (client_id,) if client_id else None
    return Principal(
        subject="dev",
        auth_mode="dev_token",
        role=role,
        client_id=client_id,
        platform_admin=False,
        membership_client_ids=membership,
    )


def _authenticate_jwt(settings: Settings, token: str) -> Principal:
    secret = settings.dfip_auth_secret
    if not secret:
        raise AuthenticationError("Authentication required.")

    decode_options: dict[str, object] = {
        "require": ["exp", "sub"],
        "verify_signature": True,
        "verify_exp": True,
        "verify_iss": bool(settings.dfip_auth_issuer),
        "verify_aud": bool(settings.dfip_auth_audience),
    }
    kwargs: dict[str, object] = {
        "algorithms": ["HS256"],
        "options": decode_options,
        "leeway": 0,
    }
    if settings.dfip_auth_issuer:
        kwargs["issuer"] = settings.dfip_auth_issuer
    if settings.dfip_auth_audience:
        kwargs["audience"] = settings.dfip_auth_audience

    try:
        claims = jwt.decode(token, secret, **kwargs)
    except InvalidTokenError:
        raise AuthenticationError("Invalid authentication credentials.") from None

    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject:
        raise AuthenticationError("Invalid authentication credentials.")

    role = claims.get("role", DEFAULT_ROLE)
    if not isinstance(role, str) or not role:
        role = DEFAULT_ROLE

    client_id = claims.get("client_id")
    if client_id is not None and not isinstance(client_id, str):
        client_id = None

    if role not in ALLOWED_ROLES:
        raise AuthenticationError("Invalid authentication credentials.")

    token_version = _claim_token_version(claims)

    return Principal(
        subject=subject,
        auth_mode="jwt",
        role=role,
        client_id=client_id,
        token_version=token_version,
    )


def _claim_token_version(claims: Mapping[str, Any]) -> int | None:
    raw = claims.get("ver", claims.get("tvn"))
    if isinstance(raw, int) and not isinstance(raw, bool):
        return raw
    if isinstance(raw, str) and raw.isdigit():
        return int(raw)
    return None


def issue_access_token(settings: Settings, principal: Principal) -> tuple[str, int]:
    """Issue an HS256 access JWT. Never log the returned token."""
    secret = settings.dfip_auth_secret
    if not secret:
        raise AuthenticationError("Authentication required.")
    ttl = max(1, int(settings.dfip_auth_token_ttl_seconds))
    now = datetime.now(tz=UTC)
    payload: dict[str, object] = {
        "sub": principal.subject,
        "role": principal.role,
        "iat": now,
        "exp": now + timedelta(seconds=ttl),
    }
    if principal.client_id:
        payload["client_id"] = principal.client_id
    if principal.token_version is not None:
        payload["ver"] = principal.token_version
    if settings.dfip_auth_issuer:
        payload["iss"] = settings.dfip_auth_issuer
    if settings.dfip_auth_audience:
        payload["aud"] = settings.dfip_auth_audience
    token = jwt.encode(payload, secret, algorithm="HS256")
    if isinstance(token, bytes):
        token = token.decode("ascii")
    return token, ttl


def principal_from_credentials(
    settings: Settings,
    credentials: HTTPAuthorizationCredentials | None,
) -> Principal:
    if credentials is None:
        raise AuthenticationError("Authentication required.")
    header = f"{credentials.scheme} {credentials.credentials}"
    return authenticate_bearer(settings, header)
