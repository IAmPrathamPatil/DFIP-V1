"""D9 saved analytical workspace state.

Persists allowlisted Overview configuration for one authenticated subject
inside one JWT-bound company. Does not store fact rows. Does not share
across tenants or users.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

from dfip_analytics.workspace import WorkspaceStateError, parse_workspace_state
from dfip_db.saved_analysis import (
    SavedAnalysisRecord,
    count_saved_analyses,
    delete_saved_analysis,
    fetch_saved_analysis,
    insert_saved_analysis,
    list_saved_analyses,
    purge_saved_analyses_for_client,
    update_saved_analysis,
)
from psycopg_pool import ConnectionPool

from dfip_api.auth import Principal
from dfip_api.errors import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    ValidationFailed,
)
from dfip_api.lifecycle import require_company_active

MAX_SAVED_ANALYSES = 50
MAX_TITLE_CHARS = 80
UNBOUND_MESSAGE = "Select a company before using saved analysis."


class SavedAnalysisStore(Protocol):
    def create(
        self,
        *,
        owner_subject: str,
        user_id: str | None,
        client_id: str,
        title: str,
        state: dict[str, Any],
    ) -> SavedAnalysisRecord: ...

    def list(self, *, owner_subject: str, client_id: str) -> tuple[SavedAnalysisRecord, ...]: ...

    def count(self, *, owner_subject: str, client_id: str) -> int: ...

    def get(
        self, *, analysis_id: str, owner_subject: str, client_id: str
    ) -> SavedAnalysisRecord | None: ...

    def update(
        self,
        *,
        analysis_id: str,
        owner_subject: str,
        client_id: str,
        title: str | None,
        state: dict[str, Any] | None,
    ) -> SavedAnalysisRecord | None: ...

    def delete(self, *, analysis_id: str, owner_subject: str, client_id: str) -> bool: ...

    def purge_client(self, client_id: str) -> None: ...


class InMemorySavedAnalysisStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_id: dict[str, SavedAnalysisRecord] = {}

    def create(
        self,
        *,
        owner_subject: str,
        user_id: str | None,
        client_id: str,
        title: str,
        state: dict[str, Any],
    ) -> SavedAnalysisRecord:
        now = datetime.now(tz=UTC)
        record = SavedAnalysisRecord(
            analysis_id=str(uuid4()),
            owner_subject=owner_subject,
            user_id=user_id,
            client_id=client_id,
            title=title,
            state=dict(state),
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._by_id[record.analysis_id] = record
        return record

    def list(self, *, owner_subject: str, client_id: str) -> tuple[SavedAnalysisRecord, ...]:
        with self._lock:
            items = [
                item
                for item in self._by_id.values()
                if item.owner_subject == owner_subject and item.client_id == client_id
            ]
        items.sort(key=lambda item: (item.updated_at, item.analysis_id), reverse=True)
        return tuple(items)

    def count(self, *, owner_subject: str, client_id: str) -> int:
        return len(self.list(owner_subject=owner_subject, client_id=client_id))

    def get(
        self, *, analysis_id: str, owner_subject: str, client_id: str
    ) -> SavedAnalysisRecord | None:
        with self._lock:
            record = self._by_id.get(analysis_id)
        if record is None or record.owner_subject != owner_subject or record.client_id != client_id:
            return None
        return record

    def update(
        self,
        *,
        analysis_id: str,
        owner_subject: str,
        client_id: str,
        title: str | None,
        state: dict[str, Any] | None,
    ) -> SavedAnalysisRecord | None:
        current = self.get(
            analysis_id=analysis_id, owner_subject=owner_subject, client_id=client_id
        )
        if current is None:
            return None
        record = SavedAnalysisRecord(
            analysis_id=current.analysis_id,
            owner_subject=current.owner_subject,
            user_id=current.user_id,
            client_id=current.client_id,
            title=current.title if title is None else title,
            state=current.state if state is None else dict(state),
            created_at=current.created_at,
            updated_at=datetime.now(tz=UTC),
        )
        with self._lock:
            self._by_id[analysis_id] = record
        return record

    def delete(self, *, analysis_id: str, owner_subject: str, client_id: str) -> bool:
        current = self.get(
            analysis_id=analysis_id, owner_subject=owner_subject, client_id=client_id
        )
        if current is None:
            return False
        with self._lock:
            self._by_id.pop(analysis_id, None)
        return True

    def purge_client(self, client_id: str) -> None:
        with self._lock:
            self._by_id = {
                key: item for key, item in self._by_id.items() if item.client_id != client_id
            }


class PostgresSavedAnalysisStore:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def create(
        self,
        *,
        owner_subject: str,
        user_id: str | None,
        client_id: str,
        title: str,
        state: dict[str, Any],
    ) -> SavedAnalysisRecord:
        return insert_saved_analysis(
            self._pool,
            owner_subject=owner_subject,
            user_id=user_id,
            client_id=client_id,
            title=title,
            state=state,
        )

    def list(self, *, owner_subject: str, client_id: str) -> tuple[SavedAnalysisRecord, ...]:
        return list_saved_analyses(self._pool, owner_subject=owner_subject, client_id=client_id)

    def count(self, *, owner_subject: str, client_id: str) -> int:
        return count_saved_analyses(self._pool, owner_subject=owner_subject, client_id=client_id)

    def get(
        self, *, analysis_id: str, owner_subject: str, client_id: str
    ) -> SavedAnalysisRecord | None:
        return fetch_saved_analysis(
            self._pool,
            analysis_id=analysis_id,
            owner_subject=owner_subject,
            client_id=client_id,
        )

    def update(
        self,
        *,
        analysis_id: str,
        owner_subject: str,
        client_id: str,
        title: str | None,
        state: dict[str, Any] | None,
    ) -> SavedAnalysisRecord | None:
        return update_saved_analysis(
            self._pool,
            analysis_id=analysis_id,
            owner_subject=owner_subject,
            client_id=client_id,
            title=title,
            state=state,
        )

    def delete(self, *, analysis_id: str, owner_subject: str, client_id: str) -> bool:
        return delete_saved_analysis(
            self._pool,
            analysis_id=analysis_id,
            owner_subject=owner_subject,
            client_id=client_id,
        )

    def purge_client(self, client_id: str) -> None:
        purge_saved_analyses_for_client(self._pool, client_id)


@dataclass(frozen=True)
class SavedAnalysisService:
    store: SavedAnalysisStore
    clients: Any = None

    def _scope(self, principal: Principal) -> tuple[str, str]:
        client_id = principal.client_id
        if not client_id:
            raise AuthorizationError(UNBOUND_MESSAGE)
        require_company_active(self.clients, client_id)
        subject = (principal.subject or "").strip()
        if not subject:
            raise AuthorizationError()
        return subject, client_id

    def create(self, principal: Principal, *, title: str, state: dict[str, Any] | None):
        owner, client_id = self._scope(principal)
        clean_title = _parse_title(title)
        clean_state = _parse_state(state)
        if self.store.count(owner_subject=owner, client_id=client_id) >= MAX_SAVED_ANALYSES:
            raise ConflictError("Saved analysis limit reached for this company.")
        return self.store.create(
            owner_subject=owner,
            user_id=_optional_user_id(principal.user_id),
            client_id=client_id,
            title=clean_title,
            state=clean_state,
        )

    def list(self, principal: Principal):
        owner, client_id = self._scope(principal)
        return self.store.list(owner_subject=owner, client_id=client_id)

    def get(self, principal: Principal, analysis_id: str):
        owner, client_id = self._scope(principal)
        record = self.store.get(analysis_id=analysis_id, owner_subject=owner, client_id=client_id)
        if record is None:
            raise NotFoundError("Saved analysis not found.")
        return record

    def update(
        self,
        principal: Principal,
        analysis_id: str,
        *,
        title: str | None,
        state: dict[str, Any] | None,
    ):
        owner, client_id = self._scope(principal)
        next_title = _parse_title(title) if title is not None else None
        next_state = _parse_state(state) if state is not None else None
        if next_title is None and next_state is None:
            raise ValidationFailed("Provide a title or state to update.")
        record = self.store.update(
            analysis_id=analysis_id,
            owner_subject=owner,
            client_id=client_id,
            title=next_title,
            state=next_state,
        )
        if record is None:
            raise NotFoundError("Saved analysis not found.")
        return record

    def delete(self, principal: Principal, analysis_id: str) -> None:
        owner, client_id = self._scope(principal)
        if not self.store.delete(analysis_id=analysis_id, owner_subject=owner, client_id=client_id):
            raise NotFoundError("Saved analysis not found.")


def _parse_title(title: str | None) -> str:
    text = (title or "").strip()
    if not text:
        raise ValidationFailed("Title is required.")
    if len(text) > MAX_TITLE_CHARS:
        raise ValidationFailed("Title is too long.")
    if "\x00" in text:
        raise ValidationFailed("Title is invalid.")
    return text


def _parse_state(state: dict[str, Any] | None) -> dict[str, Any]:
    try:
        return parse_workspace_state(state or {})
    except WorkspaceStateError as exc:
        raise ValidationFailed(str(exc)) from exc


def _optional_user_id(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return str(UUID(value))
    except ValueError:
        return None
