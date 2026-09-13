"""Publisher company registry. List, create, and rename display names."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from dfip_db.identity import DuplicateSubjectError
from fastapi import APIRouter, Depends, Query, Request, status

from dfip_api.account_policy import validate_new_account
from dfip_api.client_directory import (
    apply_name_to_identity_store,
    grant_created_company,
    inspector_directory_ids,
)
from dfip_api.company_delete import delete_company
from dfip_api.deps import InspectorDep, get_principal
from dfip_api.errors import AuthorizationError, NotFoundError, ValidationFailed
from dfip_api.lifecycle import (
    LIFECYCLE_ACTIVE,
    LIFECYCLE_INACTIVE,
    purge_eligible_after_for,
    require_company_active,
)
from dfip_api.roles import ADMIN_ROLES
from dfip_api.schemas import (
    ClientCreateRequest,
    ClientDeleteResponse,
    ClientPage,
    ClientRenameRequest,
    ClientResponse,
    ClientUserCreateRequest,
    ClientUserResponse,
    ErrorResponse,
)

ERROR_RESPONSES = {
    401: {"model": ErrorResponse, "description": "Authentication failed."},
    403: {"model": ErrorResponse, "description": "Authorization failed."},
    404: {"model": ErrorResponse, "description": "Resource not found."},
    409: {"model": ErrorResponse, "description": "Conflict."},
    422: {"model": ErrorResponse, "description": "Validation error."},
    503: {"model": ErrorResponse, "description": "Persistence unavailable."},
}

client_router = APIRouter(
    dependencies=[Depends(get_principal)],
    responses=ERROR_RESPONSES,
)


def _to_response(record) -> ClientResponse:
    return ClientResponse(
        client_id=record.client_id,
        code=record.code,
        name=record.name,
        lifecycle_status=record.lifecycle_status,
        deactivated_at=record.deactivated_at,
        purge_eligible_after=record.purge_eligible_after,
    )


def _allowed_ids(request: Request, principal) -> tuple[str, ...]:
    store = getattr(request.app.state, "identity_store", None)
    identity = store.get_by_subject(principal.subject) if store is not None else None
    return inspector_directory_ids(principal, identity)


@client_router.get(
    "/clients",
    response_model=ClientPage,
    summary="List companies this inspector may operate",
    description=(
        "Returns client directory rows for the caller's publisher/admin "
        "memberships. Names come from the client table. Inactive companies "
        "are omitted unless include_inactive=true. This is not a "
        "published-data API and is not a company-create endpoint."
    ),
    tags=["Companies"],
)
def list_clients(
    request: Request,
    principal: InspectorDep,
    include_inactive: Annotated[bool, Query()] = False,
) -> ClientPage:
    allowed = _allowed_ids(request, principal)
    directory = request.app.state.client_directory
    items = [_to_response(item) for item in directory.list(allowed)]
    known = {item.client_id for item in items}
    store = getattr(request.app.state, "identity_store", None)
    identity = store.get_by_subject(principal.subject) if store is not None else None
    if identity is not None:
        for membership in identity.memberships:
            if membership.client_id in known or membership.client_id not in allowed:
                continue
            items.append(
                ClientResponse(
                    client_id=membership.client_id,
                    code=membership.code or membership.client_id,
                    name=membership.name or membership.code or membership.client_id,
                    lifecycle_status="active",
                )
            )
    items.sort(key=lambda row: (row.name.lower(), row.client_id))
    if not include_inactive:
        items = [item for item in items if item.lifecycle_status != LIFECYCLE_INACTIVE]
    return ClientPage(items=items)


@client_router.post(
    "/clients",
    response_model=ClientResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a company",
    description=(
        "Inserts a new client row with a generated id. Grants inspector "
        "membership to the caller only. Does not create client users, "
        "clone catalogs, or change publications."
    ),
    tags=["Companies"],
)
def create_client(
    body: ClientCreateRequest,
    request: Request,
    principal: InspectorDep,
) -> ClientResponse:
    if principal.role not in ADMIN_ROLES:
        raise AuthorizationError("Not authorized to access this client.")
    if not principal.user_id:
        raise AuthorizationError("Not authorized to access this resource.")
    name = body.name.strip()
    if not name:
        raise ValidationFailed("name is required.")
    directory = request.app.state.client_directory
    record = directory.create(
        name=name,
        owner_user_id=principal.user_id,
        owner_role=principal.role,
    )
    grant_created_company(
        request.app.state.identity_store,
        user_id=principal.user_id,
        record=record,
        role=principal.role,
    )
    return _to_response(record)


@client_router.post(
    "/clients/{client_id}/users",
    response_model=ClientUserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Provision a client-portal login for a company",
    description=(
        "Creates one app_user with an operator-chosen username and password "
        "and a single client-role membership on the target company. Stores a "
        "password hash only. Does not generate credentials, grant publisher "
        "access, or return the password."
    ),
    tags=["Companies"],
)
def create_client_user(
    client_id: UUID,
    body: ClientUserCreateRequest,
    request: Request,
    principal: InspectorDep,
) -> ClientUserResponse:
    if principal.role not in ADMIN_ROLES:
        raise AuthorizationError("Not authorized to access this client.")
    target = str(client_id)
    if target not in _allowed_ids(request, principal):
        raise AuthorizationError("Not authorized to access this client.")
    username = body.username.strip()
    if not username:
        raise ValidationFailed("username is required.")
    if body.role != "client":
        raise ValidationFailed("Only role=client can be provisioned.")
    directory = request.app.state.client_directory
    if directory.get(target) is None:
        raise NotFoundError("Company not found.")
    require_company_active(directory, target)
    store = request.app.state.identity_store
    settings = request.app.state.settings
    validate_new_account(settings, username=username, password=body.password)
    try:
        identity = store.create_client_user(
            subject=username,
            password=body.password,
            client_id=target,
            iterations=settings.dfip_password_pbkdf2_iterations,
        )
    except DuplicateSubjectError as exc:
        raise ValidationFailed(str(exc)) from exc
    return ClientUserResponse(
        user_id=identity.user_id,
        username=identity.subject,
        client_id=target,
        role="client",
    )


@client_router.post(
    "/clients/{client_id}/rename",
    response_model=ClientResponse,
    summary="Rename a company display name",
    description=(
        "Updates client.name only. client.id and client.code are unchanged. "
        "Does not rewrite publications, facts, memberships, or Excel identity. "
        "The caller must have an inspector membership on the target client."
    ),
    tags=["Companies"],
)
def rename_client(
    client_id: UUID,
    body: ClientRenameRequest,
    request: Request,
    principal: InspectorDep,
) -> ClientResponse:
    target = str(client_id)
    if target not in _allowed_ids(request, principal):
        raise AuthorizationError("Not authorized to access this client.")
    name = body.name.strip()
    if not name:
        raise ValidationFailed("name is required.")
    directory = request.app.state.client_directory
    record = directory.rename(target, name)
    if record is None:
        raise NotFoundError("Company not found.")
    apply_name_to_identity_store(request.app.state.identity_store, target, name)
    return _to_response(record)


@client_router.post(
    "/clients/{client_id}/deactivate",
    response_model=ClientResponse,
    summary="Deactivate a company",
    description=(
        "Sets lifecycle_status=inactive. Blocks new live operations. "
        "Does not delete data, memberships, publication_current, or archives. "
        "Does not cancel in-flight processing. Permanent delete is a separate "
        "authorized action after deactivation."
    ),
    tags=["Companies"],
)
def deactivate_client(
    client_id: UUID,
    request: Request,
    principal: InspectorDep,
) -> ClientResponse:
    target = str(client_id)
    if target not in _allowed_ids(request, principal):
        raise AuthorizationError("Not authorized to access this client.")
    directory = request.app.state.client_directory
    current = directory.get(target)
    if current is None:
        raise NotFoundError("Company not found.")
    if current.lifecycle_status == LIFECYCLE_INACTIVE:
        return _to_response(current)
    now = datetime.now(tz=UTC)
    record = directory.set_lifecycle(
        target,
        lifecycle_status=LIFECYCLE_INACTIVE,
        deactivated_at=now,
        purge_eligible_after=purge_eligible_after_for(request.app.state.settings, now),
    )
    if record is None:
        raise NotFoundError("Company not found.")
    return _to_response(record)


@client_router.post(
    "/clients/{client_id}/reactivate",
    response_model=ClientResponse,
    summary="Reactivate a company",
    description=(
        "Sets lifecycle_status=active and clears deactivation timestamps. "
        "Existing memberships, publications, catalogs, and archives are unchanged."
    ),
    tags=["Companies"],
)
def reactivate_client(
    client_id: UUID,
    request: Request,
    principal: InspectorDep,
) -> ClientResponse:
    target = str(client_id)
    if target not in _allowed_ids(request, principal):
        raise AuthorizationError("Not authorized to access this client.")
    directory = request.app.state.client_directory
    current = directory.get(target)
    if current is None:
        raise NotFoundError("Company not found.")
    if current.lifecycle_status == LIFECYCLE_ACTIVE:
        return _to_response(current)
    record = directory.set_lifecycle(
        target,
        lifecycle_status=LIFECYCLE_ACTIVE,
        deactivated_at=None,
        purge_eligible_after=None,
    )
    if record is None:
        raise NotFoundError("Company not found.")
    return _to_response(record)


@client_router.delete(
    "/clients/{client_id}",
    response_model=ClientDeleteResponse,
    summary="Permanently delete a deactivated company",
    description=(
        "Permanently removes the company and all company-owned application "
        "data. The company must already be inactive. In-flight processing or "
        "publishing is refused. Idempotent when the company is already gone. "
        "The packaged default company cannot be deleted."
    ),
    tags=["Companies"],
)
def delete_client(
    client_id: UUID,
    request: Request,
    principal: InspectorDep,
) -> ClientDeleteResponse:
    return delete_company(request, principal, str(client_id))
