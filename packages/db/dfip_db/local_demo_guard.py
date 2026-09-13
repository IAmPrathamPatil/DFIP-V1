"""Refuse local demo identity seed against hosted or production databases.

Never logs a connection string, password, or JWT secret.
"""

from __future__ import annotations

from urllib.parse import unquote, urlparse

DEFAULT_CLIENT_ID = "a0000000-0000-4000-8000-000000000001"
DEFAULT_CLIENT_CODE = "default"

COMPANY_2_CLIENT_ID = "a0000000-0000-4000-8000-000000000002"
COMPANY_2_CLIENT_CODE = "company-2"
COMPANY_2_CLIENT_NAME = "Company 2 (local disposable)"

DEMO_PUBLISHER_SUBJECT = "demo-publisher"
DEMO_CLIENT_SUBJECT = "demo-client"
DEMO_PUBLISHER_2_SUBJECT = "demo-publisher-2"
DEMO_CLIENT_2_SUBJECT = "demo-client-2"

ALLOWED_LOCAL_HOSTS = frozenset(
    {
        "127.0.0.1",
        "localhost",
        "::1",
        "host.docker.internal",
        "dfip_db",
        "postgres",
    }
)

_HOSTED_MARKERS = (
    "supabase",
    "neon.tech",
    "neon.build",
    "amazonaws.com",
    "rds.amazonaws",
    "azure.com",
    "googleusercontent",
    "cloud.google",
    "elephantsql",
    "render.com",
    "timescaledb",
)


class LocalDemoSeedError(Exception):
    """Operator-facing refusal. Message must not include secrets."""


def hostname_of(database_url: str) -> str | None:
    raw = (database_url or "").strip()
    if not raw:
        return None
    parsed = urlparse(raw)
    host = parsed.hostname
    if host:
        return host.lower()
    return None


def is_hosted_or_unsafe_database_url(database_url: str) -> bool:
    raw = (database_url or "").strip().lower()
    if not raw:
        return True
    if any(marker in raw for marker in _HOSTED_MARKERS):
        return True
    parsed = urlparse(database_url.strip())
    host = (parsed.hostname or "").lower()
    user = unquote(parsed.username or "").lower()
    if parsed.port == 6543:
        return True
    if user.startswith("postgres."):
        return True
    if not host:
        return True
    return host not in ALLOWED_LOCAL_HOSTS


def assert_local_demo_seed_allowed(
    *,
    database_url: str,
    dfip_env: str,
    seed_flag: str,
    confirmed: bool,
) -> None:
    """Raise LocalDemoSeedError unless this is an explicit disposable local seed."""
    env = (dfip_env or "").strip().lower()
    if env == "production":
        raise LocalDemoSeedError("Local demo seed refuses DFIP_ENV=production.")
    if (seed_flag or "").strip() != "1":
        raise LocalDemoSeedError(
            "Local demo seed requires DFIP_LOCAL_DEMO_SEED=1 (explicit local-only acknowledgement)."
        )
    if not confirmed:
        raise LocalDemoSeedError(
            "Local demo seed requires --confirm-local-only on the command line."
        )
    url = (database_url or "").strip()
    if not url:
        raise LocalDemoSeedError(
            "Local demo seed requires DATABASE_URL pointing at disposable local PostgreSQL."
        )
    if is_hosted_or_unsafe_database_url(url):
        host = hostname_of(url) or "unknown"
        raise LocalDemoSeedError(
            "Local demo seed refuses this DATABASE_URL "
            f"(host={host}). Use loopback / compose dfip_db only."
        )
