"""Single active Publisher session / lease.

The website bearer JWT is shared by every tab and every browser signed into the
same account, so it cannot identify one Publisher browser instance. This module
adds that missing concept without inventing a second user identity: the session
is keyed on the existing ``app_user`` id carried by ``Principal.user_id``, and
the identifier is an opaque 256-bit token containing no user information.

Enforcement is server-side. The browser sends the id in
``X-DFIP-Publisher-Session`` on every authenticated request; a Publisher whose
session is no longer the active one is rejected before any route body runs.

Client/reader roles are deliberately not tracked here, so an unlimited number of
Client users can be active at the same time.

dev_token mode and claim-only test JWTs have no ``Principal.user_id``. They are
development/test-only identities (production startup requires jwt mode) and are
skipped rather than being given a second identity system.

Takeover contract
-----------------
``POST /auth/publisher-session`` decides from the presented session id, so a
replaced tab can never win a takeover fight against the session that replaced it:

* **No id presented** -> a new browser instance. Registers a new session and
  revokes the previous one. This is the intended "newest wins" policy.
* **The currently active id presented** -> a continuing instance (for example a
  page reload that still holds the id). The same session is renewed.
* **Any other id presented** -> the caller has been replaced. Refused with
  PUBLISHER_SESSION_REPLACED. It does not get to take the session back.

Because of the last rule, a replaced tab re-establishing can never displace the
live session, so the two tabs cannot ping-pong. Callers must treat
PUBLISHER_SESSION_REPLACED as terminal for that page and must not retry.

FRONTEND CONTRACT: a Publisher page generates its session id in memory for that
page load and does not persist it to localStorage/sessionStorage. A duplicated
tab therefore presents no id and correctly takes over, while a page that still
holds a stale id is correctly refused. Persisting the id would make a
duplicated tab look like the original.
"""

from __future__ import annotations

import secrets
import threading
from datetime import UTC, datetime, timedelta
from typing import Protocol

from dfip_config.settings import Settings
from dfip_db.connection import DatabaseUnavailableError
from dfip_db.publisher_session import (
    PublisherSession,
    advisory_lock_key,
    fetch_active_session,
    fetch_session,
    register_session,
    renew_session,
    revoke_active_session_for_user,
)
from psycopg_pool import ConnectionPool

from dfip_api.auth import Principal
from dfip_api.errors import (
    AuthorizationError,
    PersistenceUnavailableError,
    PublisherSessionReplacedError,
)
from dfip_api.roles import can_inspect

PUBLISHER_SESSION_HEADER = "X-DFIP-Publisher-Session"
REPLACED_MESSAGE = "Publisher session opened elsewhere. This session is no longer active."
_NOT_PUBLISHER = "Not authorized to access this resource."

# 32 random bytes -> 256 bits, URL-safe. The token is a bearer credential for
# the publisher session, so it must not be derived from the user id, the
# subject, a timestamp, or a counter.
SESSION_ID_BYTES = 32
_MAX_USER_AGENT = 255


def new_session_id() -> str:
    return secrets.token_urlsafe(SESSION_ID_BYTES)


def lease_seconds(settings: Settings) -> int:
    return max(1, int(settings.dfip_publisher_session_lease_seconds))


def heartbeat_seconds(settings: Settings) -> int:
    configured = max(1, int(settings.dfip_publisher_session_heartbeat_seconds))
    return min(configured, lease_seconds(settings))


class PublisherSessionStore(Protocol):
    """Persistence seam for the publisher lease.

    Every method takes an optional ``now`` so lease expiry is exercisable
    through this interface rather than only against a concrete store.
    """

    def register(
        self,
        *,
        user_id: str,
        session_id: str,
        lease_seconds: int,
        user_agent: str | None = None,
        now: datetime | None = None,
    ) -> PublisherSession: ...

    def get_active(
        self, session_id: str, *, now: datetime | None = None
    ) -> PublisherSession | None: ...

    def renew(
        self,
        *,
        session_id: str,
        user_id: str,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> PublisherSession | None: ...

    def revoke_active_for_user(self, user_id: str) -> int: ...


class InMemoryPublisherSessionStore:
    """Test/local lease. One lock makes register and renew atomic."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_id: dict[str, PublisherSession] = {}

    def register(
        self,
        *,
        user_id: str,
        session_id: str,
        lease_seconds: int,
        user_agent: str | None = None,
        now: datetime | None = None,
    ) -> PublisherSession:
        moment = now if now is not None else _utcnow()
        expires_at = moment + timedelta(seconds=max(1, int(lease_seconds)))
        with self._lock:
            revoked = [key for key, item in self._by_id.items() if _is_claimable(item, user_id)]
            for key in revoked:
                self._by_id[key] = _with_revoked(self._by_id[key], moment)
            session = PublisherSession(
                session_id=session_id,
                user_id=user_id,
                created_at=moment,
                last_seen_at=moment,
                expires_at=expires_at,
                revoked_at=None,
                user_agent=user_agent,
            )
            self._by_id[session_id] = session
        return session

    def get_active(
        self, session_id: str, *, now: datetime | None = None
    ) -> PublisherSession | None:
        moment = now if now is not None else _utcnow()
        with self._lock:
            session = self._by_id.get(session_id)
        if session is None or not session.is_active_at(moment):
            return None
        return session

    def get(self, session_id: str) -> PublisherSession | None:
        with self._lock:
            return self._by_id.get(session_id)

    def renew(
        self,
        *,
        session_id: str,
        user_id: str,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> PublisherSession | None:
        moment = now if now is not None else _utcnow()
        with self._lock:
            session = self._by_id.get(session_id)
            if session is None or session.user_id != user_id:
                return None
            if not session.is_active_at(moment):
                return None
            renewed = PublisherSession(
                session_id=session.session_id,
                user_id=session.user_id,
                created_at=session.created_at,
                last_seen_at=moment,
                expires_at=moment + timedelta(seconds=max(1, int(lease_seconds))),
                revoked_at=None,
                user_agent=session.user_agent,
            )
            self._by_id[session_id] = renewed
        return renewed

    def revoke_active_for_user(self, user_id: str) -> int:
        now = _utcnow()
        with self._lock:
            keys = [key for key, item in self._by_id.items() if _is_claimable(item, user_id)]
            for key in keys:
                self._by_id[key] = _with_revoked(self._by_id[key], now)
            return len(keys)

    def active_for_user(
        self, user_id: str, *, now: datetime | None = None
    ) -> PublisherSession | None:
        moment = now if now is not None else _utcnow()
        with self._lock:
            live = [
                item
                for item in self._by_id.values()
                if item.user_id == user_id and item.is_active_at(moment)
            ]
        return live[0] if live else None


class PostgresPublisherSessionStore:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def register(
        self,
        *,
        user_id: str,
        session_id: str,
        lease_seconds: int,
        user_agent: str | None = None,
        now: datetime | None = None,
    ) -> PublisherSession:
        return register_session(
            self._pool,
            user_id=user_id,
            session_id=session_id,
            lease_seconds=lease_seconds,
            user_agent=user_agent,
            now=now,
        )

    def get_active(
        self, session_id: str, *, now: datetime | None = None
    ) -> PublisherSession | None:
        return fetch_active_session(self._pool, session_id, now=now)

    def get(self, session_id: str) -> PublisherSession | None:
        return fetch_session(self._pool, session_id)

    def renew(
        self,
        *,
        session_id: str,
        user_id: str,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> PublisherSession | None:
        return renew_session(
            self._pool,
            session_id=session_id,
            user_id=user_id,
            lease_seconds=lease_seconds,
            now=now,
        )

    def revoke_active_for_user(self, user_id: str) -> int:
        return revoke_active_session_for_user(self._pool, user_id)


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


def _is_claimable(session: PublisherSession, user_id: str) -> bool:
    """An unrevoked row for this user still occupies the one-active slot.

    Expired-but-unrevoked rows count, so registration must clear them; that is
    what stops a lapsed lease from silently becoming active again.
    """
    return session.user_id == user_id and session.revoked_at is None


def _with_revoked(session: PublisherSession, moment: datetime) -> PublisherSession:
    return PublisherSession(
        session_id=session.session_id,
        user_id=session.user_id,
        created_at=session.created_at,
        last_seen_at=session.last_seen_at,
        expires_at=session.expires_at,
        revoked_at=moment,
        user_agent=session.user_agent,
    )


def requires_publisher_session(principal: Principal) -> bool:
    """True when this caller must present a live Publisher session.

    Only admin/publisher with a real ``app_user`` identity. Client and reader
    are never subject to this check, and dev_token / claim-only test identities
    have no user_id to key a session on.
    """
    return can_inspect(principal.role) and bool(str(principal.user_id or "").strip())


def _require_store(store: PublisherSessionStore | None) -> PublisherSessionStore:
    """Fail closed: a Publisher is never allowed through without a store.

    Enforcement disappearing must be an outage, not a silent bypass.
    """
    if store is None:
        raise PersistenceUnavailableError()
    return store


def active_publisher_session(
    store: PublisherSessionStore | None,
    principal: Principal,
    session_id: str | None,
    *,
    now: datetime | None = None,
) -> PublisherSession | None:
    """Return the caller's live session, or None. Never raises on a bad id."""
    if store is None or not requires_publisher_session(principal):
        return None
    candidate = str(session_id or "").strip()
    if not candidate:
        return None
    session = store.get_active(candidate, now=now)
    if session is None or session.user_id != principal.user_id:
        return None
    return session


def enforce_publisher_session(
    store: PublisherSessionStore | None,
    principal: Principal,
    session_id: str | None,
    *,
    now: datetime | None = None,
) -> None:
    """Reject a Publisher that is not the currently active session.

    Fails closed: a missing store, an absent id, an unknown id, a foreign id, a
    replaced id, and an expired lease are all refused. The first is an outage
    (503), the rest are one indistinguishable 401 so this endpoint cannot be
    used to probe which session ids exist.
    """
    if not requires_publisher_session(principal):
        return
    resolved = _require_store(store)
    if active_publisher_session(resolved, principal, session_id, now=now) is None:
        raise PublisherSessionReplacedError(REPLACED_MESSAGE)


def register_publisher_session(
    store: PublisherSessionStore | None,
    settings: Settings,
    principal: Principal,
    *,
    presented_session_id: str | None = None,
    user_agent: str | None = None,
    now: datetime | None = None,
) -> PublisherSession:
    """Establish the caller's active session.

    See the module takeover contract. A caller presenting a stale id is refused
    rather than allowed to take the session back, so a replaced tab cannot
    fight the session that replaced it.
    """
    if not requires_publisher_session(principal):
        raise AuthorizationError(_NOT_PUBLISHER)
    resolved = _require_store(store)
    user_id = str(principal.user_id)
    presented = str(presented_session_id or "").strip()
    if presented:
        current = resolved.get_active(presented, now=now)
        if current is None or current.user_id != user_id:
            raise PublisherSessionReplacedError(REPLACED_MESSAGE)
        renewed = resolved.renew(
            session_id=presented,
            user_id=user_id,
            lease_seconds=lease_seconds(settings),
            now=now,
        )
        if renewed is None:
            raise PublisherSessionReplacedError(REPLACED_MESSAGE)
        return renewed
    session_id = new_session_id()
    try:
        return resolved.register(
            user_id=user_id,
            session_id=session_id,
            lease_seconds=lease_seconds(settings),
            user_agent=_safe_user_agent(user_agent),
            now=now,
        )
    except DatabaseUnavailableError:
        raise
    except Exception as exc:
        raise PersistenceUnavailableError() from exc


def renew_publisher_session(
    store: PublisherSessionStore | None,
    settings: Settings,
    principal: Principal,
    session_id: str | None,
    *,
    now: datetime | None = None,
) -> PublisherSession:
    """Extend the lease for the caller's active session, or fail."""
    if not requires_publisher_session(principal):
        raise AuthorizationError(_NOT_PUBLISHER)
    resolved = _require_store(store)
    candidate = str(session_id or "").strip()
    if not candidate:
        raise PublisherSessionReplacedError(REPLACED_MESSAGE)
    renewed = resolved.renew(
        session_id=candidate,
        user_id=str(principal.user_id),
        lease_seconds=lease_seconds(settings),
        now=now,
    )
    if renewed is None:
        raise PublisherSessionReplacedError(REPLACED_MESSAGE)
    return renewed


def release_publisher_sessions(store: PublisherSessionStore | None, user_id: str | None) -> int:
    """Revoke a session on logout. Never raises; logout is always allowed."""
    if store is None or not str(user_id or "").strip():
        return 0
    try:
        return store.revoke_active_for_user(str(user_id))
    except Exception:
        return 0


def advisory_lock_key_for(user_id: str) -> int:
    """Exposed for tests that assert the lock key is stable across processes."""
    return advisory_lock_key(user_id)


def _safe_user_agent(value: str | None) -> str | None:
    """Diagnostic only. Strip control characters and cap the length."""
    raw = str(value or "").strip()
    if not raw:
        return None
    cleaned = "".join(char for char in raw if char.isprintable())
    return cleaned[:_MAX_USER_AGENT] or None
