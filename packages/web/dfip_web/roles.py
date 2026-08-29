"""UI authorization helpers. API route authorization is application-level, not RLS."""

from __future__ import annotations

ADMIN_ROLES = frozenset({"admin", "publisher"})


def can_access_admin(role: str | None) -> bool:
    return (role or "") in ADMIN_ROLES


def can_access_client(role: str | None) -> bool:
    """Any authenticated principal may open the client surface."""
    return bool(role)
