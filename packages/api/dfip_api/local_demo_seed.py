"""Explicit local-only demo identities for automated tests.

Not the product publisher-setup path. Not a migration. Not imported by
``create_app``. Operator-defined publisher setup is
``POST /api/v1/auth/setup-publisher``. Passwords come from the environment
at seed time and are never printed.

    python -m dfip_api.local_demo_seed --confirm-local-only
    python -m dfip_api.local_demo_seed --confirm-local-only --company-2
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime

from dfip_config.settings import Settings
from dfip_db.local_demo_guard import (
    COMPANY_2_CLIENT_CODE,
    COMPANY_2_CLIENT_ID,
    COMPANY_2_CLIENT_NAME,
    DEFAULT_CLIENT_ID,
    DEMO_CLIENT_2_SUBJECT,
    DEMO_CLIENT_SUBJECT,
    DEMO_PUBLISHER_2_SUBJECT,
    DEMO_PUBLISHER_SUBJECT,
    LocalDemoSeedError,
    assert_local_demo_seed_allowed,
)
from psycopg import connect
from psycopg.rows import dict_row

from dfip_api.password import hash_password

_SELECT_CLIENT = """
SELECT id::text AS id, code,
       COALESCE(lifecycle_status, 'active') AS lifecycle_status
FROM client
WHERE id = %s
"""

_FIRST_ACTIVE_CLIENT = """
SELECT id::text AS id, code
FROM client
WHERE COALESCE(lifecycle_status, 'active') = 'active'
ORDER BY code, id
LIMIT 1
"""

_UPSERT_USER = """
INSERT INTO app_user (
    id, subject, is_platform_admin, password_hash, token_version, created_at, updated_at
)
VALUES (gen_random_uuid(), %s, false, %s, 1, %s, %s)
ON CONFLICT (subject) DO UPDATE
SET password_hash = EXCLUDED.password_hash,
    is_platform_admin = false,
    updated_at = EXCLUDED.updated_at
RETURNING id::text AS id, subject
"""

_UPSERT_MEMBERSHIP = """
INSERT INTO client_membership (user_id, client_id, role, created_at, updated_at)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (user_id, client_id) DO UPDATE
SET role = EXCLUDED.role,
    updated_at = EXCLUDED.updated_at
"""

_UPSERT_CLIENT = """
INSERT INTO client (id, code, name)
VALUES (%s, %s, %s)
ON CONFLICT (id) DO UPDATE
SET code = EXCLUDED.code,
    name = EXCLUDED.name
"""


def _demo_client_membership_target(conn, requested_client_id: str) -> dict[str, str]:
    """Bind demo-client to an active company.

    Publisher membership stays on the requested client. When that client is
    inactive, demo-client is bound to the first active company so a local
    portal login is possible. demo-client keeps exactly one membership.
    """
    row = conn.execute(_SELECT_CLIENT, (requested_client_id,)).fetchone()
    if row is None:
        raise LocalDemoSeedError("Default client is missing. Apply migrations before seeding.")
    if str(row["lifecycle_status"]) == "active":
        return {"id": str(row["id"]), "code": str(row["code"])}
    active = conn.execute(_FIRST_ACTIVE_CLIENT).fetchone()
    if active is None:
        raise LocalDemoSeedError(
            "Default client is inactive and no active company exists for demo-client."
        )
    return {"id": str(active["id"]), "code": str(active["code"])}


def _bind_single_client_membership(conn, user_id: str, client_id: str, now) -> None:
    conn.execute(
        _UPSERT_MEMBERSHIP,
        (user_id, client_id, "client", now, now),
    )
    conn.execute(
        """
        DELETE FROM client_membership
        WHERE user_id = %s AND role = 'client' AND client_id <> %s
        """,
        (user_id, client_id),
    )


def seed_demo_identities(
    conn,
    *,
    publisher_password: str,
    client_password: str,
    iterations: int,
    client_id: str = DEFAULT_CLIENT_ID,
) -> dict[str, str]:
    """Insert or update demo-publisher and demo-client.

    demo-publisher is attached to ``client_id``. demo-client is attached to
    that company when it is active, otherwise to the first active company.
    Does not print passwords or hashes. Caller must already have passed the
    local-DSN guard when this is used from the CLI.
    """
    if not publisher_password or not client_password:
        raise LocalDemoSeedError("Publisher and client passwords are required.")
    if publisher_password == client_password:
        raise LocalDemoSeedError("Publisher and client passwords must differ.")
    now = datetime.now(tz=UTC)
    publisher_row = conn.execute(_SELECT_CLIENT, (client_id,)).fetchone()
    if publisher_row is None:
        raise LocalDemoSeedError("Default client is missing. Apply migrations before seeding.")
    client_target = _demo_client_membership_target(conn, client_id)
    publisher_hash = hash_password(publisher_password, iterations)
    client_hash = hash_password(client_password, iterations)
    publisher = conn.execute(
        _UPSERT_USER, (DEMO_PUBLISHER_SUBJECT, publisher_hash, now, now)
    ).fetchone()
    client = conn.execute(_UPSERT_USER, (DEMO_CLIENT_SUBJECT, client_hash, now, now)).fetchone()
    assert publisher is not None and client is not None
    conn.execute(
        _UPSERT_MEMBERSHIP,
        (publisher["id"], client_id, "publisher", now, now),
    )
    _bind_single_client_membership(conn, client["id"], client_target["id"], now)
    return {
        "client_id": client_target["id"],
        "client_code": client_target["code"],
        "publisher_client_id": client_id,
        "publisher_subject": DEMO_PUBLISHER_SUBJECT,
        "client_subject": DEMO_CLIENT_SUBJECT,
        "publisher_user_id": publisher["id"],
        "client_user_id": client["id"],
    }


def seed_company2_identities(
    conn,
    *,
    publisher_password: str,
    client_password: str,
    iterations: int,
    client_id: str = COMPANY_2_CLIENT_ID,
) -> dict[str, str]:
    """Insert Company 2 plus demo-publisher-2 / demo-client-2.

    Also grants ``demo-publisher`` a publisher membership on Company 2 so one
    operator identity can select either tenant. Does not add a second
    membership to ``demo-client``.
    """
    if not publisher_password or not client_password:
        raise LocalDemoSeedError("Company 2 publisher and client passwords are required.")
    if publisher_password == client_password:
        raise LocalDemoSeedError("Company 2 publisher and client passwords must differ.")
    now = datetime.now(tz=UTC)
    conn.execute(
        _UPSERT_CLIENT,
        (client_id, COMPANY_2_CLIENT_CODE, COMPANY_2_CLIENT_NAME),
    )
    row = conn.execute(_SELECT_CLIENT, (client_id,)).fetchone()
    if row is None:
        raise LocalDemoSeedError("Company 2 client row could not be created.")
    publisher_hash = hash_password(publisher_password, iterations)
    client_hash = hash_password(client_password, iterations)
    publisher = conn.execute(
        _UPSERT_USER, (DEMO_PUBLISHER_2_SUBJECT, publisher_hash, now, now)
    ).fetchone()
    client = conn.execute(_UPSERT_USER, (DEMO_CLIENT_2_SUBJECT, client_hash, now, now)).fetchone()
    assert publisher is not None and client is not None
    conn.execute(
        _UPSERT_MEMBERSHIP,
        (publisher["id"], client_id, "publisher", now, now),
    )
    conn.execute(
        _UPSERT_MEMBERSHIP,
        (client["id"], client_id, "client", now, now),
    )
    universal = conn.execute(
        "SELECT id::text AS id FROM app_user WHERE subject = %s",
        (DEMO_PUBLISHER_SUBJECT,),
    ).fetchone()
    if universal is not None:
        conn.execute(
            _UPSERT_MEMBERSHIP,
            (universal["id"], client_id, "publisher", now, now),
        )
    return {
        "client_id": client_id,
        "client_code": str(row["code"]),
        "publisher_subject": DEMO_PUBLISHER_2_SUBJECT,
        "client_subject": DEMO_CLIENT_2_SUBJECT,
        "publisher_user_id": publisher["id"],
        "client_user_id": client["id"],
    }


def _require_password(name: str) -> str:
    value = os.environ.get(name, "")
    if not value.strip():
        raise LocalDemoSeedError(f"{name} is required.")
    return value


def run_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Seed disposable local demo-publisher and demo-client users. "
            "Optional --company-2 adds a second tenant with its own identities. "
            "Refuses hosted/production databases. Does not print passwords."
        )
    )
    parser.add_argument(
        "--confirm-local-only",
        action="store_true",
        help="Required. Acknowledges this targets disposable local Postgres only.",
    )
    parser.add_argument(
        "--company-2",
        action="store_true",
        dest="company_2",
        help=(
            "Also upsert Company 2 (company-2) plus demo-publisher-2 / "
            "demo-client-2. Also grants demo-publisher a Company 2 membership "
            "for the publisher company selector."
        ),
    )
    args = parser.parse_args(argv)
    settings = Settings()
    try:
        assert_local_demo_seed_allowed(
            database_url=settings.database_url,
            dfip_env=settings.dfip_env,
            seed_flag=os.environ.get("DFIP_LOCAL_DEMO_SEED", ""),
            confirmed=args.confirm_local_only,
        )
        publisher_password = _require_password("DFIP_LOCAL_DEMO_PUBLISHER_PASSWORD")
        client_password = _require_password("DFIP_LOCAL_DEMO_CLIENT_PASSWORD")
        publisher2_password = ""
        client2_password = ""
        if args.company_2:
            publisher2_password = _require_password("DFIP_LOCAL_DEMO_PUBLISHER2_PASSWORD")
            client2_password = _require_password("DFIP_LOCAL_DEMO_CLIENT2_PASSWORD")
            used = {
                publisher_password,
                client_password,
                publisher2_password,
                client2_password,
            }
            if len(used) != 4:
                raise LocalDemoSeedError("Company 1 and Company 2 seed passwords must all differ.")
        iterations = settings.dfip_password_pbkdf2_iterations
        with connect(settings.database_url.strip(), row_factory=dict_row) as conn:
            with conn.transaction():
                seeded = seed_demo_identities(
                    conn,
                    publisher_password=publisher_password,
                    client_password=client_password,
                    iterations=iterations,
                )
                seeded2 = None
                if args.company_2:
                    seeded2 = seed_company2_identities(
                        conn,
                        publisher_password=publisher2_password,
                        client_password=client2_password,
                        iterations=iterations,
                    )
    except LocalDemoSeedError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Local demo seed failed ({type(exc).__name__}).", file=sys.stderr)
        return 1
    print("Local demo identities upserted.")
    print(f"client_id={seeded['client_id']} code={seeded['client_code']}")
    print(f"publisher subject={seeded['publisher_subject']} role=publisher")
    print(f"client subject={seeded['client_subject']} role=client")
    if seeded2 is not None:
        print(f"client_id={seeded2['client_id']} code={seeded2['client_code']}")
        print(f"publisher subject={seeded2['publisher_subject']} role=publisher")
        print(f"client subject={seeded2['client_subject']} role=client")
    print("Passwords and hashes were not printed.")
    print("SPA login uses these subjects as usernames (JWT). Seed is not automatic.")
    return 0


def main() -> None:
    raise SystemExit(run_cli())


if __name__ == "__main__":
    main()
