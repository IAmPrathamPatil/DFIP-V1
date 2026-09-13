"""Process-local V1 request limits.

Failed-attempt windows and JSON body caps are in-memory only. Distributed
limiting is not implemented. X-Forwarded-For is trusted only when the direct
TCP peer is loopback (the V1 Caddy reverse proxy on this host).
"""

from __future__ import annotations

import ipaddress
import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from fastapi import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from dfip_api.errors import PAYLOAD_TOO_LARGE, error_response

LOGIN_MAX_FAILURES = 5
LOGIN_WINDOW_SECONDS = 600
JSON_BODY_TOO_LARGE = "Request body exceeds the maximum allowed size."
log = logging.getLogger(__name__)


class AttemptLimiter:
    """Sliding-window failed-attempt counter. Process-local; not a lockout."""

    def __init__(
        self,
        *,
        max_failures: int = LOGIN_MAX_FAILURES,
        window_seconds: float = LOGIN_WINDOW_SECONDS,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self._clock = clock if clock is not None else time.monotonic
        self._lock = threading.Lock()
        self._events: dict[str, list[float]] = {}

    def _prune_unlocked(self, key: str, now: float) -> list[float]:
        cutoff = now - self.window_seconds
        events = [stamp for stamp in self._events.get(key, ()) if stamp > cutoff]
        if events:
            self._events[key] = events
        else:
            self._events.pop(key, None)
        return events

    def is_blocked(self, *keys: str) -> bool:
        now = self._clock()
        with self._lock:
            for key in keys:
                if not key:
                    continue
                if len(self._prune_unlocked(key, now)) >= self.max_failures:
                    return True
            return False

    def record_failure(self, *keys: str) -> None:
        now = self._clock()
        with self._lock:
            for key in keys:
                if not key:
                    continue
                events = self._prune_unlocked(key, now)
                events.append(now)
                self._events[key] = events

    def clear(self, *keys: str) -> None:
        with self._lock:
            for key in keys:
                self._events.pop(key, None)


def _is_loopback_peer(host: str) -> bool:
    lowered = host.strip().lower()
    if lowered in {"localhost", "::ffff:127.0.0.1"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _forwarded_client_ip(request: Request) -> str | None:
    raw = request.headers.get("x-forwarded-for") or ""
    parts = [item.strip() for item in raw.split(",") if item.strip()]
    if not parts:
        return None
    candidate = parts[-1]
    if candidate.startswith("[") and "]" in candidate:
        candidate = candidate[1 : candidate.index("]")]
    elif candidate.count(":") == 1 and "." in candidate:
        candidate = candidate.rsplit(":", 1)[0]
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return None
    return candidate


def request_peer_ip(request: Request) -> str:
    """Direct ASGI peer, or Caddy's client IP when the peer is loopback.

    Untrusted clients cannot spoof X-Forwarded-For: the header is ignored
    unless the TCP peer is loopback. The rightmost forwarded address is used
    so a client-supplied prefix cannot replace the address Caddy observed.
    """
    client = request.client
    if client is None or not client.host:
        return "unknown"
    peer = client.host
    if _is_loopback_peer(peer):
        forwarded = _forwarded_client_ip(request)
        if forwarded:
            return forwarded
    return peer


def normalize_username(username: str) -> str:
    return username.strip().casefold()


def login_attempt_keys(request: Request, username: str) -> tuple[str, str]:
    peer = request_peer_ip(request)
    return f"login:ip:{peer}", f"login:user:{normalize_username(username)}"


def bootstrap_attempt_keys(request: Request, username: str) -> tuple[str, str]:
    peer = request_peer_ip(request)
    return f"bootstrap:ip:{peer}", f"bootstrap:user:{normalize_username(username)}"


def _header_map(scope: Scope) -> dict[str, str]:
    headers: dict[str, str] = {}
    for key, value in scope.get("headers") or ():
        headers[key.decode("latin-1").lower()] = value.decode("latin-1")
    return headers


def _is_multipart(content_type: str) -> bool:
    return "multipart/form-data" in content_type.lower()


def _should_limit_json_body(scope: Scope) -> bool:
    method = str(scope.get("method") or "GET").upper()
    if method in {"GET", "HEAD", "OPTIONS"}:
        return False
    path = str(scope.get("path") or "")
    return path != "/health"


async def _send_body_too_large(scope: Scope, receive: Receive, send: Send) -> None:
    path = str(scope.get("path") or "")
    method = str(scope.get("method") or "")
    log.warning("payload-too-large route=%s %s", method, path)
    response = error_response(413, PAYLOAD_TOO_LARGE, JSON_BODY_TOO_LARGE)
    await response(scope, receive, send)


class JsonBodyLimitMiddleware:
    """Cap non-multipart request bodies. Multipart workbook uploads are skipped."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if not _should_limit_json_body(scope):
            await self.app(scope, receive, send)
            return
        headers = _header_map(scope)
        if _is_multipart(headers.get("content-type", "")):
            await self.app(scope, receive, send)
            return
        declared = headers.get("content-length")
        if declared:
            try:
                length = int(declared)
            except ValueError:
                length = -1
            if length > self.max_bytes:
                await _send_body_too_large(scope, receive, send)
                return
        body = bytearray()
        while True:
            message = await receive()
            message_type = message.get("type")
            if message_type == "http.disconnect":
                return
            if message_type != "http.request":
                continue
            chunk = message.get("body", b"") or b""
            if len(body) + len(chunk) > self.max_bytes:
                while message.get("more_body"):
                    message = await receive()
                    if message.get("type") == "http.disconnect":
                        return
                await _send_body_too_large(scope, receive, send)
                return
            body.extend(chunk)
            if not message.get("more_body"):
                break

        replayed = False

        async def replay_receive() -> dict[str, Any]:
            nonlocal replayed
            if replayed:
                return {"type": "http.request", "body": b"", "more_body": False}
            replayed = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.app(scope, replay_receive, send)
