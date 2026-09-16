"""Password sign-in, sign-out, and access-token refresh.

Does not replace JWT/dev-token Bearer checks on resource routes.
POST /auth/refresh accepts a still-valid access JWT, or an Excel workbook
JWT whose jti grant is still active (access exp may have passed). It is
not a refresh_token grant.
"""

from __future__ import annotations

from dfip_config.settings import BOOTSTRAP_TOKEN_HEADER
from dfip_db.identity import (
    DuplicateSubjectError,
    MembershipRow,
    NoCompaniesForSetupError,
    PublisherAlreadyExistsError,
)
from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials

from dfip_api.account_policy import validate_new_account
from dfip_api.audit import write_audit_event
from dfip_api.auth import (
    Principal,
    authenticate_bearer,
    bearer_scheme,
    decode_jwt_claims,
    issue_access_token,
    token_matches,
)
from dfip_api.client_directory import overlay_session_clients
from dfip_api.deps import PrincipalDep, get_principal
from dfip_api.errors import (
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    TooManyRequests,
    ValidationFailed,
)
from dfip_api.excel_grant import EXCEL_TOKEN_TYP, principal_from_excel_grant
from dfip_api.lifecycle import require_company_active
from dfip_api.limits import bootstrap_attempt_keys, login_attempt_keys
from dfip_api.membership import apply_identity, enrich_principal
from dfip_api.password import dummy_password_hash, verify_password
from dfip_api.roles import ADMIN_ROLES, can_inspect
from dfip_api.schemas import (
    ErrorResponse,
    LoginRequest,
    LoginResponse,
    PublisherSetupRequest,
    PublisherSetupResponse,
    PublisherSetupStatusResponse,
    SelectClientRequest,
    SessionClient,
    SessionResponse,
)

ERROR_RESPONSES = {
    401: {"model": ErrorResponse, "description": "Authentication failed."},
    403: {"model": ErrorResponse, "description": "Authorization failed."},
    409: {"model": ErrorResponse, "description": "Publisher already exists."},
    413: {"model": ErrorResponse, "description": "Request body exceeds the size limit."},
    422: {"model": ErrorResponse, "description": "Validation error."},
    429: {"model": ErrorResponse, "description": "Too many requests."},
}

auth_public_router = APIRouter(responses=ERROR_RESPONSES)
auth_session_router = APIRouter(
    dependencies=[Depends(get_principal)],
    responses=ERROR_RESPONSES,
)

_INVALID = "Invalid authentication credentials."


@auth_public_router.get(
    "/auth/setup-status",
    response_model=PublisherSetupStatusResponse,
    summary="Whether one-time publisher setup is required",
    description=(
        "Returns true only when no publisher or admin identity exists. "
        "Production-grade environments always return false so the bootstrap "
        "window is not advertised to unauthenticated callers. "
        "This is not a registration page and does not create accounts."
    ),
    tags=["Auth"],
)
def setup_status(request: Request) -> PublisherSetupStatusResponse:
    settings = request.app.state.settings
    if settings.is_production_grade:
        return PublisherSetupStatusResponse(publisher_setup_required=False)
    store = request.app.state.identity_store
    return PublisherSetupStatusResponse(publisher_setup_required=not store.inspector_exists())


@auth_public_router.post(
    "/auth/setup-publisher",
    response_model=PublisherSetupResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create the one V1 universal publisher account",
    description=(
        "One-time operator setup. The operator supplies username, password, "
        "and confirm_password. Refuses if a publisher/admin already exists. "
        "Production-grade environments require header "
        f"{BOOTSTRAP_TOKEN_HEADER} matching DFIP_BOOTSTRAP_TOKEN. "
        "Does not generate credentials, create per-company publishers, or "
        "return the password. Local demo seed is a separate test fixture."
    ),
    tags=["Auth"],
)
def setup_publisher(body: PublisherSetupRequest, request: Request) -> PublisherSetupResponse:
    limiter = request.app.state.attempt_limiter
    ip_key, user_key = bootstrap_attempt_keys(request, body.username)
    if limiter.is_blocked(ip_key, user_key):
        raise TooManyRequests()
    try:
        return _create_publisher(body, request)
    except Exception:
        limiter.record_failure(ip_key, user_key)
        raise


def _create_publisher(body: PublisherSetupRequest, request: Request) -> PublisherSetupResponse:
    settings = request.app.state.settings
    if settings.is_production_grade:
        provided = request.headers.get(BOOTSTRAP_TOKEN_HEADER, "")
        if not token_matches(provided, settings.dfip_bootstrap_token):
            raise AuthenticationError("Authentication required.")
    store = request.app.state.identity_store
    directory = getattr(request.app.state, "client_directory", None)
    records = directory.list_all() if directory is not None else ()
    if not records:
        raise ValidationFailed("No companies exist. Apply migrations before publisher setup.")
    memberships = tuple(
        MembershipRow(item.client_id, "publisher", code=item.code, name=item.name)
        for item in records
    )
    username = body.username.strip()
    if not username:
        raise ValidationFailed("username is required.")
    validate_new_account(settings, username=username, password=body.password)
    try:
        identity = store.create_publisher_user(
            subject=username,
            password=body.password,
            memberships=memberships,
            iterations=settings.dfip_password_pbkdf2_iterations,
        )
    except PublisherAlreadyExistsError as exc:
        raise ConflictError(str(exc)) from exc
    except NoCompaniesForSetupError as exc:
        raise ValidationFailed(str(exc)) from exc
    except DuplicateSubjectError as exc:
        raise ValidationFailed(str(exc)) from exc
    limiter = request.app.state.attempt_limiter
    ip_key, user_key = bootstrap_attempt_keys(request, body.username)
    limiter.clear(ip_key, user_key)
    return PublisherSetupResponse(
        user_id=identity.user_id,
        username=identity.subject,
        role="publisher",
    )


def session_response(principal: Principal, identity=None, directory=None) -> SessionResponse:
    clients: list[SessionClient] = []
    if identity is not None:
        for item in identity.memberships:
            clients.append(
                SessionClient(
                    client_id=item.client_id,
                    role=item.role,
                    code=item.code,
                    name=item.name,
                )
            )
    elif principal.membership_client_ids:
        for client_id in principal.membership_client_ids:
            clients.append(SessionClient(client_id=client_id, role=principal.role))
    return SessionResponse(
        subject=principal.subject,
        auth_mode=principal.auth_mode,
        role=principal.role,
        client_id=principal.client_id,
        clients=overlay_session_clients(clients, directory),
    )


def _login_response(settings, principal: Principal, identity=None, directory=None) -> LoginResponse:
    token, expires_in = issue_access_token(settings, principal)
    return LoginResponse(
        access_token=token,
        token_type="bearer",
        expires_in=expires_in,
        session=session_response(principal, identity, directory),
    )


def _excel_refresh_response(
    settings, principal: Principal, jti: str, identity=None, directory=None
) -> LoginResponse:
    """Mint a short-lived access JWT that remains bound to the workbook grant.

    Keep typ=excel and the grant jti so membership lookup cannot restore
    publisher/admin privileges on the in-memory refresh token.
    """
    token, expires_in = issue_access_token(
        settings,
        principal,
        extra_claims={"jti": jti, "typ": EXCEL_TOKEN_TYP},
    )
    return LoginResponse(
        access_token=token,
        token_type="bearer",
        expires_in=expires_in,
        session=session_response(principal, identity, directory),
    )


def _inspector_memberships(identity) -> tuple:
    return tuple(item for item in identity.memberships if item.role in ADMIN_ROLES)


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
    inspectors = _inspector_memberships(identity)
    if stub_client is None:
        if len(identity.memberships) == 1:
            stub_client = identity.memberships[0].client_id
        elif len(inspectors) >= 2 and len(inspectors) == len(identity.memberships):
            stub_client = None
        else:
            raise AuthorizationError("Not authorized to access this client.")
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
        "A publisher or admin with two or more inspector memberships may omit "
        "client_id and then POST /auth/select-client. This is not a "
        "development-token paste endpoint."
    ),
    tags=["Auth"],
)
def login(body: LoginRequest, request: Request) -> LoginResponse:
    limiter = request.app.state.attempt_limiter
    ip_key, user_key = login_attempt_keys(request, body.username)
    if limiter.is_blocked(ip_key, user_key):
        raise TooManyRequests()
    settings = request.app.state.settings
    store = request.app.state.identity_store
    iterations = settings.dfip_password_pbkdf2_iterations
    dummy = dummy_password_hash(iterations)
    username = body.username.strip()
    if not username:
        verify_password(body.password, dummy)
        limiter.record_failure(ip_key, user_key)
        raise AuthenticationError(_INVALID)

    record = store.get_credential(username)
    if record is None or not record.password_hash:
        verify_password(body.password, dummy)
        limiter.record_failure(ip_key, user_key)
        raise AuthenticationError(_INVALID)
    if not verify_password(body.password, record.password_hash):
        limiter.record_failure(ip_key, user_key)
        raise AuthenticationError(_INVALID)

    requested = str(body.client_id) if body.client_id else None
    try:
        principal = _principal_for_identity(record.identity, requested, settings)
        directory = getattr(request.app.state, "client_directory", None)
        if principal.role in {"client", "reader"}:
            require_company_active(directory, principal.client_id)
        elif requested is not None:
            require_company_active(directory, requested)
    except AuthorizationError:
        limiter.clear(ip_key, user_key)
        raise
    limiter.clear(ip_key, user_key)
    response = _login_response(
        settings,
        principal,
        record.identity,
        getattr(request.app.state, "client_directory", None),
    )
    write_audit_event(
        getattr(request.app.state, "db_pool", None),
        actor=principal.subject,
        action="auth.login",
        entity_type="app_user",
        client_id=principal.client_id,
        after={"role": principal.role, "auth_mode": principal.auth_mode},
    )
    return response


@auth_session_router.post(
    "/auth/logout",
    status_code=204,
    summary="Invalidate issued access tokens for this user",
    description=(
        "Increments app_user.token_version so previously issued website "
        "session JWTs with a matching ver claim are rejected. Excel "
        "workbook grants are independent of website logout and remain "
        "usable until grant expiry or explicit revocation. The caller "
        "still discards the local credential."
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


@auth_public_router.post(
    "/auth/refresh",
    response_model=LoginResponse,
    summary="Issue a replacement access JWT",
    description=(
        "Requires a still-valid access JWT, or an Excel workbook JWT whose "
        "server-side grant is still active (access exp may have passed). "
        "Copies the authenticated membership scope onto a new short-lived "
        "access token. This is not a refresh_token grant. typ=excel grants "
        "are client-scoped even when a publisher minted the workbook."
    ),
    tags=["Auth"],
)
def refresh(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> LoginResponse:
    if credentials is None:
        raise AuthenticationError(_INVALID)
    settings = request.app.state.settings
    store = request.app.state.identity_store
    claims = decode_jwt_claims(settings, credentials.credentials, verify_exp=False)
    excel_jti = None
    if claims.get("typ") == EXCEL_TOKEN_TYP:
        principal = principal_from_excel_grant(
            settings=settings,
            identity_store=store,
            grants=getattr(request.app.state, "excel_grant_store", None),
            token=credentials.credentials,
            verify_exp=False,
            restore_missing=True,
        )
        raw_jti = claims.get("jti")
        if not isinstance(raw_jti, str) or not raw_jti:
            raise AuthenticationError(_INVALID)
        excel_jti = raw_jti
    else:
        header = f"{credentials.scheme} {credentials.credentials}"
        principal = authenticate_bearer(settings, header)
        if principal.auth_mode != "jwt":
            raise AuthenticationError(_INVALID)
        if store is not None:
            principal = enrich_principal(store, principal, settings)
    identity = store.get_by_subject(principal.subject) if store is not None else None
    require_company_active(
        getattr(request.app.state, "client_directory", None), principal.client_id
    )
    directory = getattr(request.app.state, "client_directory", None)
    if excel_jti is not None:
        return _excel_refresh_response(
            settings, principal, excel_jti, identity, directory
        )
    return _login_response(settings, principal, identity, directory)


@auth_session_router.post(
    "/auth/select-client",
    response_model=LoginResponse,
    summary="Bind this inspector session to one authorized client",
    description=(
        "Re-issues the access JWT with client_id set to one publisher/admin "
        "membership of the caller. JWT client_id then wins on resource routes. "
        "Client/reader callers are refused. This is not a company picker for "
        "published-data users."
    ),
    tags=["Auth"],
)
def select_client(
    body: SelectClientRequest, request: Request, principal: PrincipalDep
) -> LoginResponse:
    if principal.auth_mode != "jwt":
        raise AuthenticationError(_INVALID)
    if not can_inspect(principal.role):
        raise AuthorizationError("Not authorized to access this client.")
    settings = request.app.state.settings
    store = request.app.state.identity_store
    identity = store.get_by_subject(principal.subject)
    if identity is None:
        raise AuthorizationError("Not authorized to access this resource.")
    requested = str(body.client_id)
    inspectors = [
        item
        for item in identity.memberships
        if item.client_id == requested and item.role in ADMIN_ROLES
    ]
    if not inspectors:
        raise AuthorizationError("Not authorized to access this client.")
    require_company_active(getattr(request.app.state, "client_directory", None), requested)
    bound = _principal_for_identity(identity, requested, settings)
    return _login_response(
        settings, bound, identity, getattr(request.app.state, "client_directory", None)
    )
