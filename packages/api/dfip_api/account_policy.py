"""Account-creation policy for publisher setup and client-user provisioning.

Does not change login, password hashing, or the RUN 004B-4 account model.
"""

from __future__ import annotations

from dfip_config.settings import Settings
from dfip_db.local_demo_guard import (
    DEMO_CLIENT_2_SUBJECT,
    DEMO_CLIENT_SUBJECT,
    DEMO_PUBLISHER_2_SUBJECT,
    DEMO_PUBLISHER_SUBJECT,
)

from dfip_api.errors import ValidationFailed

MIN_PASSWORD_LENGTH = 12

DEMO_SUBJECTS = frozenset(
    {
        DEMO_PUBLISHER_SUBJECT,
        DEMO_CLIENT_SUBJECT,
        DEMO_PUBLISHER_2_SUBJECT,
        DEMO_CLIENT_2_SUBJECT,
        "demo-reader",
    }
)

# Deterministic repository/demo fixture passwords. Compared case-insensitively.
FORBIDDEN_PRODUCTION_PASSWORDS = frozenset(
    {
        "password",
        "password123",
        "changeme",
        "demo",
        "local-publisher-pass",
        "local-client-pass",
        "local-publisher2-pass",
        "local-client2-pass",
        "ops-publisher-pass",
        "alpha-portal-pass",
        "gamma-portal-pass",
        "local-reader-pass",
        "local-solo-pass",
    }
)


def is_demo_subject(subject: str) -> bool:
    name = subject.strip().lower()
    if not name:
        return False
    if name in DEMO_SUBJECTS:
        return True
    return name.startswith("demo-")


def validate_new_account(settings: Settings, *, username: str, password: str) -> None:
    """Refuse weak or demo identities at creation time. Login does not use this."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValidationFailed("password does not meet the minimum length.")
    if not settings.is_production_grade:
        return
    if is_demo_subject(username):
        raise ValidationFailed("This username is reserved.")
    if password.strip().lower() in FORBIDDEN_PRODUCTION_PASSWORDS:
        raise ValidationFailed("password is not allowed.")
