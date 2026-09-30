"""Test support for the single-active Publisher session requirement.

A Publisher identity that resolves to a real ``app_user`` must hold a live
Publisher session before any gated route answers. Tests that log in as a
publisher and then call a Publisher route use these helpers so the session is
established the same way the browser does.

These tests are not weakened: they still assert the same behaviour, they just
also establish the session the security requirement now demands.
"""

from __future__ import annotations

from dfip_api.publisher_session import PUBLISHER_SESSION_HEADER
from fastapi.testclient import TestClient


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def open_publisher_session(http: TestClient, token: str) -> str:
    """Register this caller as the active Publisher session. Returns the id."""
    response = http.post("/api/v1/auth/publisher-session", headers=_bearer(token))
    assert response.status_code == 200, response.text
    return str(response.json()["session_id"])


def _cached_session_id(http: TestClient, token: str) -> str:
    """The caller's currently active Publisher session, establishing it if needed.

    Keyed on the publisher identity (``sub``), not the token string, because
    there is exactly one active session per publisher: a token re-issued by
    select-client is the same browser, not a new one. Re-establishing per token
    would make one test steal the session from itself.

    The cached id is re-validated before use so a session genuinely superseded
    by another caller is replaced rather than reused.
    """
    cache = getattr(http.app.state, "_test_publisher_sessions", None)
    if cache is None:
        cache = {}
        http.app.state._test_publisher_sessions = cache
    store = http.app.state.publisher_session_store
    key = _subject(token)
    cached = cache.get(key)
    if cached is not None and store.get_active(cached) is not None:
        return str(cached)
    fresh = open_publisher_session(http, token)
    cache[key] = fresh
    return fresh


def _subject(token: str) -> str:
    import jwt

    return str(jwt.decode(token, options={"verify_signature": False})["sub"])


def publisher_headers(http: TestClient, token: str) -> dict[str, str]:
    """Bearer headers plus this caller's active Publisher session id."""
    return {**_bearer(token), PUBLISHER_SESSION_HEADER: _cached_session_id(http, token)}


def with_publisher_session(
    headers: dict[str, str], http: TestClient, token: str
) -> dict[str, str]:
    return {**headers, PUBLISHER_SESSION_HEADER: _cached_session_id(http, token)}


def drop_cached_session(http: TestClient, token: str) -> None:
    """Forget a memoized id, for tests that deliberately re-establish."""
    cache = getattr(http.app.state, "_test_publisher_sessions", None)
    if cache is not None:
        cache.pop(_subject(token), None)
