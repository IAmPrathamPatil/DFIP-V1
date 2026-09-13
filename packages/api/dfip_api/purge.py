"""P13F operator company purge.

Privileged DSN, not imported by create_app. Does not grant DELETE to dfip_api.
Does not schedule. Does not rewrite backups.

    python -m dfip_api.purge dry-run --code CODE --confirm-disposable
    python -m dfip_api.purge purge --code CODE --confirm-purge --confirm-disposable
    python -m dfip_api.purge dry-run --code CODE --confirm-production-local
    python -m dfip_api.purge purge --code CODE --confirm-purge --confirm-production-local
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from dfip_config.catalog_identity import packaged_catalog_owner_client_id
from dfip_db.client_directory import ClientRecord, fetch_client_by_code
from dfip_db.connection import redact_dsn
from dfip_db.local_demo_guard import (
    DEFAULT_CLIENT_CODE,
    DEFAULT_CLIENT_ID,
    is_hosted_or_unsafe_database_url,
)
from psycopg import connect
from psycopg.rows import dict_row

from dfip_api.backup import FORBIDDEN_RESTORE_NAMES, PRODUCTION_LOCAL_DATABASE_NAME, database_name
from dfip_api.lifecycle import is_purge_eligible
from dfip_api.source_storage import DEFAULT_BUCKET

TOOL_NAME = "dfip_api.purge"
UPLOAD_TEMP_PREFIX = "dfip-upload-"


class PurgeError(Exception):
    """Operator-facing purge failure. Message must not include secrets."""


def assert_safe_purge_target(
    database_url: str,
    *,
    confirmed: bool,
    production_local: bool = False,
) -> str:
    if production_local and confirmed:
        raise PurgeError(
            "Purge cannot combine --confirm-disposable with --confirm-production-local."
        )
    if production_local:
        if is_hosted_or_unsafe_database_url(database_url):
            raise PurgeError(
                "Purge refuses hosted or non-local PostgreSQL. Use a colocated local database."
            )
        name = database_name(database_url).lower()
        if name != PRODUCTION_LOCAL_DATABASE_NAME:
            raise PurgeError("Production-local purge allows only database name dfip.")
        return name
    if not confirmed:
        raise PurgeError("Purge requires --confirm-disposable.")
    if is_hosted_or_unsafe_database_url(database_url):
        raise PurgeError(
            "Purge refuses hosted or non-local PostgreSQL. Use a disposable local database."
        )
    name = database_name(database_url).lower()
    if name in FORBIDDEN_RESTORE_NAMES:
        raise PurgeError(
            "Purge refuses this database name. Use a newly created disposable database."
        )
    return name


def tenant_archive_dir(archive_root: Path, client_id: str, bucket: str = DEFAULT_BUCKET) -> Path:
    root = Path(archive_root).resolve()
    name = (bucket or DEFAULT_BUCKET).strip() or DEFAULT_BUCKET
    path = (root / name / client_id).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise PurgeError("Archive path is invalid.") from exc
    return path


def _count(conn, sql: str, params: tuple[object, ...] = ()) -> int:
    row = conn.execute(sql, params).fetchone()
    if row is None:
        return 0
    return int(next(iter(row.values())))


def _is_protected_owner(record: ClientRecord) -> bool:
    owner = packaged_catalog_owner_client_id()
    return (
        record.client_id == owner
        or record.client_id == DEFAULT_CLIENT_ID
        or record.code == DEFAULT_CLIENT_CODE
    )


def collect_inventory(conn, record: ClientRecord, archive_path: Path | None) -> dict[str, object]:
    client_id = record.client_id
    params = (client_id,)
    memberships = conn.execute(
        """
        SELECT u.id::text AS user_id, u.subject, u.is_platform_admin,
               (
                   SELECT COUNT(*) FROM client_membership AS other
                   WHERE other.user_id = u.id
               ) AS membership_count
        FROM app_user AS u
        JOIN client_membership AS m ON m.user_id = u.id
        WHERE m.client_id = %s
        ORDER BY u.subject
        """,
        params,
    ).fetchall()
    delete_users: list[dict[str, object]] = []
    retain_users: list[dict[str, object]] = []
    for row in memberships:
        item = {
            "user_id": str(row["user_id"]),
            "subject": str(row["subject"]),
            "is_platform_admin": bool(row["is_platform_admin"]),
            "membership_count": int(row["membership_count"]),
        }
        if item["is_platform_admin"] or int(row["membership_count"]) > 1:
            retain_users.append(item)
        else:
            delete_users.append(item)
    archive_objects = 0
    if archive_path is not None and archive_path.is_dir():
        archive_objects = sum(1 for item in archive_path.rglob("*") if item.is_file())
    return {
        "client_id": record.client_id,
        "code": record.code,
        "lifecycle_status": record.lifecycle_status,
        "deactivated_at": (
            record.deactivated_at.isoformat() if record.deactivated_at is not None else None
        ),
        "purge_eligible_after": (
            record.purge_eligible_after.isoformat()
            if record.purge_eligible_after is not None
            else None
        ),
        "purge_eligible": is_purge_eligible(record),
        "protected_packaged_owner": _is_protected_owner(record),
        "source_files": _count(
            conn, "SELECT COUNT(*) AS n FROM source_file WHERE client_id = %s", params
        ),
        "archive_objects": archive_objects,
        "archive_path": str(archive_path) if archive_path is not None else None,
        "batches": _count(conn, "SELECT COUNT(*) AS n FROM batch WHERE client_id = %s", params),
        "processing_runs": _count(
            conn, "SELECT COUNT(*) AS n FROM processing_run WHERE client_id = %s", params
        ),
        "stg_source_rows": _count(
            conn, "SELECT COUNT(*) AS n FROM stg_source_row WHERE client_id = %s", params
        ),
        "stg_rejected_rows": _count(
            conn, "SELECT COUNT(*) AS n FROM stg_rejected_row WHERE client_id = %s", params
        ),
        "facts": _count(
            conn, "SELECT COUNT(*) AS n FROM fact_campaign_day WHERE client_id = %s", params
        ),
        "fact_history": _count(
            conn,
            "SELECT COUNT(*) AS n FROM fact_campaign_day_history WHERE client_id = %s",
            params,
        ),
        "qa_findings": _count(
            conn, "SELECT COUNT(*) AS n FROM qa_finding WHERE client_id = %s", params
        ),
        "publications": _count(
            conn, "SELECT COUNT(*) AS n FROM publication WHERE client_id = %s", params
        ),
        "publication_facts": _count(
            conn, "SELECT COUNT(*) AS n FROM publication_fact WHERE client_id = %s", params
        ),
        "publication_history_grains": _count(
            conn,
            "SELECT COUNT(*) AS n FROM publication_history_grain WHERE client_id = %s",
            params,
        ),
        "legacy_snapshot_none": _count(
            conn,
            "SELECT COUNT(*) AS n FROM publication "
            "WHERE client_id = %s AND snapshot_status = 'none'",
            params,
        ),
        "campaign_label_versions": _count(
            conn, "SELECT COUNT(*) AS n FROM campaign_label_version WHERE client_id = %s", params
        ),
        "campaign_label_rows": _count(
            conn,
            """
            SELECT COUNT(*) AS n FROM campaign_label_row AS r
            JOIN campaign_label_version AS v ON v.id = r.version_id
            WHERE v.client_id = %s
            """,
            params,
        ),
        "template_label_versions": _count(
            conn, "SELECT COUNT(*) AS n FROM template_label_version WHERE client_id = %s", params
        ),
        "template_label_rows": _count(
            conn,
            """
            SELECT COUNT(*) AS n FROM template_label_row AS r
            JOIN template_label_version AS v ON v.id = r.version_id
            WHERE v.client_id = %s
            """,
            params,
        ),
        "rate_card_versions": _count(
            conn, "SELECT COUNT(*) AS n FROM rate_card_version WHERE client_id = %s", params
        ),
        "rate_card_rules": _count(
            conn,
            """
            SELECT COUNT(*) AS n FROM rate_card_rule AS r
            JOIN rate_card_version AS v ON v.id = r.version_id
            WHERE v.client_id = %s
            """,
            params,
        ),
        "label_group_versions": _count(
            conn, "SELECT COUNT(*) AS n FROM label_group_version WHERE client_id = %s", params
        ),
        "label_group_members": _count(
            conn,
            """
            SELECT COUNT(*) AS n FROM label_group_member AS r
            JOIN label_group_version AS v ON v.id = r.version_id
            WHERE v.client_id = %s
            """,
            params,
        ),
        "memberships": len(memberships),
        "users_deleted": delete_users,
        "users_retained": retain_users,
    }


def _delete_step(
    conn,
    name: str,
    sql: str,
    params: tuple[object, ...],
    fail_before: str | None,
) -> None:
    if fail_before == name:
        raise PurgeError(f"Injected failure before {name}.")
    conn.execute(sql, params)


_SQL_PURGE_STEPS: tuple[tuple[str, str], ...] = (
    (
        "analytics_saved_analysis",
        "DELETE FROM analytics_saved_analysis WHERE client_id = %s",
    ),
    ("excel_workbook_grant", "DELETE FROM excel_workbook_grant WHERE client_id = %s"),
    ("publication_current", "DELETE FROM publication_current WHERE client_id = %s"),
    (
        "publication_history_grain",
        "DELETE FROM publication_history_grain WHERE client_id = %s",
    ),
    ("publication_fact", "DELETE FROM publication_fact WHERE client_id = %s"),
    ("publication", "DELETE FROM publication WHERE client_id = %s"),
    ("qa_finding", "DELETE FROM qa_finding WHERE client_id = %s"),
    ("fact_campaign_day", "DELETE FROM fact_campaign_day WHERE client_id = %s"),
    (
        "fact_campaign_day_history",
        "DELETE FROM fact_campaign_day_history WHERE client_id = %s",
    ),
    ("processing_run", "DELETE FROM processing_run WHERE client_id = %s"),
    ("stg_source_row", "DELETE FROM stg_source_row WHERE client_id = %s"),
    ("stg_rejected_row", "DELETE FROM stg_rejected_row WHERE client_id = %s"),
    ("batch", "DELETE FROM batch WHERE client_id = %s"),
    (
        "campaign_label_row",
        "DELETE FROM campaign_label_row WHERE version_id IN "
        "(SELECT id FROM campaign_label_version WHERE client_id = %s)",
    ),
    (
        "label_group_member",
        "DELETE FROM label_group_member WHERE version_id IN "
        "(SELECT id FROM label_group_version WHERE client_id = %s)",
    ),
    (
        "template_label_row",
        "DELETE FROM template_label_row WHERE version_id IN "
        "(SELECT id FROM template_label_version WHERE client_id = %s)",
    ),
    (
        "rate_card_rule",
        "DELETE FROM rate_card_rule WHERE version_id IN "
        "(SELECT id FROM rate_card_version WHERE client_id = %s)",
    ),
    ("campaign_label_version", "DELETE FROM campaign_label_version WHERE client_id = %s"),
    ("label_group_version", "DELETE FROM label_group_version WHERE client_id = %s"),
    (
        "template_label_version",
        "DELETE FROM template_label_version WHERE client_id = %s",
    ),
    ("rate_card_version", "DELETE FROM rate_card_version WHERE client_id = %s"),
    ("source_file", "DELETE FROM source_file WHERE client_id = %s"),
    ("client_membership", "DELETE FROM client_membership WHERE client_id = %s"),
    ("audit_log", "DELETE FROM audit_log WHERE client_id = %s"),
    ("client", "DELETE FROM client WHERE id = %s"),
)


def execute_sql_purge(
    conn,
    client_id: str,
    delete_user_ids: tuple[str, ...],
    *,
    fail_before: str | None = None,
) -> None:
    params = (client_id,)
    for name, sql in _SQL_PURGE_STEPS:
        if name == "client_membership" and delete_user_ids:
            _delete_step(conn, name, sql, params, fail_before)
            _delete_step(
                conn,
                "orphan_app_user",
                """
                DELETE FROM app_user
                WHERE id = ANY(%s)
                  AND is_platform_admin = false
                  AND NOT EXISTS (
                      SELECT 1 FROM client_membership AS m
                      WHERE m.user_id = app_user.id
                  )
                """,
                (list(delete_user_ids),),
                fail_before,
            )
            continue
        _delete_step(conn, name, sql, params, fail_before)


def remove_tenant_archive(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        raise PurgeError("Tenant archive path exists and is not a directory.")


def stale_upload_dirs(*, max_age_hours: int, now: float | None = None) -> list[Path]:
    root = Path(tempfile.gettempdir())
    cutoff = (now if now is not None else time.time()) - max(0, max_age_hours) * 3600
    found: list[Path] = []
    try:
        entries = list(root.iterdir())
    except OSError:
        return found
    for item in entries:
        if not item.is_dir() or not item.name.startswith(UPLOAD_TEMP_PREFIX):
            continue
        try:
            if item.stat().st_mtime < cutoff:
                found.append(item)
        except OSError:
            continue
    return found


def dry_run(
    *,
    database_url: str,
    code: str,
    archive_root: Path | None,
    bucket: str,
    confirm_disposable: bool,
    confirm_production_local: bool = False,
) -> dict[str, object]:
    assert_safe_purge_target(
        database_url,
        confirmed=confirm_disposable,
        production_local=confirm_production_local,
    )
    with connect(database_url, row_factory=dict_row) as conn:
        record = fetch_client_by_code(conn, code)
        if record is None:
            raise PurgeError("Company not found.")
        archive_path = None
        if archive_root is not None:
            archive_path = tenant_archive_dir(archive_root, record.client_id, bucket)
        inventory = collect_inventory(conn, record, archive_path)
    inventory["ok"] = True
    inventory["mutated"] = False
    inventory["database"] = redact_dsn(database_url)
    return inventory


def purge_company(
    *,
    database_url: str,
    code: str,
    archive_root: Path | None,
    bucket: str,
    confirm_disposable: bool,
    confirm_purge: bool,
    confirm_production_local: bool = False,
    fail_before: str | None = None,
    archive_remover: Callable[[Path], None] | None = None,
) -> dict[str, object]:
    if not confirm_purge:
        raise PurgeError("Purge requires --confirm-purge.")
    assert_safe_purge_target(
        database_url,
        confirmed=confirm_disposable,
        production_local=confirm_production_local,
    )
    remover = archive_remover or remove_tenant_archive
    with connect(database_url, row_factory=dict_row) as conn:
        record = fetch_client_by_code(conn, code)
        if record is None:
            raise PurgeError("Company not found.")
        if _is_protected_owner(record):
            raise PurgeError("The packaged default company cannot be purged.")
        if not is_purge_eligible(record):
            raise PurgeError("Company is not purge-eligible.")
        archive_path = None
        if archive_root is not None:
            archive_path = tenant_archive_dir(archive_root, record.client_id, bucket)
        inventory = collect_inventory(conn, record, archive_path)
        delete_ids = tuple(str(item["user_id"]) for item in inventory["users_deleted"])
        try:
            with conn.transaction():
                execute_sql_purge(conn, record.client_id, delete_ids, fail_before=fail_before)
        except PurgeError:
            raise
        except Exception as exc:
            raise PurgeError("Company purge failed and was rolled back.") from exc
    archive_removed = True
    archive_error = None
    if archive_path is not None:
        try:
            remover(archive_path)
            if archive_path.exists():
                archive_removed = False
                archive_error = "Tenant archive tree still exists after deletion."
        except Exception:
            archive_removed = False
            archive_error = "Tenant archive cleanup failed."
    result = {
        "ok": archive_removed,
        "database_purged": True,
        "archive_removed": archive_removed,
        "archive_path": str(archive_path) if archive_path is not None else None,
        "code": record.code,
        "client_id": record.client_id,
        "counts": inventory,
        "purged_at": datetime.now(tz=UTC).isoformat(),
    }
    if archive_error:
        result["error"] = archive_error
        result["ok"] = False
    return result


def _settings_from_env() -> tuple[str, Path | None, str]:
    database_url = os.environ.get("DATABASE_URL", "").strip()
    raw_archive = os.environ.get("DFIP_STORAGE_ENDPOINT", "").strip()
    archive = Path(raw_archive) if raw_archive else None
    bucket = os.environ.get("DFIP_STORAGE_BUCKET", "").strip() or DEFAULT_BUCKET
    return database_url, archive, bucket


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DFIP P13F company purge. Operator CLI only.")
    sub = parser.add_subparsers(dest="command", required=True)

    dry = sub.add_parser("dry-run", help="Show what a company purge would delete. No mutation.")
    dry.add_argument("--code", required=True)
    dry.add_argument("--database-url")
    dry.add_argument("--archive-root", type=Path)
    dry.add_argument("--bucket")
    dry.add_argument("--confirm-disposable", action="store_true")
    dry.add_argument("--confirm-production-local", action="store_true")

    purge_cmd = sub.add_parser("purge", help="Permanently delete one inactive eligible company.")
    purge_cmd.add_argument("--code", required=True)
    purge_cmd.add_argument("--database-url")
    purge_cmd.add_argument("--archive-root", type=Path)
    purge_cmd.add_argument("--bucket")
    purge_cmd.add_argument("--confirm-disposable", action="store_true")
    purge_cmd.add_argument("--confirm-production-local", action="store_true")
    purge_cmd.add_argument("--confirm-purge", action="store_true")

    temps = sub.add_parser(
        "stale-uploads",
        help="Identify leftover dfip-upload-* temp directories.",
    )
    temps.add_argument("--max-age-hours", type=int, default=24)
    temps.add_argument("--delete", action="store_true")

    args = parser.parse_args(argv)
    env_url, env_archive, env_bucket = _settings_from_env()
    try:
        if args.command == "stale-uploads":
            found = stale_upload_dirs(max_age_hours=args.max_age_hours)
            deleted: list[str] = []
            if args.delete:
                for path in found:
                    shutil.rmtree(path, ignore_errors=True)
                    if not path.exists():
                        deleted.append(str(path))
            print(
                json.dumps(
                    {
                        "ok": True,
                        "identified": [str(path) for path in found],
                        "deleted": deleted,
                    }
                )
            )
            return 0
        database_url = (args.database_url or env_url).strip()
        if not database_url:
            raise PurgeError("DATABASE_URL is required.")
        archive_root = args.archive_root or env_archive
        bucket = args.bucket or env_bucket
        if args.command == "dry-run":
            report = dry_run(
                database_url=database_url,
                code=args.code.strip(),
                archive_root=archive_root,
                bucket=bucket,
                confirm_disposable=args.confirm_disposable,
                confirm_production_local=args.confirm_production_local,
            )
            print(json.dumps(report, default=str))
            return 0
        report = purge_company(
            database_url=database_url,
            code=args.code.strip(),
            archive_root=archive_root,
            bucket=bucket,
            confirm_disposable=args.confirm_disposable,
            confirm_purge=args.confirm_purge,
            confirm_production_local=args.confirm_production_local,
        )
        print(json.dumps(report, default=str))
        return 0 if report.get("ok") else 2
    except PurgeError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
