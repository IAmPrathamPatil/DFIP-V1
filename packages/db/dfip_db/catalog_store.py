"""PostgreSQL adapter for publisher Logic/Labels catalog uploads.

Writes the existing ``campaign_label_*`` and ``label_group_*`` tables.
Does not duplicate catalogs. Seeded P2 rows are not updated.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from uuid import uuid4

from dfip_config.catalog import (
    LABELS_KIND,
    LOGIC_KIND,
    UPLOAD_NOTES,
    CatalogKind,
    CatalogVersion,
)
from psycopg_pool import ConnectionPool

from dfip_db.connection import transaction
from dfip_db.rls import current_rls


def _as_text(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _version_from_row(
    row: dict[str, Any], kind: CatalogKind, rows: tuple[dict[str, Any], ...]
) -> CatalogVersion:
    created = row["created_at"]
    if isinstance(created, datetime) and created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    effective_from = row.get("effective_from")
    effective_to = row.get("effective_to")
    return CatalogVersion(
        id=str(row["id"]),
        client_id=str(row["client_id"]),
        kind=kind,
        version_label=str(row["version_label"]),
        status=row["status"],
        row_count=int(row.get("row_count") or len(rows) or 0),
        distinct_key_count=int(row.get("distinct_key_count") or len(rows) or 0),
        duplicate_key_count=int(row.get("duplicate_key_count") or 0),
        created_at=created,
        created_by=_as_text(row.get("created_by")),
        notes=_as_text(row.get("notes")),
        source_filename=None,
        rows=rows,
        effective_from=effective_from.isoformat()
        if isinstance(effective_from, date)
        else _as_text(effective_from),
        effective_to=effective_to.isoformat()
        if isinstance(effective_to, date)
        else _as_text(effective_to),
    )


class PostgresCatalogStore:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def _tx(self):
        return transaction(self._pool, current_rls())

    def add_version(self, record: CatalogVersion) -> CatalogVersion:
        with self._tx() as conn:
            if record.kind == LOGIC_KIND:
                conn.execute(
                    """
                    INSERT INTO campaign_label_version (
                        id, client_id, version_label, effective_from, effective_to,
                        row_count, distinct_key_count, duplicate_key_count, status,
                        created_at, created_by, notes
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        record.id,
                        record.client_id,
                        record.version_label,
                        record.effective_from,
                        record.effective_to,
                        record.row_count,
                        record.distinct_key_count,
                        record.duplicate_key_count,
                        record.status,
                        record.created_at,
                        record.created_by,
                        record.notes or UPLOAD_NOTES,
                    ),
                )
                for item in record.rows:
                    conn.execute(
                        """
                        INSERT INTO campaign_label_row (
                            id, version_id, row_order, campaign_name,
                            filter_logic_1, filter_logic_2,
                            amc_status_filter_logic_3,
                            amc_device_category_filter_logic_4,
                            amc_product_cat_filter_logic_5,
                            manual_or_automated
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            str(uuid4()),
                            record.id,
                            int(item["row_order"]),
                            item["campaign_name"],
                            item.get("filter_logic_1"),
                            item.get("filter_logic_2"),
                            item.get("amc_status_filter_logic_3"),
                            item.get("amc_device_category_filter_logic_4"),
                            item.get("amc_product_cat_filter_logic_5"),
                            item.get("manual_or_automated"),
                        ),
                    )
            else:
                conn.execute(
                    """
                    INSERT INTO label_group_version (
                        id, client_id, version_label, status, created_at, notes
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        record.id,
                        record.client_id,
                        record.version_label,
                        record.status,
                        record.created_at,
                        record.notes or UPLOAD_NOTES,
                    ),
                )
                for item in record.rows:
                    conn.execute(
                        """
                        INSERT INTO label_group_member (
                            id, version_id, group_name, filter_logic_1_value, row_order
                        )
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        (
                            str(uuid4()),
                            record.id,
                            item["group_name"],
                            item["filter_logic_1_value"],
                            int(item["row_order"]),
                        ),
                    )
        return record

    def get(self, version_id: str) -> CatalogVersion | None:
        with self._tx() as conn:
            return self._load(conn, version_id, client_id=None)

    def get_for_client(self, client_id: str, version_id: str) -> CatalogVersion | None:
        with self._tx() as conn:
            return self._load(conn, version_id, client_id=client_id)

    def list_for_client(self, client_id: str, kind: CatalogKind) -> tuple[CatalogVersion, ...]:
        with self._tx() as conn:
            if kind == LOGIC_KIND:
                rows = conn.execute(
                    """
                    SELECT * FROM campaign_label_version
                    WHERE client_id = %s AND notes = %s
                    ORDER BY created_at DESC, id DESC
                    """,
                    (client_id, UPLOAD_NOTES),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT v.*,
                           (SELECT COUNT(*) FROM label_group_member m WHERE m.version_id = v.id)
                               AS row_count
                    FROM label_group_version v
                    WHERE v.client_id = %s AND v.notes = %s
                    ORDER BY v.created_at DESC, v.id DESC
                    """,
                    (client_id, UPLOAD_NOTES),
                ).fetchall()
        return tuple(_version_from_row(row, kind, ()) for row in rows)

    def save(self, record: CatalogVersion) -> CatalogVersion:
        with self._tx() as conn:
            if record.kind == LOGIC_KIND:
                conn.execute(
                    """
                    UPDATE campaign_label_version
                    SET status = %s, effective_from = %s, effective_to = %s, created_by = %s
                    WHERE id = %s AND client_id = %s AND notes = %s
                    """,
                    (
                        record.status,
                        record.effective_from,
                        record.effective_to,
                        record.created_by,
                        record.id,
                        record.client_id,
                        UPLOAD_NOTES,
                    ),
                )
            else:
                conn.execute(
                    """
                    UPDATE label_group_version
                    SET status = %s
                    WHERE id = %s AND client_id = %s AND notes = %s
                    """,
                    (record.status, record.id, record.client_id, UPLOAD_NOTES),
                )
        return record

    def active_for(self, client_id: str, kind: CatalogKind) -> CatalogVersion | None:
        items = [item for item in self.list_for_client(client_id, kind) if item.status == "active"]
        if not items:
            return None
        loaded = self.get_for_client(client_id, items[0].id)
        return loaded

    def activate(
        self,
        client_id: str,
        version_id: str,
        *,
        activated_by: str | None = None,
    ) -> CatalogVersion:
        with self._tx() as conn:
            current = self._load(conn, version_id, client_id=client_id)
            if current is None:
                raise KeyError(version_id)
            if current.status != "draft":
                raise ValueError("only a draft version can be activated")
            if not current.rows:
                raise ValueError("empty catalog versions cannot be activated")
            today = date.today().isoformat()
            if current.kind == LOGIC_KIND:
                conn.execute(
                    """
                    UPDATE campaign_label_version
                    SET status = 'superseded', effective_to = %s
                    WHERE client_id = %s AND notes = %s AND status = 'active' AND id <> %s
                    """,
                    (today, client_id, UPLOAD_NOTES, version_id),
                )
                conn.execute(
                    """
                    UPDATE campaign_label_version
                    SET status = 'active', effective_from = COALESCE(effective_from, %s),
                        effective_to = NULL, created_by = COALESCE(created_by, %s)
                    WHERE id = %s AND client_id = %s AND notes = %s
                    """,
                    (today, activated_by, version_id, client_id, UPLOAD_NOTES),
                )
            else:
                conn.execute(
                    """
                    UPDATE label_group_version
                    SET status = 'superseded'
                    WHERE client_id = %s AND notes = %s AND status = 'active' AND id <> %s
                    """,
                    (client_id, UPLOAD_NOTES, version_id),
                )
                conn.execute(
                    """
                    UPDATE label_group_version
                    SET status = 'active'
                    WHERE id = %s AND client_id = %s AND notes = %s
                    """,
                    (version_id, client_id, UPLOAD_NOTES),
                )
            loaded = self._load(conn, version_id, client_id=client_id)
            if loaded is None:
                raise KeyError(version_id)
            return loaded

    def deactivate(self, client_id: str, version_id: str) -> CatalogVersion:
        with self._tx() as conn:
            current = self._load(conn, version_id, client_id=client_id)
            if current is None:
                raise KeyError(version_id)
            if current.status != "active":
                raise ValueError("only an active version can be deactivated")
            today = date.today().isoformat()
            if current.kind == LOGIC_KIND:
                conn.execute(
                    """
                    UPDATE campaign_label_version
                    SET status = 'superseded', effective_to = %s
                    WHERE id = %s AND client_id = %s AND notes = %s
                    """,
                    (today, version_id, client_id, UPLOAD_NOTES),
                )
            else:
                conn.execute(
                    """
                    UPDATE label_group_version
                    SET status = 'superseded'
                    WHERE id = %s AND client_id = %s AND notes = %s
                    """,
                    (version_id, client_id, UPLOAD_NOTES),
                )
            loaded = self._load(conn, version_id, client_id=client_id)
            if loaded is None:
                raise KeyError(version_id)
            return loaded

    def _load(self, conn: Any, version_id: str, *, client_id: str | None) -> CatalogVersion | None:
        logic = conn.execute(
            "SELECT * FROM campaign_label_version WHERE id = %s AND notes = %s",
            (version_id, UPLOAD_NOTES),
        ).fetchone()
        if logic is not None:
            if client_id and str(logic["client_id"]) != client_id:
                return None
            members = conn.execute(
                """
                SELECT row_order, campaign_name, filter_logic_1, filter_logic_2,
                       amc_status_filter_logic_3, amc_device_category_filter_logic_4,
                       amc_product_cat_filter_logic_5, manual_or_automated
                FROM campaign_label_row
                WHERE version_id = %s
                ORDER BY row_order
                """,
                (version_id,),
            ).fetchall()
            rows = tuple(
                {
                    "row_order": int(item["row_order"]),
                    "campaign_name": item["campaign_name"],
                    "filter_logic_1": item["filter_logic_1"],
                    "filter_logic_2": item["filter_logic_2"],
                    "amc_status_filter_logic_3": item["amc_status_filter_logic_3"],
                    "amc_device_category_filter_logic_4": item[
                        "amc_device_category_filter_logic_4"
                    ],
                    "amc_product_cat_filter_logic_5": item["amc_product_cat_filter_logic_5"],
                    "manual_or_automated": item["manual_or_automated"],
                }
                for item in members
            )
            return _version_from_row(logic, LOGIC_KIND, rows)

        group = conn.execute(
            "SELECT * FROM label_group_version WHERE id = %s AND notes = %s",
            (version_id, UPLOAD_NOTES),
        ).fetchone()
        if group is None:
            return None
        if client_id and str(group["client_id"]) != client_id:
            return None
        members = conn.execute(
            """
            SELECT row_order, group_name, filter_logic_1_value
            FROM label_group_member
            WHERE version_id = %s
            ORDER BY row_order
            """,
            (version_id,),
        ).fetchall()
        rows = tuple(
            {
                "row_order": int(item["row_order"]),
                "group_name": item["group_name"],
                "filter_logic_1_value": item["filter_logic_1_value"],
            }
            for item in members
        )
        return _version_from_row(group, LABELS_KIND, rows)
