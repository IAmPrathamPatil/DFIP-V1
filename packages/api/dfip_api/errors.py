"""Stable API error types and safe response bodies.

Production responses never include tracebacks, filesystem paths, secrets, or
environment values.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

VALIDATION_ERROR = "VALIDATION_ERROR"
AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
AUTHORIZATION_FAILED = "AUTHORIZATION_FAILED"
NOT_FOUND = "NOT_FOUND"
CONFLICT = "CONFLICT"
INVALID_PAGINATION = "INVALID_PAGINATION"
PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
TOO_MANY_REQUESTS = "TOO_MANY_REQUESTS"
PERSISTENCE_UNAVAILABLE = "PERSISTENCE_UNAVAILABLE"
INTERNAL_ERROR = "INTERNAL_ERROR"
METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"

log = logging.getLogger(__name__)


def safe_route(request: Request) -> str:
    """Method + path only. Never query strings, headers, or bodies."""
    return f"{request.method} {request.url.path}"


class ApiError(Exception):
    """An error that can be rendered as a public API response."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


class AuthenticationError(ApiError):
    def __init__(self, message: str = "Authentication required.") -> None:
        super().__init__(401, AUTHENTICATION_FAILED, message)


class AuthorizationError(ApiError):
    def __init__(self, message: str = "Not authorized to access this resource.") -> None:
        super().__init__(403, AUTHORIZATION_FAILED, message)


class NotFoundError(ApiError):
    def __init__(self, message: str = "Resource not found.") -> None:
        super().__init__(404, NOT_FOUND, message)


class ConflictError(ApiError):
    def __init__(self, message: str = "Resource already exists.") -> None:
        super().__init__(409, CONFLICT, message)


class PersistenceUnavailableError(ApiError):
    def __init__(self, message: str = "Persistence is unavailable.") -> None:
        super().__init__(503, PERSISTENCE_UNAVAILABLE, message)


class ValidationFailed(ApiError):
    def __init__(
        self,
        message: str = "Invalid request.",
        details: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(422, VALIDATION_ERROR, message, details)


class PayloadTooLarge(ApiError):
    def __init__(self, message: str = "Workbook exceeds the maximum allowed size.") -> None:
        super().__init__(413, PAYLOAD_TOO_LARGE, message)


class TooManyRequests(ApiError):
    def __init__(self, message: str = "Too many requests.") -> None:
        super().__init__(429, TOO_MANY_REQUESTS, message)


class AuthConfigurationError(Exception):
    """Raised at startup when authentication is misconfigured. Not an HTTP body."""


class PersistenceConfigurationError(Exception):
    """Raised at startup when production persistence is misconfigured. Not an HTTP body."""


def error_body(
    code: str,
    message: str,
    details: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"error": {"code": code, "message": message}}
    if details:
        payload["error"]["details"] = details
    return payload


def error_response(
    status_code: int,
    code: str,
    message: str,
    details: list[dict[str, Any]] | None = None,
) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=error_body(code, message, details))


def authorization_error_response(
    message: str = "Not authorized to access this resource.",
) -> JSONResponse:
    return error_response(403, AUTHORIZATION_FAILED, message)


def _http_code_for_status(status_code: int) -> str:
    if status_code == 401:
        return AUTHENTICATION_FAILED
    if status_code == 403:
        return AUTHORIZATION_FAILED
    if status_code == 404:
        return NOT_FOUND
    if status_code == 409:
        return CONFLICT
    if status_code == 405:
        return METHOD_NOT_ALLOWED
    if status_code == 413:
        return PAYLOAD_TOO_LARGE
    if status_code == 429:
        return TOO_MANY_REQUESTS
    if status_code == 422:
        return VALIDATION_ERROR
    if status_code == 503:
        return PERSISTENCE_UNAVAILABLE
    return INTERNAL_ERROR


def _safe_http_message(status_code: int, detail: Any) -> str:
    if status_code == 404:
        return "Not found."
    if status_code == 405:
        return "Method not allowed."
    if isinstance(detail, str) and detail and "traceback" not in detail.lower():
        if "\\" not in detail and "/" not in detail:
            return detail
    return "Request failed."


async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    if exc.code == PERSISTENCE_UNAVAILABLE:
        log.error(
            "persistence-unavailable type=%s route=%s",
            type(exc).__name__,
            safe_route(request),
        )
    if exc.code == PAYLOAD_TOO_LARGE:
        log.warning("payload-too-large route=%s", safe_route(request))
    if exc.code == TOO_MANY_REQUESTS:
        log.warning("rate-limited route=%s", safe_route(request))
    return error_response(exc.status_code, exc.code, exc.message, exc.details)


async def validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    details: list[dict[str, Any]] = []
    pagination_problem = False
    for error in exc.errors():
        loc = [str(part) for part in error.get("loc", ())]
        details.append(
            {
                "loc": loc,
                "msg": error.get("msg", "Invalid value."),
                "type": error.get("type", "value_error"),
            }
        )
        if "limit" in loc or "offset" in loc:
            pagination_problem = True
    code = INVALID_PAGINATION if pagination_problem else VALIDATION_ERROR
    message = "Invalid pagination." if pagination_problem else "Invalid request."
    return error_response(422, code, message, details)


async def http_exception_handler(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return error_response(
        exc.status_code,
        _http_code_for_status(exc.status_code),
        _safe_http_message(exc.status_code, exc.detail),
    )


async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
    log.error("unexpected-error type=%s route=%s", type(exc).__name__, safe_route(request))
    return error_response(500, INTERNAL_ERROR, "An unexpected error occurred.")
