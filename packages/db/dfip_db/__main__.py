"""Apply DFIP migrations: ``python -m dfip_db``."""

from __future__ import annotations

import os
import sys

from psycopg import connect

from dfip_db.migrate import apply_migrations


def main() -> None:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        print("DATABASE_URL is required to apply migrations.", file=sys.stderr)
        raise SystemExit(1)
    with connect(url, autocommit=False) as conn:
        applied = apply_migrations(conn)
    if applied:
        print("Applied:", ", ".join(applied))
    else:
        print("Migrations already applied.")


if __name__ == "__main__":
    main()
