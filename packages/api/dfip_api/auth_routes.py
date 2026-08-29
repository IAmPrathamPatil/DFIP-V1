"""Password sign-in, sign-out, and access-token refresh.

Does not replace JWT/dev-token Bearer checks on resource routes.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response

from dfip_api.auth import Principal, issue_access_token
from dfip_api.deps import PrincipalDep, get_principal
from dfip_api.errors import AuthenticationError, AuthorizationError
from dfip_api.membership import apply_identity
from dfip_api.password import dummy_password_hash, verify_password
from dfip_api.schemas import (
    ErrorResponse,
    LoginRequest,
    LoginResponse,
    SessionResponse,
)

ERROR_RESPONSES = {
    401: {"model": ErrorResponse, "description": "Authentication failed."},
    403: {"model": ErrorResponse, "description": "Authorization failed."},
    422: {"model": ErrorResponse, "description": "Validation error."},
}

auth_public_router = APIRouter(responses=ERROR_RESPONSES)
auth_session_router = APIRouter(
    dependencies=[Depends(get_principal)],
    responses=ERROR_RESPONSES,
)

_INVALID = "Invalid authentication credentials."


def _session_body(principal: Principal) -> SessionResponse:
    return SessionResponse(
        subject=principal.subject,
        auth_mode=principal.auth_mode,
        role=principal.role,
        client_id=principal.client_id,
    )


def _login_response(settings, principal: Principal) -> LoginResponse:
    token, expires_in = issue_access_token(settings, principal)
    return LoginResponse(
        access_token=token,
        token_type="bearer",
        expires_in=expires_in,
        session=_session_body(principal),
    )


def _principal_for_identity(identity, requested_client_id: str | None, settings) -> Principal:
    stub_client = requested_client_id
    if identity.is_platform_admin:
        stub = Principal(
            subject=identity.subject,
            auth_mode="jwt",
            role="admin",
            client_id=stub_client,
            token_version=identity.token_version,
        )
        return apply_identity(stub, identity, settings)
    if not identity.memberships:
        raise AuthorizationError("Not authorized to access this resource.")
    if stub_client is None:
        if len(identity.memberships) != 1:
            raise AuthorizationError("Not authorized to access this client.")
        stub_client = identity.memberships[0].client_id
    stub = Principal(
        subject=identity.subject,
        auth_mode="jwt",
        role="client",
        client_id=stub_client,
        token_version=identity.token_version,
    )
    return apply_identity(stub, identity, settings)


@auth_public_router.post(
    "/auth/login",
    response_model=LoginResponse,
    summary="Sign in with username and password",
    description=(
        "Verifies app_user credentials and issues an HS256 access JWT bound to "
        "exactly one client membership (or an unbound platform-admin token). "
        "This is not a development-token paste endpoint."
    ),
    tags=["Auth"],
)
def login(body: LoginRequest, request: Request) -> LoginResponse:
    settings = request.app.state.settings
    store = request.app.state.identity_store
    iterations = settings.dfip_password_pbkdf2_iterations
    dummy = dummy_password_hash(iterations)
    username = body.username.strip()
    if not username:
        verify_password(body.password, dummy)
        raise AuthenticationError(_INVALID)

    record = store.get_credential(username)
    if record is None or not record.password_hash:
        verify_password(body.password, dummy)
        raise AuthenticationError(_INVALID)
    if not verify_password(body.password, record.password_hash):
        raise AuthenticationError(_INVALID)

    requested = str(body.client_id) if body.client_id else None
    principal = _principal_for_identity(record.identity, requested, settings)
    return _login_response(settings, principal)


@auth_session_router.post(
    "/auth/logout",
    status_code=204,
    summary="Invalidate issued access tokens for this user",
    description=(
        "Increments app_user.token_version so previously issued access JWTs "
        "with a matching ver claim are rejected. The caller still discards "
        "the local credential."
    ),
    tags=["Auth"],
    response_class=Response,
)
def logout(request: Request, principal: PrincipalDep) -> Response:
    store = request.app.state.identity_store
    user_id = principal.user_id
    if user_id is None and principal.auth_mode == "jwt":
        identity = store.get_by_subject(principal.subject)
        if identity is not None:
            user_id = identity.user_id
    if user_id:
        store.increment_token_version(user_id)
    return Response(status_code=204)


@auth_session_router.post(
    "/auth/refresh",
    response_model=LoginResponse,
    summary="Issue a replacement access JWT",
    description=(
        "Requires a still-valid access JWT. Copies the authenticated membership "
        "scope onto a new token with a fresh expiry."
    ),
    tags=["Auth"],
)
def refresh(request: Request, principal: PrincipalDep) -> LoginResponse:
    if principal.auth_mode != "jwt":
        raise AuthenticationError(_INVALID)
    settings = request.app.state.settings
    store = request.app.state.identity_store
    identity = store.get_by_subject(principal.subject)
    if identity is not None:
        principal = apply_identity(principal, identity, settings)
    return _login_response(settings, principal)
