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
uses password sign-in. Production-grade environments (staging, production)
refuse ``dev_token`` and require ``jwt`` plus a strong ``DFIP_AUTH_SECRET``.
They also disable OpenAPI (``/docs``, ``/redoc``, ``/openapi.json``).
"""

from __future__ import annotations

import hmac
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import jwt
from dfip_config.settings import (
    ALLOWED_ENVIRONMENTS,
    ALLOWED_LOG_LEVELS,
    DEV_TOKEN_ENVIRONMENTS,
    KNOWN_BAD_JWT_SECRETS,
    MIN_JWT_SECRET_LENGTH,
    Settings,
)
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt.exceptions import InvalidTokenError

from dfip_api.errors import AuthConfigurationError, AuthenticationError
from dfip_api.roles import ALLOWED_ROLES

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
    environment = settings.environment_name
    if environment not in ALLOWED_ENVIRONMENTS:
        raise AuthConfigurationError(
            "Unsupported DFIP_ENV. Allowed values: development, test, staging, production."
        )

    mode = settings.dfip_auth_mode.strip().lower()
    if mode not in ALLOWED_AUTH_MODES:
        raise AuthConfigurationError(
            f"Unsupported DFIP_AUTH_MODE {settings.dfip_auth_mode!r}. "
            "Allowed values: dev_token, jwt."
        )

    log_level = settings.dfip_log_level.strip().upper()
    if settings.is_production_grade and log_level not in ALLOWED_LOG_LEVELS:
        raise AuthConfigurationError(
            "DFIP_LOG_LEVEL must be one of: DEBUG, INFO, WARNING, ERROR, CRITICAL."
        )

    if settings.is_production_grade:
        if mode != "jwt":
            raise AuthConfigurationError(
                "Production requires DFIP_AUTH_MODE=jwt. "
                "DFIP_AUTH_MODE=dev_token is allowed only in development or test."
            )
        if not settings.dfip_auth_secret.strip():
            raise AuthConfigurationError("Production requires DFIP_AUTH_SECRET.")
        _validate_production_jwt_secret(settings.dfip_auth_secret)

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


def _validate_production_jwt_secret(secret: str) -> None:
    stripped = secret.strip()
    if len(stripped) < MIN_JWT_SECRET_LENGTH:
        raise AuthConfigurationError(
            "Production-grade environments require a DFIP_AUTH_SECRET of at least 32 characters."
        )
    if stripped.lower() in KNOWN_BAD_JWT_SECRETS:
        raise AuthConfigurationError(
            "Production-grade environments reject known-bad or example DFIP_AUTH_SECRET values."
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
        if token_matches(token, settings.dfip_dev_auth_token):
            return _authenticate_dev_token(settings, token)
        if settings.environment_name in DEV_TOKEN_ENVIRONMENTS and settings.dfip_auth_secret:
            return _authenticate_jwt(settings, token)
        raise AuthenticationError("Invalid authentication credentials.")
    if mode == "jwt":
        return _authenticate_jwt(settings, token)
    raise AuthenticationError("Authentication required.")


def token_matches(provided: str, expected: str) -> bool:
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
    environment = settings.environment_name
    if environment not in DEV_TOKEN_ENVIRONMENTS:
        raise AuthenticationError("Authentication required.")
    if not token_matches(token, settings.dfip_dev_auth_token):
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
    claims = decode_jwt_claims(settings, token, verify_exp=True)
    return principal_from_access_claims(claims)


def _claim_token_version(claims: Mapping[str, Any]) -> int | None:
    raw = claims.get("ver", claims.get("tvn"))
    if isinstance(raw, int) and not isinstance(raw, bool):
        return raw
    if isinstance(raw, str) and raw.isdigit():
        return int(raw)
    return None


def issue_access_token(
    settings: Settings,
    principal: Principal,
    *,
    ttl_seconds: int | None = None,
    extra_claims: Mapping[str, Any] | None = None,
) -> tuple[str, int]:
    """Issue an HS256 access JWT. Never log the returned token."""
    secret = settings.dfip_auth_secret
    if not secret:
        raise AuthenticationError("Authentication required.")
    ttl = max(
        1, int(ttl_seconds if ttl_seconds is not None else settings.dfip_auth_token_ttl_seconds)
    )
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
    reserved = frozenset(payload)
    if extra_claims:
        for key, value in extra_claims.items():
            if key in reserved:
                continue
            payload[key] = value
    token = jwt.encode(payload, secret, algorithm="HS256")
    if isinstance(token, bytes):
        token = token.decode("ascii")
    return token, ttl


def decode_jwt_claims(
    settings: Settings,
    token: str,
    *,
    verify_exp: bool,
) -> dict[str, Any]:
    """Return verified JWT claims. Never log ``token``."""
    secret = settings.dfip_auth_secret
    if not secret:
        raise AuthenticationError("Authentication required.")
    required = ["exp", "sub"] if verify_exp else ["sub"]
    decode_options: dict[str, object] = {
        "require": required,
        "verify_signature": True,
        "verify_exp": verify_exp,
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
    if not isinstance(claims, dict):
        raise AuthenticationError("Invalid authentication credentials.")
    return claims


def principal_from_access_claims(claims: Mapping[str, Any]) -> Principal:
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

    return Principal(
        subject=subject,
        auth_mode="jwt",
        role=role,
        client_id=client_id,
        token_version=_claim_token_version(claims),
    )


def principal_from_credentials(
    settings: Settings,
    credentials: HTTPAuthorizationCredentials | None,
) -> Principal:
    if credentials is None:
        raise AuthenticationError("Authentication required.")
    header = f"{credentials.scheme} {credentials.credentials}"
    return authenticate_bearer(settings, header)
