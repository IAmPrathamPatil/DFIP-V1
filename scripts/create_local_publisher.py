#!/usr/bin/env python3
"""Create a local publisher account with a custom username and password.

This script provisions a publisher account in the local development database
without removing the existing demo accounts.

Safety features:
- Password prompted securely via getpass with confirmation
- DATABASE_URL validated to point to local loopback PostgreSQL + 'dfip' database
- Publisher company membership explicit (defaults to 'default' company only)
- Uses project's password-hashing, enforces uniqueness, uses transaction
- Preserves demo-publisher and demo-client accounts

Usage:
    python scripts/create_local_publisher.py <username>

Requires environment variables:
    DFIP_LOCAL_DEMO_SEED=1
    DATABASE_URL (from .env) pointing to local dfip database
    DFIP_PASSWORD_PBKDF2_ITERATIONS (from .env or default)

Flags (all required):
    --confirm-local-only     Acknowledge this targets disposable local Postgres only
    --company <code>         Company code to grant publisher membership (default: 'default')
                              Can be specified multiple times for multiple companies
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from datetime import UTC, datetime
from urllib.parse import urlparse
from uuid import uuid4

from psycopg import connect
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row

# Load .env if present
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from dfip_api.password import hash_password
from dfip_config.settings import Settings

# Allowed local database hosts (matching local_demo_guard.py)
ALLOWED_LOCAL_HOSTS = frozenset({
    "127.0.0.1",
    "localhost",
    "::1",
    "host.docker.internal",
    "dfip_db",
    "postgres",
})

EXPECTED_DB_NAME = "dfip"


def validate_database_url(database_url: str) -> tuple[str, str]:
    """Validate DATABASE_URL points to allowed local host and expected database.
    Returns (host, dbname) if valid, raises ValueError otherwise.
    """
    raw = (database_url or "").strip()
    if not raw:
        raise ValueError("DATABASE_URL is not set.")

    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    dbname = (parsed.path or "").lstrip("/")

    if host not in ALLOWED_LOCAL_HOSTS:
        raise ValueError(
            f"DATABASE_URL host '{host}' is not an allowed local development host. "
            f"Allowed: {', '.join(sorted(ALLOWED_LOCAL_HOSTS))}"
        )

    if dbname != EXPECTED_DB_NAME:
        raise ValueError(
            f"DATABASE_URL database '{dbname}' does not match expected '{EXPECTED_DB_NAME}'. "
            "Refusing to write to unexpected database."
        )

    return host, dbname


def get_password_with_confirmation(prompt: str = "Enter password: ") -> str:
    """Prompt for password with confirmation. Returns the password."""
    while True:
        pwd1 = getpass.getpass(prompt)
        if not pwd1:
            print("Password cannot be empty.", file=sys.stderr)
            continue
        if len(pwd1) < 8:
            print("Password must be at least 8 characters.", file=sys.stderr)
            continue
        pwd2 = getpass.getpass("Confirm password: ")
        if pwd1 != pwd2:
            print("Passwords do not match. Please try again.", file=sys.stderr)
            continue
        return pwd1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a local publisher account with custom username/password.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "username",
        help="Desired username (subject) for the new publisher account",
    )
    parser.add_argument(
        "--company",
        action="append",
        default=None,
        help="Company code to grant publisher membership (default: 'default'). "
             "Can be specified multiple times for multiple companies.",
    )
    parser.add_argument(
        "--confirm-local-only",
        action="store_true",
        required=True,
        help="Required. Acknowledge this targets disposable local Postgres only.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate inputs and show what would be created without writing to database.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    # Validate environment flags
    if os.environ.get("DFIP_LOCAL_DEMO_SEED") != "1":
        print("ERROR: DFIP_LOCAL_DEMO_SEED=1 is required.", file=sys.stderr)
        return 2

    if not args.confirm_local_only:
        print("ERROR: --confirm-local-only is required.", file=sys.stderr)
        return 2

    settings = Settings()
    database_url = settings.database_url.strip()

    # Validate DATABASE_URL
    try:
        host, dbname = validate_database_url(database_url)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    # Validate username
    username = args.username.strip()
    if not username:
        print("ERROR: username is required.", file=sys.stderr)
        return 2

    # Resolve company codes to client IDs
    company_codes = args.company if args.company is not None else ["default"]
    company_codes = [c.strip() for c in company_codes if c.strip()]

    if args.dry_run:
        print("DRY RUN - No changes will be made.")
        print(f"  username: {username}")
        print(f"  company codes: {', '.join(company_codes)}")
        print(f"  database: {host}/{dbname}")
        print("Would create publisher account with above details.")
        return 0

    # Secure password prompt
    print("Creating local publisher account...")
    print(f"  username: {username}")
    print(f"  company codes: {', '.join(company_codes)}")
    print(f"  database: {host}/{dbname}")
    print()
    password = get_password_with_confirmation("Enter password for new account: ")

    iterations = settings.dfip_password_pbkdf2_iterations
    password_hash = hash_password(password, iterations)

    try:
        with connect(database_url, row_factory=dict_row) as conn:
            with conn.transaction():
                now = datetime.now(tz=UTC)
                user_id = str(uuid4())

                # Check if user already exists
                existing = conn.execute(
                    "SELECT subject FROM app_user WHERE subject = %s", (username,)
                ).fetchone()
                if existing:
                    print(f"ERROR: User '{username}' already exists.", file=sys.stderr)
                    return 2

                # Resolve company codes to client IDs
                companies = conn.execute(
                    "SELECT id::text AS id, code, name FROM client "
                    "WHERE code = ANY(%s) AND COALESCE(lifecycle_status, 'active') = 'active'",
                    (company_codes,),
                ).fetchall()

                if not companies:
                    print(
                        f"ERROR: No active companies found for codes: {company_codes}. "
                        "Available active companies:",
                        file=sys.stderr,
                    )
                    active = conn.execute(
                        "SELECT code, name FROM client "
                        "WHERE COALESCE(lifecycle_status, 'active') = 'active'"
                    ).fetchall()
                    for c in active:
                        print(f"  {c['code']}: {c['name']}", file=sys.stderr)
                    return 2

                # Insert user as client user first (bypasses publisher-exists check)
                conn.execute(
                    """
                    INSERT INTO app_user (
                        id, subject, is_platform_admin, password_hash, token_version,
                        created_at, updated_at
                    )
                    VALUES (%s, %s, false, %s, 1, %s, %s)
                    """,
                    (user_id, username, password_hash, now, now),
                )

                # Add publisher membership for each requested company
                for company in companies:
                    conn.execute(
                        """
                        INSERT INTO client_membership (
                            user_id, client_id, role, created_at, updated_at
                        )
                        VALUES (%s, %s, 'publisher', %s, %s)
                        """,
                        (user_id, company["id"], now, now),
                    )

                print(f"\nCreated publisher account:")
                print(f"  username: {username}")
                print(f"  user_id: {user_id}")
                print(f"  companies: {', '.join(c['code'] for c in companies)}")
                print(f"  password: (not printed)")
                return 0

    except UniqueViolation:
        print(f"ERROR: User '{username}' already exists (race condition).", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())