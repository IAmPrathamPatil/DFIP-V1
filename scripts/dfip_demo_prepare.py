"""Prepare disposable local demo Postgres + identities for START_DFIP_DEMO.bat.

Loads selected names from repository `.env` into the process environment
(without printing values), refuses hosted/production DSNs, optionally starts
the existing `docker compose --profile v2-db` Postgres, applies migrations,
and runs `python -m dfip_api.local_demo_seed --confirm-local-only`.

Not imported by the API. Not a production bootstrap. Never prints passwords,
JWT secrets, or DATABASE_URL.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from dfip_api.local_demo_seed import run_cli
from dfip_db.local_demo_guard import (
    LocalDemoSeedError,
    assert_local_demo_seed_allowed,
    is_hosted_or_unsafe_database_url,
)
from dfip_db.migrate import apply_migrations
from psycopg import connect

ROOT = Path(__file__).resolve().parents[1]

_OVERLAY_NAMES = frozenset(
    {
        "DATABASE_URL",
        "DFIP_ENV",
        "DFIP_AUTH_MODE",
        "DFIP_AUTH_SECRET",
        "DFIP_LOCAL_DEMO_SEED",
        "DFIP_LOCAL_DEMO_PUBLISHER_PASSWORD",
        "DFIP_LOCAL_DEMO_CLIENT_PASSWORD",
        "DFIP_STORAGE_ENDPOINT",
    }
)

_COMPOSE_CMD = ("docker", "compose", "--profile", "v2-db", "up", "-d", "dfip_db")

# Local/demo workbook ingest only. Production P13E defaults stay 10 MiB / 20 MiB.
LOCAL_DEMO_UPLOAD_MAX_BYTES = "52428800"
LOCAL_DEMO_UPLOAD_MAX_FILES = "5"
LOCAL_DEMO_UPLOAD_MAX_TOTAL_BYTES = "104857600"
_UPLOAD_LIMIT_NAMES = (
    "DFIP_UPLOAD_MAX_BYTES",
    "DFIP_UPLOAD_MAX_FILES",
    "DFIP_UPLOAD_MAX_TOTAL_BYTES",
)
# Local/demo access JWT lifetime only. Production/default remains 3600.
LOCAL_DEMO_AUTH_TOKEN_TTL_SECONDS = "43200"
_AUTH_TTL_NAME = "DFIP_AUTH_TOKEN_TTL_SECONDS"
# Local demo source archive. Empty DFIP_STORAGE_ENDPOINT is in-memory and
# cannot resume uploads after API restart. Relative to the repository root.
LOCAL_DEMO_STORAGE_RELATIVE = "tmp/dfip-source-archive"
_STORAGE_NAME = "DFIP_STORAGE_ENDPOINT"


def _parse_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        index = line.find("=")
        if index < 1:
            continue
        name = line[:index].strip()
        value = line[index + 1 :].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[name] = value
    return values


def overlay_dotenv(repo_root: Path) -> None:
    """Copy allowlisted `.env` names into os.environ when unset. Never prints values."""
    file_map = _parse_dotenv(repo_root / ".env")
    for name in _OVERLAY_NAMES:
        if os.environ.get(name, "").strip():
            continue
        value = file_map.get(name, "")
        if value.strip():
            os.environ[name] = value


def ensure_local_demo_upload_limits(env_path: Path) -> list[str]:
    """Append missing local-demo upload caps. Never overwrites operator values.

    Does not print secrets or other ``.env`` contents. Returns the names added.
    """
    if not env_path.is_file():
        return []
    existing = _parse_dotenv(env_path)
    values = {
        "DFIP_UPLOAD_MAX_BYTES": LOCAL_DEMO_UPLOAD_MAX_BYTES,
        "DFIP_UPLOAD_MAX_FILES": LOCAL_DEMO_UPLOAD_MAX_FILES,
        "DFIP_UPLOAD_MAX_TOTAL_BYTES": LOCAL_DEMO_UPLOAD_MAX_TOTAL_BYTES,
    }
    missing = [
        name for name in _UPLOAD_LIMIT_NAMES if not existing.get(name, "").strip()
    ]
    if not missing:
        return []
    lines = ["", "# Local demo workbook ingest (not production P13E defaults)."]
    for name in missing:
        lines.append(f"{name}={values[name]}")
    with env_path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines) + "\n")
    return missing


def ensure_local_demo_jwt_ttl(env_path: Path) -> list[str]:
    """Append missing local-demo JWT TTL. Never overwrites operator values.

    Production/default in settings.py remains 3600. This does not create
    non-expiring tokens. Does not print secrets. Returns the names added.
    """
    if not env_path.is_file():
        return []
    existing = _parse_dotenv(env_path)
    if existing.get(_AUTH_TTL_NAME, "").strip():
        return []
    lines = [
        "",
        "# Local demo JWT access lifetime (not production default 3600).",
        f"{_AUTH_TTL_NAME}={LOCAL_DEMO_AUTH_TOKEN_TTL_SECONDS}",
    ]
    with env_path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines) + "\n")
    return [_AUTH_TTL_NAME]


def local_demo_storage_path(repo_root: Path | None = None) -> Path:
    """Private local directory for demo source archives. Creates the folder."""
    root = repo_root or ROOT
    path = (root / LOCAL_DEMO_STORAGE_RELATIVE).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def ensure_local_demo_storage(env_path: Path, repo_root: Path | None = None) -> list[str]:
    """Set empty DFIP_STORAGE_ENDPOINT to the local demo archive. Never overwrites.

    Does not print secrets or other ``.env`` contents. Returns the names added.
    """
    if not env_path.is_file():
        return []
    existing = _parse_dotenv(env_path)
    if existing.get(_STORAGE_NAME, "").strip():
        return []
    archive = local_demo_storage_path(repo_root).as_posix()
    raw_lines = env_path.read_text(encoding="utf-8").splitlines()
    replaced = False
    rewritten: list[str] = []
    for line in raw_lines:
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            rewritten.append(line)
            continue
        key, value = stripped.split("=", 1)
        if key.strip() == _STORAGE_NAME and not value.strip().strip("\"'"):
            rewritten.append(f"{_STORAGE_NAME}={archive}")
            replaced = True
        else:
            rewritten.append(line)
    if not replaced:
        rewritten.extend(
            [
                "",
                "# Local demo source archive (durable across API restart).",
                f"{_STORAGE_NAME}={archive}",
            ]
        )
    env_path.write_text("\n".join(rewritten) + "\n", encoding="utf-8")
    return [_STORAGE_NAME]


def _require_name(name: str) -> str:
    value = os.environ.get(name, "")
    if not value.strip():
        raise LocalDemoSeedError(f"Missing required variable: {name}")
    return value


def validate_local_demo_env() -> str:
    """Return DATABASE_URL after local-only checks. Raises LocalDemoSeedError."""
    env_name = os.environ.get("DFIP_ENV", "development").strip().lower() or "development"
    if env_name == "production":
        raise LocalDemoSeedError("This launcher refuses DFIP_ENV=production.")
    mode = os.environ.get("DFIP_AUTH_MODE", "").strip().lower()
    if mode != "jwt":
        raise LocalDemoSeedError(
            "One-click demo login requires DFIP_AUTH_MODE=jwt "
            "(SPA username/password issues a JWT)."
        )
    _require_name("DFIP_AUTH_SECRET")
    _require_name("DFIP_LOCAL_DEMO_PUBLISHER_PASSWORD")
    _require_name("DFIP_LOCAL_DEMO_CLIENT_PASSWORD")
    database_url = _require_name("DATABASE_URL")
    if is_hosted_or_unsafe_database_url(database_url):
        raise LocalDemoSeedError(
            "Local demo seed refuses this DATABASE_URL. Use loopback / compose dfip_db only."
        )
    os.environ["DFIP_LOCAL_DEMO_SEED"] = "1"
    assert_local_demo_seed_allowed(
        database_url=database_url,
        dfip_env=env_name,
        seed_flag="1",
        confirmed=True,
    )
    return database_url


def _dsn_port(database_url: str) -> int:
    parsed = urlparse(database_url.strip())
    if parsed.port:
        return parsed.port
    return 5432


def _dsn_host(database_url: str) -> str:
    return (urlparse(database_url.strip()).hostname or "").lower()


def maybe_start_compose_postgres(repo_root: Path, database_url: str) -> None:
    """Start the documented v2-db Postgres when the DSN is local compose port 5433."""
    host = _dsn_host(database_url)
    if host not in {"127.0.0.1", "localhost", "::1"}:
        return
    if _dsn_port(database_url) != 5433:
        return
    compose = repo_root / "docker-compose.yml"
    if not compose.is_file():
        return
    print("Starting local PostgreSQL via docker compose profile v2-db...")
    try:
        completed = subprocess.run(
            _COMPOSE_CMD,
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    except OSError:
        print("Docker Compose is not available. Start local PostgreSQL yourself.")
        print("Documented command: docker compose --profile v2-db up -d dfip_db")
        return
    except subprocess.TimeoutExpired:
        print("Docker Compose timed out starting dfip_db.")
        print("Documented command: docker compose --profile v2-db up -d dfip_db")
        return
    if completed.returncode != 0:
        print("Docker Compose did not start dfip_db (it may already be running).")
        print("Documented command: docker compose --profile v2-db up -d dfip_db")


def wait_for_postgres(database_url: str, *, seconds: int = 45) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            with connect(database_url, connect_timeout=3) as conn:
                conn.execute("SELECT 1")
            return
        except Exception:
            time.sleep(2)
    raise LocalDemoSeedError(
        "Local PostgreSQL is not accepting connections. "
        "Start it first (docker compose --profile v2-db up -d dfip_db) "
        "or point DATABASE_URL at a local disposable database."
    )


def apply_local_migrations(database_url: str) -> None:
    with connect(database_url, autocommit=False) as conn:
        applied = apply_migrations(conn)
    if applied:
        print("Applied local migrations:", ", ".join(applied))
    else:
        print("Local migrations already applied.")


def prepare_local_demo(repo_root: Path | None = None) -> int:
    root = repo_root or ROOT
    overlay_dotenv(root)
    ensure_local_demo_upload_limits(root / ".env")
    ensure_local_demo_jwt_ttl(root / ".env")
    added_storage = ensure_local_demo_storage(root / ".env", root)
    if added_storage:
        print("Local demo source archive: tmp/dfip-source-archive")
        overlay_dotenv(root)
    try:
        database_url = validate_local_demo_env()
        maybe_start_compose_postgres(root, database_url)
        wait_for_postgres(database_url)
        apply_local_migrations(database_url)
    except LocalDemoSeedError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception:
        print("Local demo prepare failed.", file=sys.stderr)
        return 1
    code = run_cli(["--confirm-local-only"])
    if code == 0:
        print("Local demo preparation complete.")
    return code


def main() -> None:
    raise SystemExit(prepare_local_demo())


if __name__ == "__main__":
    main()
