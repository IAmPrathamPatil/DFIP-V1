"""P13C operator backup and restore.

Logical PostgreSQL custom-format dump plus a filesystem copy of the source
archive. Not imported by create_app. Does not schedule or delete backups.
P13F identify-eligible lists keep-count candidates only.

    python -m dfip_api.backup backup --output DIR
    python -m dfip_api.backup verify --backup DIR
    python -m dfip_api.backup identify-eligible --parent DIR [--keep-count N]
    python -m dfip_api.backup restore --backup DIR --target-database-url URL \\
        --target-archive-root DIR --confirm-disposable
    python -m dfip_api.backup restore --backup DIR --target-database-url URL \\
        --target-archive-root DIR --confirm-production-local
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse, urlunparse

from dfip_db.connection import redact_dsn
from dfip_db.local_demo_guard import is_hosted_or_unsafe_database_url
from dfip_db.migrate import LEDGER_TABLE
from dfip_db.paths import repo_root
from psycopg import connect, sql
from psycopg.rows import dict_row

from dfip_api.source_storage import DEFAULT_BUCKET, URI_SCHEME

TOOL_NAME = "dfip_api.backup"
DUMP_RELATIVE = "postgres/dfip.dump"
ARCHIVE_RELATIVE = "source-archive"
MANIFEST_NAME = "manifest.json"
FORBIDDEN_RESTORE_NAMES = frozenset({"dfip", "postgres", "template0", "template1"})
PRODUCTION_LOCAL_DATABASE_NAME = "dfip"
CLUSTER_ROLES = ("dfip_migrator", "dfip_api", "dfip_worker")
_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)
_SECRET_MANIFEST_KEYS = frozenset(
    {
        "database_url",
        "password",
        "dfip_auth_secret",
        "dfip_bootstrap_token",
        "dfip_dev_auth_token",
        "bearer",
        "authorization",
        "supabase_service_role_key",
        "supabase_anon_key",
    }
)
KEY_TABLES = (
    "client",
    "app_user",
    "client_membership",
    "excel_workbook_grant",
    "source_file",
    "batch",
    "processing_run",
    "stg_source_row",
    "publication",
    "publication_fact",
    "publication_history_grain",
    "publication_current",
    LEDGER_TABLE,
)


class BackupError(Exception):
    """Operator-facing backup/restore failure. Message must not include secrets."""


@dataclass
class ArchiveFile:
    path: str
    byte_size: int
    sha256: str


@dataclass
class Issue:
    source_file_id: str
    kind: str
    detail: str


@dataclass
class VerificationReport:
    checked: int = 0
    skipped_no_uri: int = 0
    missing: list[Issue] = field(default_factory=list)
    sha_mismatch: list[Issue] = field(default_factory=list)
    size_mismatch: list[Issue] = field(default_factory=list)
    malformed_uri: list[Issue] = field(default_factory=list)
    path_mismatch: list[Issue] = field(default_factory=list)
    orphan_count: int = 0
    ok: bool = True

    def as_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["ok"] = self.is_ok()
        return payload

    def is_ok(self) -> bool:
        return not (
            self.missing
            or self.sha_mismatch
            or self.size_mismatch
            or self.malformed_uri
            or self.path_mismatch
        )


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def git_revision() -> str | None:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root(),
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    value = completed.stdout.strip()
    return value or None


def tool_version() -> str:
    try:
        from importlib.metadata import version

        return version("dfip")
    except Exception:
        return "1.0.0"


_compose_prefix: list[str] | None | bool = False


def compose_postgres_prefix() -> list[str] | None:
    """Optional local compose fallback when pg_dump is not on PATH.

    Production operators should install PostgreSQL client tools. This path is
    only for loopback/compose dfip_db. It is not a cloud backup vendor.
    """
    global _compose_prefix
    if _compose_prefix is not False:
        return _compose_prefix
    docker = shutil.which("docker")
    if not docker:
        _compose_prefix = None
        return None
    attempts = (
        [docker, "compose", "--profile", "v2-db", "exec", "-T", "dfip_db"],
        [docker, "compose", "exec", "-T", "dfip_db"],
    )
    for prefix in attempts:
        probe = subprocess.run(
            [*prefix, "pg_dump", "--version"],
            cwd=str(repo_root()),
            check=False,
            capture_output=True,
            text=True,
        )
        if probe.returncode == 0:
            _compose_prefix = prefix
            return prefix
    _compose_prefix = None
    return None


def postgres_tools_available() -> bool:
    if shutil.which("pg_dump") and shutil.which("pg_restore"):
        return True
    return compose_postgres_prefix() is not None


def _compose_container_id(prefix: list[str]) -> str:
    docker = prefix[0]
    command = [docker, "compose"]
    if "--profile" in prefix:
        index = prefix.index("--profile")
        command.extend(["--profile", prefix[index + 1]])
    command.extend(["ps", "-q", "dfip_db"])
    completed = subprocess.run(
        command,
        cwd=str(repo_root()),
        check=False,
        capture_output=True,
        text=True,
    )
    container = completed.stdout.strip().splitlines()[0] if completed.stdout.strip() else ""
    if completed.returncode != 0 or not container:
        raise BackupError("pg_dump was not found on PATH.")
    return container


def _compose_copy(container: str, source: str, destination: str) -> None:
    docker = shutil.which("docker")
    if not docker:
        raise BackupError("pg_dump was not found on PATH.")
    completed = subprocess.run(
        [docker, "cp", source, destination],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise BackupError("PostgreSQL dump file is missing.")


def _cli_identity(database_url: str) -> tuple[str, str]:
    parsed = urlparse(database_url.strip())
    user = unquote(parsed.username) if parsed.username else "postgres"
    return user, database_name(database_url)


def database_name(database_url: str) -> str:
    parsed = urlparse(database_url.strip())
    if parsed.scheme:
        return (parsed.path or "/").lstrip("/").split("/")[0]
    for part in database_url.split():
        key, separator, value = part.partition("=")
        if separator and key.lower() in {"dbname", "database"}:
            return value
    raise BackupError("Database name is missing from the connection settings.")


def with_database_name(database_url: str, name: str) -> str:
    parsed = urlparse(database_url.strip())
    if parsed.scheme:
        return urlunparse(parsed._replace(path="/" + name))
    raise BackupError("Connection settings must be a PostgreSQL URI.")


def pg_cli_env(database_url: str) -> tuple[list[str], dict[str, str]]:
    parsed = urlparse(database_url.strip())
    if not parsed.scheme:
        raise BackupError("Connection settings must be a PostgreSQL URI.")
    env = os.environ.copy()
    if parsed.password:
        env["PGPASSWORD"] = unquote(parsed.password)
    sslmode = (parse_qs(parsed.query).get("sslmode") or [None])[0]
    if sslmode:
        env["PGSSLMODE"] = sslmode
    args = [
        "--no-password",
        "-h",
        parsed.hostname or "127.0.0.1",
        "-p",
        str(parsed.port or 5432),
        "-d",
        database_name(database_url),
    ]
    if parsed.username:
        args.extend(["-U", unquote(parsed.username)])
    return args, env


def assert_privileged_dump_role(database_url: str) -> dict[str, object]:
    with connect(database_url, row_factory=dict_row) as conn:
        row = conn.execute(
            """
            SELECT current_user AS user_name, rolsuper, rolbypassrls
            FROM pg_roles
            WHERE rolname = current_user
            """
        ).fetchone()
    if row is None:
        raise BackupError("Backup login could not be identified.")
    name = str(row["user_name"])
    if name == "dfip_api":
        raise BackupError(
            "Backup refuses role dfip_api. FORCE RLS can hide tenant rows. "
            "Use a superuser or BYPASSRLS-capable account."
        )
    if not bool(row["rolsuper"]) and not bool(row["rolbypassrls"]):
        raise BackupError(
            "Backup requires a superuser or BYPASSRLS-capable account. "
            "Do not grant BYPASSRLS to dfip_api."
        )
    return {
        "user_name": name,
        "superuser": bool(row["rolsuper"]),
        "bypassrls": bool(row["rolbypassrls"]),
    }


def load_migration_ledger(database_url: str) -> list[str]:
    with connect(database_url, row_factory=dict_row) as conn:
        rows = conn.execute(f"SELECT filename FROM {LEDGER_TABLE} ORDER BY filename").fetchall()
    return [str(row["filename"]) for row in rows]


def load_source_files(database_url: str) -> list[dict[str, object]]:
    with connect(database_url, row_factory=dict_row) as conn:
        rows = conn.execute(
            """
            SELECT id::text AS id,
                   client_id::text AS client_id,
                   sha256,
                   byte_size,
                   storage_uri
            FROM source_file
            ORDER BY id
            """
        ).fetchall()
    return [dict(row) for row in rows]


def parse_storage_uri(uri: str) -> tuple[str, str, str, str]:
    raw = (uri or "").strip()
    parsed = urlparse(raw)
    if parsed.scheme != URI_SCHEME or not parsed.netloc:
        raise BackupError("storage_uri is malformed.")
    parts = [item for item in parsed.path.strip("/").split("/") if item]
    if len(parts) != 3 or not parts[2].lower().endswith(".xlsx"):
        raise BackupError("storage_uri is malformed.")
    client_id, source_file_id, filename = parts
    sha256 = filename[:-5]
    if not _UUID.match(client_id) or not _UUID.match(source_file_id) or not _SHA256.match(sha256):
        raise BackupError("storage_uri is malformed.")
    return parsed.netloc, client_id, source_file_id, sha256.lower()


def expected_archive_relative(uri: str) -> str:
    bucket, client_id, source_file_id, sha256 = parse_storage_uri(uri)
    return f"{bucket}/{client_id}/{source_file_id}/{sha256}.xlsx"


def inventory_archive(root: Path) -> list[ArchiveFile]:
    if not root.is_dir():
        raise BackupError("Source archive directory is missing.")
    items: list[ArchiveFile] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        items.append(
            ArchiveFile(path=relative, byte_size=path.stat().st_size, sha256=sha256_file(path))
        )
    items.sort(key=lambda item: item.path)
    return items


def copy_archive_tree(source_root: Path, destination_root: Path) -> None:
    if not source_root.is_dir():
        raise BackupError("Source archive directory is missing.")
    if destination_root.exists():
        raise BackupError("Backup archive destination already exists.")
    shutil.copytree(source_root, destination_root)


def verify_source_mapping(
    source_files: list[dict[str, object]],
    archive_root: Path,
    *,
    expected_bucket: str | None = None,
) -> VerificationReport:
    report = VerificationReport()
    referenced: set[str] = set()
    for row in source_files:
        source_id = str(row.get("id") or "")
        uri = row.get("storage_uri")
        if not uri:
            report.skipped_no_uri += 1
            continue
        report.checked += 1
        try:
            bucket, client_id, file_id, sha256 = parse_storage_uri(str(uri))
            relative = expected_archive_relative(str(uri))
        except BackupError:
            report.malformed_uri.append(
                Issue(source_id, "malformed_uri", "storage_uri is malformed.")
            )
            continue
        referenced.add(relative)
        if expected_bucket and bucket != expected_bucket:
            report.path_mismatch.append(
                Issue(source_id, "path_mismatch", "storage_uri bucket does not match.")
            )
        if str(row.get("client_id") or "") != client_id:
            report.path_mismatch.append(
                Issue(source_id, "path_mismatch", "storage_uri client_id does not match.")
            )
        if source_id != file_id:
            report.path_mismatch.append(
                Issue(source_id, "path_mismatch", "storage_uri source_file_id does not match.")
            )
        if str(row.get("sha256") or "").lower() != sha256:
            report.path_mismatch.append(
                Issue(source_id, "path_mismatch", "storage_uri sha256 does not match.")
            )
        path = archive_root.joinpath(*relative.split("/"))
        if not path.is_file():
            report.missing.append(Issue(source_id, "missing", relative))
            continue
        digest = sha256_file(path)
        if digest != sha256:
            report.sha_mismatch.append(Issue(source_id, "sha_mismatch", relative))
        size = path.stat().st_size
        expected_size = row.get("byte_size")
        if expected_size is not None and int(expected_size) != size:
            report.size_mismatch.append(Issue(source_id, "size_mismatch", relative))
    if archive_root.is_dir():
        inventory = inventory_archive(archive_root)
        report.orphan_count = sum(1 for item in inventory if item.path not in referenced)
    report.ok = report.is_ok()
    return report


def _secret_free(payload: object) -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            lowered = str(key).lower()
            if lowered in _SECRET_MANIFEST_KEYS or "password" in lowered:
                raise BackupError("Manifest refused a secret-bearing field.")
            _secret_free(value)
    elif isinstance(payload, list):
        for item in payload:
            _secret_free(item)


def write_manifest(path: Path, payload: dict[str, object]) -> None:
    _secret_free(payload)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_manifest(backup_root: Path) -> dict[str, object]:
    path = backup_root / MANIFEST_NAME
    if not path.is_file():
        raise BackupError("Backup manifest is missing.")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise BackupError("Backup manifest is invalid.")
    _secret_free(payload)
    return payload


def _tool_major(version_text: str) -> int | None:
    match = re.search(r"\(PostgreSQL\)\s+(\d+)", version_text)
    if match:
        return int(match.group(1))
    match = re.search(r"\b(\d+)\.\d+", version_text)
    return int(match.group(1)) if match else None


def server_major(database_url: str) -> int | None:
    try:
        with connect(database_url) as conn:
            raw = conn.execute("SHOW server_version").fetchone()
    except Exception as exc:
        raise BackupError("Could not read PostgreSQL server version.") from exc
    if raw is None:
        return None
    return _tool_major(str(raw[0]))


def client_matches_server(binary: str, database_url: str) -> bool:
    completed = subprocess.run([binary, "--version"], check=False, capture_output=True, text=True)
    client = _tool_major((completed.stdout or "") + (completed.stderr or ""))
    server = server_major(database_url)
    return client is not None and server is not None and client == server


def _native_dump_binary(database_url: str) -> str | None:
    native = shutil.which("pg_dump")
    if native and client_matches_server(native, database_url):
        return native
    return None


def _native_restore_binary(database_url: str | None = None) -> str | None:
    native = shutil.which("pg_restore")
    if native is None:
        return None
    if database_url and not client_matches_server(native, database_url):
        return None
    return native


def dump_postgres(database_url: str, dump_path: Path) -> dict[str, object]:
    dump_path.parent.mkdir(parents=True, exist_ok=True)
    native = _native_dump_binary(database_url)
    compose = None if native else compose_postgres_prefix()
    if native:
        args, env = pg_cli_env(database_url)
        completed = subprocess.run(
            [native, "-Fc", *args, "-f", str(dump_path)],
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )
        if completed.returncode != 0:
            raise BackupError("pg_dump failed.")
        version = subprocess.run(
            [native, "--version"], check=False, capture_output=True, text=True
        ).stdout.strip()
    elif compose:
        user, dbname = _cli_identity(database_url)
        remote = f"/tmp/dfip-p13c-{os.getpid()}.dump"
        completed = subprocess.run(
            [*compose, "pg_dump", "-Fc", "-U", user, "-d", dbname, "-f", remote],
            cwd=str(repo_root()),
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise BackupError("pg_dump failed.")
        container = _compose_container_id(compose)
        _compose_copy(container, f"{container}:{remote}", str(dump_path))
        subprocess.run(
            [*compose, "rm", "-f", remote],
            cwd=str(repo_root()),
            check=False,
            capture_output=True,
            text=True,
        )
        version = subprocess.run(
            [*compose, "pg_dump", "--version"],
            cwd=str(repo_root()),
            check=False,
            capture_output=True,
            text=True,
        ).stdout.strip()
    else:
        raise BackupError("pg_dump was not found on PATH.")
    if not dump_path.is_file() or dump_path.stat().st_size == 0:
        raise BackupError("PostgreSQL dump file is missing.")
    toc_entries = inspect_dump(dump_path)
    if toc_entries < 1:
        raise BackupError("PostgreSQL dump does not list any objects.")
    return {
        "dump_filename": DUMP_RELATIVE,
        "dump_sha256": sha256_file(dump_path),
        "dump_byte_size": dump_path.stat().st_size,
        "pg_dump_version": version,
        "database_name": database_name(database_url),
        "toc_entries": toc_entries,
        "format": "custom",
    }


def inspect_dump(dump_path: Path) -> int:
    native = shutil.which("pg_restore")
    compose = None if native else compose_postgres_prefix()
    if native:
        completed = subprocess.run(
            [native, "--list", str(dump_path)],
            check=False,
            capture_output=True,
            text=True,
        )
        stdout = completed.stdout
    elif compose:
        remote = f"/tmp/dfip-p13c-list-{os.getpid()}.dump"
        container = _compose_container_id(compose)
        _compose_copy(container, str(dump_path), f"{container}:{remote}")
        completed = subprocess.run(
            [*compose, "pg_restore", "--list", remote],
            cwd=str(repo_root()),
            check=False,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            [*compose, "rm", "-f", remote],
            cwd=str(repo_root()),
            check=False,
            capture_output=True,
            text=True,
        )
        stdout = completed.stdout
    else:
        raise BackupError("pg_restore was not found on PATH.")
    if completed.returncode != 0:
        raise BackupError("pg_restore --list failed.")
    return sum(1 for line in stdout.splitlines() if line.strip() and not line.startswith(";"))


def restore_postgres(dump_path: Path, target_url: str) -> None:
    native = _native_restore_binary(target_url)
    compose = None if native else compose_postgres_prefix()
    if native:
        args, env = pg_cli_env(target_url)
        completed = subprocess.run(
            [native, "--exit-on-error", "--no-owner", *args, str(dump_path)],
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )
    elif compose:
        user, dbname = _cli_identity(target_url)
        remote = f"/tmp/dfip-p13c-restore-{os.getpid()}.dump"
        container = _compose_container_id(compose)
        _compose_copy(container, str(dump_path), f"{container}:{remote}")
        completed = subprocess.run(
            [
                *compose,
                "pg_restore",
                "--exit-on-error",
                "--no-owner",
                "-U",
                user,
                "-d",
                dbname,
                remote,
            ],
            cwd=str(repo_root()),
            check=False,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            [*compose, "rm", "-f", remote],
            cwd=str(repo_root()),
            check=False,
            capture_output=True,
            text=True,
        )
    else:
        raise BackupError("pg_restore was not found on PATH.")
    if completed.returncode != 0:
        raise BackupError("pg_restore failed.")


def ensure_cluster_roles(database_url: str) -> None:
    statements = [
        "CREATE ROLE dfip_migrator NOLOGIN",
        "CREATE ROLE dfip_api NOLOGIN NOBYPASSRLS",
        "CREATE ROLE dfip_worker NOLOGIN NOBYPASSRLS",
    ]
    with connect(database_url, autocommit=True) as conn:
        for statement in statements:
            try:
                conn.execute(statement)
            except Exception as exc:
                sqlstate = getattr(exc, "sqlstate", None)
                if sqlstate != "42710":
                    raise BackupError("Cluster role prerequisite failed.") from exc


def assert_safe_restore_target(
    target_url: str,
    *,
    confirmed: bool,
    production_local: bool = False,
) -> str:
    if production_local and confirmed:
        raise BackupError(
            "Restore cannot combine --confirm-disposable with --confirm-production-local."
        )
    if production_local:
        if is_hosted_or_unsafe_database_url(target_url):
            raise BackupError(
                "Restore refuses hosted or non-local PostgreSQL. Use a colocated local database."
            )
        name = database_name(target_url).lower()
        if name != PRODUCTION_LOCAL_DATABASE_NAME:
            raise BackupError("Production-local restore allows only database name dfip.")
        return name
    if not confirmed:
        raise BackupError("Restore requires --confirm-disposable.")
    if is_hosted_or_unsafe_database_url(target_url):
        raise BackupError(
            "Restore refuses hosted or non-local PostgreSQL. Use a disposable local database."
        )
    name = database_name(target_url).lower()
    if name in FORBIDDEN_RESTORE_NAMES:
        raise BackupError(
            "Restore refuses this database name. Use a newly created disposable database."
        )
    return name


def create_empty_database(admin_url: str, name: str) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,62}", name):
        raise BackupError("Disposable database name is invalid.")
    assert_safe_restore_target(with_database_name(admin_url, name), confirmed=True)
    maintenance = with_database_name(admin_url, "postgres")
    with connect(maintenance, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    return with_database_name(admin_url, name)


def drop_database(admin_url: str, name: str) -> None:
    assert_safe_restore_target(with_database_name(admin_url, name), confirmed=True)
    maintenance = with_database_name(admin_url, "postgres")
    with connect(maintenance, autocommit=True) as conn:
        conn.execute(
            """
            SELECT pg_terminate_backend(pid)
            FROM pg_stat_activity
            WHERE datname = %s AND pid <> pg_backend_pid()
            """,
            (name,),
        )
        conn.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(name)))


def create_backup(
    *,
    database_url: str,
    archive_root: Path,
    output_root: Path,
    bucket: str = DEFAULT_BUCKET,
) -> dict[str, object]:
    if not database_url.strip():
        raise BackupError("DATABASE_URL is required for backup.")
    if not archive_root:
        raise BackupError("DFIP_STORAGE_ENDPOINT is required for backup.")
    privilege = assert_privileged_dump_role(database_url)
    output_root.mkdir(parents=True, exist_ok=True)
    if any(output_root.iterdir()):
        raise BackupError("Backup output directory must be empty.")
    dump_path = output_root / DUMP_RELATIVE
    archive_dest = output_root / ARCHIVE_RELATIVE
    postgres = dump_postgres(database_url, dump_path)
    postgres["migrations"] = load_migration_ledger(database_url)
    copy_archive_tree(archive_root, archive_dest)
    inventory = [asdict(item) for item in inventory_archive(archive_dest)]
    mapping = verify_source_mapping(
        load_source_files(database_url),
        archive_dest,
        expected_bucket=bucket,
    )
    if not mapping.is_ok():
        raise BackupError("Backup verification failed: source_file does not match archive.")
    manifest = {
        "created_at": datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "git_revision": git_revision(),
        "tool": {"name": TOOL_NAME, "version": tool_version()},
        "consistency": "stop-api-then-pg_dump-then-copy-archive",
        "postgres": postgres,
        "backup_privilege": {
            "superuser": privilege["superuser"],
            "bypassrls": privilege["bypassrls"],
        },
        "source_archive": {
            "bucket": bucket,
            "file_count": len(inventory),
            "inventory": inventory,
        },
        "source_file_verification": mapping.as_dict(),
        "cluster_roles": list(CLUSTER_ROLES),
        "restore_order": [
            "provision empty PostgreSQL database",
            "ensure cluster roles dfip_migrator dfip_api dfip_worker",
            "pg_restore custom dump",
            "copy source-archive tree",
            "verify ledger and source_file mapping",
            "inject P13A secrets from the environment",
            "start API",
            "P13B abandoned-run sweep",
        ],
        "rpo": "last successful coordinated backup",
        "rto": (
            "time to provision PostgreSQL, restore the dump, copy the archive, "
            "inject configuration, and verify; depends on dataset size"
        ),
    }
    write_manifest(output_root / MANIFEST_NAME, manifest)
    return manifest


def verify_backup(
    backup_root: Path,
    *,
    database_url: str | None = None,
    require_mapping_ok: bool = True,
) -> dict[str, object]:
    manifest = read_manifest(backup_root)
    dump_path = backup_root / DUMP_RELATIVE
    if not dump_path.is_file():
        raise BackupError("PostgreSQL dump file is missing.")
    postgres = manifest.get("postgres")
    if not isinstance(postgres, dict):
        raise BackupError("Backup manifest is missing postgres metadata.")
    expected = str(postgres.get("dump_sha256") or "")
    actual = sha256_file(dump_path)
    if not expected or actual != expected:
        raise BackupError("PostgreSQL dump checksum does not match the manifest.")
    archive_root = backup_root / ARCHIVE_RELATIVE
    inventory = inventory_archive(archive_root)
    recorded = manifest.get("source_archive")
    if not isinstance(recorded, dict):
        raise BackupError("Backup manifest is missing source archive metadata.")
    expected_inventory = recorded.get("inventory") or []
    if not isinstance(expected_inventory, list):
        raise BackupError("Backup archive inventory is invalid.")
    if [item.path for item in inventory] != [
        str(item.get("path")) for item in expected_inventory if isinstance(item, dict)
    ]:
        raise BackupError("Archive inventory does not match the backup tree.")
    for item, expected_item in zip(inventory, expected_inventory, strict=True):
        if not isinstance(expected_item, dict):
            raise BackupError("Archive inventory is invalid.")
        if item.sha256 != expected_item.get("sha256") or item.byte_size != expected_item.get(
            "byte_size"
        ):
            raise BackupError("Archive file checksum or size does not match the manifest.")
    mapping: VerificationReport | None = None
    if database_url:
        mapping = verify_source_mapping(
            load_source_files(database_url),
            archive_root,
            expected_bucket=str(recorded.get("bucket") or DEFAULT_BUCKET),
        )
        if require_mapping_ok and not mapping.is_ok():
            raise BackupError("source_file archive verification failed.")
    return {
        "ok": True,
        "dump_sha256": actual,
        "archive_files": len(inventory),
        "source_file_verification": None if mapping is None else mapping.as_dict(),
    }


def restore_backup(
    backup_root: Path,
    *,
    target_url: str,
    target_archive_root: Path,
    confirm_disposable: bool,
    confirm_production_local: bool = False,
) -> dict[str, object]:
    assert_safe_restore_target(
        target_url,
        confirmed=confirm_disposable,
        production_local=confirm_production_local,
    )
    verify_backup(backup_root, require_mapping_ok=False)
    dump_path = backup_root / DUMP_RELATIVE
    archive_source = backup_root / ARCHIVE_RELATIVE
    ensure_cluster_roles(target_url)
    restore_postgres(dump_path, target_url)
    if target_archive_root.exists():
        raise BackupError("Restore archive destination already exists.")
    shutil.copytree(archive_source, target_archive_root)
    mapping = verify_source_mapping(
        load_source_files(target_url),
        target_archive_root,
    )
    if not mapping.is_ok():
        raise BackupError("Restore verification failed: source_file does not match archive.")
    return {"ok": True, "source_file_verification": mapping.as_dict()}


def identify_eligible_backups(
    parent: Path,
    *,
    keep_count: int,
    keep_days: int = 0,
    now: datetime | None = None,
) -> dict[str, object]:
    """List backup sets that could be deleted under keep-count policy.

    Never selects the only verified set. Never selects an unverified set.
    Does not delete anything.
    """
    root = Path(parent)
    if not root.is_dir():
        raise BackupError("Backup parent directory is missing.")
    protected_count = max(1, int(keep_count) if keep_count else 1)
    stamp = now or datetime.now(tz=UTC)
    verified: list[dict[str, object]] = []
    unverified: list[dict[str, object]] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or not (child / MANIFEST_NAME).is_file():
            continue
        entry: dict[str, object] = {"path": str(child.resolve())}
        try:
            manifest = read_manifest(child)
            created = str(manifest.get("created_at") or "")
            entry["created_at"] = created
            verify_backup(child, require_mapping_ok=False)
            entry["verified"] = True
            verified.append(entry)
        except BackupError as exc:
            entry["verified"] = False
            entry["error"] = str(exc)
            unverified.append(entry)

    def _created_key(item: dict[str, object]) -> str:
        return str(item.get("created_at") or "")

    verified.sort(key=_created_key, reverse=True)
    protected = verified[:protected_count]
    protected_paths = {str(item["path"]) for item in protected}
    eligible: list[dict[str, object]] = []
    for item in verified:
        if str(item["path"]) in protected_paths:
            continue
        if keep_days > 0:
            raw = str(item.get("created_at") or "")
            try:
                created_at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                continue
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=UTC)
            if stamp - created_at < timedelta(days=keep_days):
                continue
        eligible.append(item)
    return {
        "ok": True,
        "keep_count": protected_count,
        "keep_days": keep_days,
        "verified_count": len(verified),
        "protected": protected,
        "eligible": eligible,
        "unverified": unverified,
        "deleted": [],
    }


def restore_database_report(database_url: str) -> dict[str, object]:
    with connect(database_url, row_factory=dict_row) as conn:
        tables = {
            name: conn.execute(
                "SELECT COUNT(*) AS n FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = %s",
                (name,),
            ).fetchone()["n"]
            for name in KEY_TABLES
        }
        migrations = [
            str(row["filename"])
            for row in conn.execute(
                f"SELECT filename FROM {LEDGER_TABLE} ORDER BY filename"
            ).fetchall()
        ]
        current = conn.execute("SELECT COUNT(*) AS n FROM publication_current").fetchone()
        history = conn.execute("SELECT COUNT(*) AS n FROM publication").fetchone()
    missing = [name for name, count in tables.items() if int(count) < 1]
    return {
        "ok": not missing,
        "missing_tables": missing,
        "migrations": migrations,
        "publication_current": int(current["n"]) if current else 0,
        "publication_history": int(history["n"]) if history else 0,
        "database": redact_dsn(database_url),
    }


def _settings_from_env() -> tuple[str, Path, str]:
    database_url = os.environ.get("DATABASE_URL", "").strip()
    archive = Path(os.environ.get("DFIP_STORAGE_ENDPOINT", "").strip() or ".")
    bucket = os.environ.get("DFIP_STORAGE_BUCKET", "").strip() or DEFAULT_BUCKET
    return database_url, archive, bucket


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="DFIP P13C backup and restore. Stop the API before backup."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    backup_cmd = sub.add_parser("backup", help="Dump PostgreSQL then copy the source archive.")
    backup_cmd.add_argument("--output", required=True, type=Path)
    backup_cmd.add_argument("--database-url")
    backup_cmd.add_argument("--archive-root", type=Path)
    backup_cmd.add_argument("--bucket")

    verify_cmd = sub.add_parser("verify", help="Read-only verification of a backup directory.")
    verify_cmd.add_argument("--backup", required=True, type=Path)
    verify_cmd.add_argument("--database-url")

    restore_cmd = sub.add_parser(
        "restore",
        help="Restore a disposable local database and archive tree.",
    )
    restore_cmd.add_argument("--backup", required=True, type=Path)
    restore_cmd.add_argument("--target-database-url", required=True)
    restore_cmd.add_argument("--target-archive-root", required=True, type=Path)
    restore_cmd.add_argument("--confirm-disposable", action="store_true")
    restore_cmd.add_argument(
        "--confirm-production-local",
        action="store_true",
        help="Restore into colocated local database name dfip. Refuses hosted URLs.",
    )

    identify_cmd = sub.add_parser(
        "identify-eligible",
        help="List extra verified backup sets. Never deletes.",
    )
    identify_cmd.add_argument("--parent", required=True, type=Path)
    identify_cmd.add_argument("--keep-count", type=int, default=0)
    identify_cmd.add_argument("--keep-days", type=int, default=0)

    args = parser.parse_args(argv)
    env_url, env_archive, env_bucket = _settings_from_env()
    try:
        if args.command == "backup":
            print(
                "Stop the API before backup. This command dumps PostgreSQL, then "
                "copies the archive so committed source_file rows cannot miss files.",
                file=sys.stderr,
            )
            manifest = create_backup(
                database_url=(args.database_url or env_url).strip(),
                archive_root=args.archive_root or env_archive,
                output_root=args.output,
                bucket=args.bucket or env_bucket,
            )
            print(json.dumps({"ok": True, "created_at": manifest["created_at"]}))
            return 0
        if args.command == "verify":
            report = verify_backup(
                args.backup,
                database_url=(args.database_url or "").strip() or None,
            )
            print(json.dumps(report, sort_keys=True))
            return 0
        if args.command == "identify-eligible":
            keep_count = args.keep_count
            if keep_count <= 0:
                raw = os.environ.get("DFIP_BACKUP_KEEP_COUNT", "").strip()
                keep_count = int(raw) if raw.isdigit() else 1
            keep_days = args.keep_days
            if keep_days <= 0:
                raw_days = os.environ.get("DFIP_BACKUP_KEEP_DAYS", "").strip()
                keep_days = int(raw_days) if raw_days.isdigit() else 0
            report = identify_eligible_backups(
                args.parent,
                keep_count=keep_count,
                keep_days=keep_days,
            )
            print(json.dumps(report, sort_keys=True))
            return 0
        restore_backup(
            args.backup,
            target_url=args.target_database_url,
            target_archive_root=args.target_archive_root,
            confirm_disposable=args.confirm_disposable,
            confirm_production_local=args.confirm_production_local,
        )
        print(json.dumps({"ok": True}))
        return 0
    except BackupError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
