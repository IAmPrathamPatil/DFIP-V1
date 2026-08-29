"""API role helpers for publication writes and working-set inspection.

This is application-level authorization, not PostgreSQL RLS and not
production tenant isolation. It is not a production identity provider.
"""

from __future__ import annotations

ALLOWED_ROLES = frozenset({"admin", "publisher", "reader", "client"})
ADMIN_ROLES = frozenset({"admin", "publisher"})


def can_publish(role: str | None) -> bool:
    return (role or "") in ADMIN_ROLES


def can_inspect(role: str | None) -> bool:
    """Admin/publisher may inspect the working set and staging metadata."""
    return (role or "") in ADMIN_ROLES
