"""PostgreSQL publication / publication_current store."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4

from dfip_analytics.kpis import ADDITIVE_MEASURES
from dfip_api.errors import PersistenceUnavailableError
from dfip_api.publication_store import (
    FACT_SCOPE_CLIENT_CURRENT,
    FACT_SCOPE_PROCESSING_RUN,
    SNAPSHOT_CHUNK_SIZE,
    SNAPSHOT_STATUS_COMPLETE,
    SNAPSHOT_STATUS_NONE,
    PublicationCurrentRecord,
    PublicationRecord,
)
from dfip_core.transform.fact import FactRecord
from psycopg.errors import UniqueViolation
from psycopg.rows import tuple_row
from psycopg_pool import ConnectionPool

from dfip_db.connection import transaction
from dfip_db.mapping import (
    FACT_COLUMNS,
    FACT_SELECT,
    FACT_WIRE_COLUMNS,
    as_datetime,
    as_decimal,
    as_optional_date,
    as_uuid_text,
    fact_from_row,
    fact_values,
)
from dfip_db.rls import current_rls

_MONTH_EXPR = "COALESCE(month_start, (date_trunc('month', day))::date)"
_OVERVIEW_SUM_SELECT = ", ".join(f"SUM({name}) AS {name}" for name in ADDITIVE_MEASURES)

_SNAPSHOT_INSERT_PREFIX = f"INSERT INTO publication_fact (publication_id, {FACT_SELECT}) VALUES "
_SNAPSHOT_VALUE_WIDTH = 1 + len(FACT_COLUMNS)
_NEWEST_WINS_SELECT = f"""
            SELECT DISTINCT ON (pf.campaign_id, pf.variation_id_key, pf.day)
                   {", ".join(f"pf.{column}" for column in FACT_COLUMNS)}
            FROM publication_fact pf
            INNER JOIN publication p ON p.id = pf.publication_id
            WHERE pf.client_id = %s
              AND p.client_id = %s
              AND p.snapshot_status = %s
            ORDER BY pf.campaign_id, pf.variation_id_key, pf.day,
                     p.published_at DESC, p.id DESC
"""
_DISTINCT_ON_PAGE_SQL = f"""
                WITH newest AS MATERIALIZED (
                    {_NEWEST_WINS_SELECT}
                ),
                stats AS (
                    SELECT COUNT(*) AS n FROM newest
                )
                SELECT stats.n AS _total, page.*
                FROM stats
                LEFT JOIN LATERAL (
                    SELECT {FACT_SELECT}
                    FROM newest
                    ORDER BY day, campaign_id, variation_id_key, client_id
                    LIMIT %s OFFSET %s
                ) page ON true
"""
_REBUILD_HISTORY_SQL = f"""
        INSERT INTO publication_history_grain ({FACT_SELECT})
        {_NEWEST_WINS_SELECT}
"""


def _text_predicate(column: str, values: tuple[str, ...]) -> tuple[str, list[Any]]:
    """Allowlisted column IN list. Empty selection means no predicate (all)."""
    if not values:
        return "", []
    include_blank = any(item == "" for item in values)
    nonempty = [item for item in values if item != ""]
    parts: list[str] = []
    params: list[Any] = []
    if nonempty:
        parts.append(f"{column} = ANY(%s)")
        params.append(list(nonempty))
    if include_blank:
        parts.append(f"({column} IS NULL OR {column} = '')")
    return "(" + " OR ".join(parts) + ")", params


def _history_where(
    client_id: str,
    day_from: date,
    day_to_exclusive: date,
    *,
    campaign_ids: tuple[str, ...] = (),
    channels: tuple[str, ...] = (),
    filter_logic_1: tuple[str, ...] = (),
    filter_logic_1_group: tuple[str, ...] = (),
    include_client: bool = True,
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if include_client:
        clauses.append("client_id = %s")
        params.append(client_id)
    clauses.append("day >= %s")
    params.append(day_from)
    clauses.append("day < %s")
    params.append(day_to_exclusive)
    for column, values in (
        ("campaign_id", campaign_ids),
        ("channel", channels),
        ("filter_logic_1", filter_logic_1),
        ("filter_logic_1_group", filter_logic_1_group),
    ):
        sql, extra = _text_predicate(column, values)
        if sql:
            clauses.append(sql)
            params.extend(extra)
    return " AND ".join(clauses), params


def _group_dimension_sql(dimension: str) -> tuple[str, str] | None:
    """Allowlisted GROUP BY expression and label expression. Never interpolates request field names."""
    from dfip_analytics.filters import DIMENSION_COLUMNS

    if dimension == "day":
        return "day::text", "day::text"
    column = DIMENSION_COLUMNS.get(dimension)
    if column is None:
        return None
    dim_sql = f"COALESCE({column}, '')"
    label_sql = "campaign_name" if dimension == "campaign_id" else column
    return dim_sql, label_sql


def _publication_from_row(row: dict[str, Any]) -> PublicationRecord:
    snapshot_count = row.get("snapshot_row_count")
    return PublicationRecord(
        id=as_uuid_text(row["id"]),
        client_id=as_uuid_text(row["client_id"]),
        processing_run_id=as_uuid_text(row["processing_run_id"]),
        period_start=as_optional_date(row["period_start"]),
        period_end=as_optional_date(row["period_end"]),
        published_at=as_datetime(row["published_at"]),
        published_by=row["published_by"],
        notes=row["notes"],
        fact_scope=row.get("fact_scope") or FACT_SCOPE_PROCESSING_RUN,
        snapshot_status=row.get("snapshot_status") or SNAPSHOT_STATUS_NONE,
        snapshot_row_count=None if snapshot_count is None else int(snapshot_count),
    )


def _current_from_row(row: dict[str, Any]) -> PublicationCurrentRecord:
    return PublicationCurrentRecord(
        client_id=as_uuid_text(row["client_id"]),
        publication_id=as_uuid_text(row["publication_id"]),
        updated_at=as_datetime(row["updated_at"]),
    )


class PostgresPublicationStore:
    def __init__(
        self,
        pool: ConnectionPool,
        *,
        use_history_serving_table: bool = True,
    ) -> None:
        self._pool = pool
        self._use_history_serving_table = bool(use_history_serving_table)
        self.supports_live_fact_copy = True

    def _tx(self):
        return transaction(self._pool, current_rls())

    def create(
        self,
        *,
        client_id: str,
        processing_run_id: str,
        period_start: date | None,
        period_end: date | None,
        published_by: str | None,
        notes: str | None,
        fact_scope: str = FACT_SCOPE_PROCESSING_RUN,
        snapshot_facts: Sequence[FactRecord] | None = None,
        on_progress=None,
    ) -> tuple[PublicationRecord, PublicationCurrentRecord]:
        now = datetime.now(tz=UTC)
        new_id = str(uuid4())

        def _note(stage: str, message: str) -> None:
            if on_progress is not None:
                on_progress(stage, message)

        with self._tx() as conn:
            if snapshot_facts is None:
                expected = _count_live_facts(
                    conn,
                    client_id=client_id,
                    processing_run_id=processing_run_id,
                    period_start=period_start,
                    period_end=period_end,
                    fact_scope=fact_scope,
                )
            else:
                expected = len(snapshot_facts)
            _note("creating", "Creating publication")
            publication_row = conn.execute(
                """
                INSERT INTO publication (
                    id, client_id, processing_run_id, period_start, period_end,
                    published_at, published_by, notes, fact_scope,
                    snapshot_status, snapshot_row_count
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (
                    new_id,
                    client_id,
                    processing_run_id,
                    period_start,
                    period_end,
                    now,
                    published_by,
                    notes,
                    fact_scope,
                    SNAPSHOT_STATUS_COMPLETE,
                    expected,
                ),
            ).fetchone()
            if snapshot_facts is None:
                written = _copy_live_facts(
                    conn,
                    publication_id=new_id,
                    client_id=client_id,
                    processing_run_id=processing_run_id,
                    period_start=period_start,
                    period_end=period_end,
                    fact_scope=fact_scope,
                )
            else:
                written = _insert_snapshot_chunks(conn, new_id, snapshot_facts)
            stored = conn.execute(
                "SELECT COUNT(*) AS n FROM publication_fact WHERE publication_id = %s",
                (new_id,),
            ).fetchone()
            actual = int(stored["n"] if stored else 0)
            if actual != written or actual != expected:
                raise PersistenceUnavailableError("Publication snapshot was incomplete.")
            pointer_row = conn.execute(
                """
                INSERT INTO publication_current (client_id, publication_id, updated_at)
                VALUES (%s, %s, %s)
                ON CONFLICT (client_id) DO UPDATE
                SET publication_id = EXCLUDED.publication_id,
                    updated_at = EXCLUDED.updated_at
                RETURNING *
                """,
                (client_id, new_id, now),
            ).fetchone()
            _note("history", "Updating historical serving data")
            _rebuild_published_history_grains(conn, client_id)
            _note("finalizing", "Finalizing")
        assert publication_row is not None
        assert pointer_row is not None
        return _publication_from_row(publication_row), _current_from_row(pointer_row)

    def has_publication_for_runs(self, run_ids: Sequence[str]) -> bool:
        ids = []
        for item in run_ids:
            if not item:
                continue
            try:
                ids.append(UUID(str(item)))
            except (TypeError, ValueError):
                continue
        if not ids:
            return False
        with self._tx() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM publication
                WHERE processing_run_id = ANY(%s)
                LIMIT 1
                """,
                (ids,),
            ).fetchone()
        return row is not None

    def get(self, publication_id: str) -> PublicationRecord | None:
        with self._tx() as conn:
            row = conn.execute(
                "SELECT * FROM publication WHERE id = %s",
                (publication_id,),
            ).fetchone()
        if row is None:
            return None
        return _publication_from_row(row)

    def get_current(
        self, client_id: str
    ) -> tuple[PublicationRecord | None, PublicationCurrentRecord | None]:
        with self._tx() as conn:
            pointer_row = conn.execute(
                "SELECT * FROM publication_current WHERE client_id = %s",
                (client_id,),
            ).fetchone()
            if pointer_row is None:
                return None, None
            publication_row = conn.execute(
                "SELECT * FROM publication WHERE id = %s",
                (pointer_row["publication_id"],),
            ).fetchone()
        if publication_row is None:
            return None, _current_from_row(pointer_row)
        return _publication_from_row(publication_row), _current_from_row(pointer_row)

    def list_for_client(self, client_id: str) -> list[PublicationRecord]:
        with self._tx() as conn:
            rows = conn.execute(
                """
                SELECT * FROM publication
                WHERE client_id = %s
                ORDER BY published_at ASC, id ASC
                """,
                (client_id,),
            ).fetchall()
        return [_publication_from_row(row) for row in rows]

    def list_snapshot(
        self,
        publication_id: str,
        *,
        client_id: str,
        limit: int,
        offset: int,
    ) -> tuple[list[FactRecord], int]:
        with self._tx() as conn:
            total_row = conn.execute(
                """
                SELECT COUNT(*) AS n FROM publication_fact
                WHERE publication_id = %s AND client_id = %s
                """,
                (publication_id, client_id),
            ).fetchone()
            total = int(total_row["n"] if total_row else 0)
            rows = conn.execute(
                f"""
                SELECT {FACT_SELECT} FROM publication_fact
                WHERE publication_id = %s AND client_id = %s
                ORDER BY day, campaign_id, variation_id_key, client_id
                LIMIT %s OFFSET %s
                """,
                (publication_id, client_id, limit, offset),
            ).fetchall()
        return [fact_from_row(row) for row in rows], total

    def list_published_history(
        self,
        client_id: str,
        *,
        limit: int,
        offset: int,
    ) -> tuple[list[FactRecord], int]:
        """Union complete snapshots for one client; newest publication wins per grain."""
        if self._use_history_serving_table:
            return self._list_published_history_serving(client_id, limit=limit, offset=offset)
        return self.list_published_history_distinct_on(client_id, limit=limit, offset=offset)

    def list_published_history_distinct_on(
        self,
        client_id: str,
        *,
        limit: int,
        offset: int,
    ) -> tuple[list[FactRecord], int]:
        """Original per-page DISTINCT ON newest-wins path. Kept for fallback."""
        with self._tx() as conn:
            rows = conn.execute(
                _DISTINCT_ON_PAGE_SQL,
                (
                    client_id,
                    client_id,
                    SNAPSHOT_STATUS_COMPLETE,
                    limit,
                    offset,
                ),
            ).fetchall()
        if not rows:
            return [], 0
        total = int(rows[0]["_total"] or 0)
        facts: list[FactRecord] = []
        for row in rows:
            if row.get("client_id") is None:
                continue
            facts.append(fact_from_row(row))
        return facts, total

    def _list_published_history_serving(
        self,
        client_id: str,
        *,
        limit: int,
        offset: int,
    ) -> tuple[list[FactRecord], int]:
        with self._tx() as conn:
            total_row = conn.execute(
                """
                SELECT COUNT(*) AS n
                FROM publication_history_grain
                WHERE client_id = %s
                """,
                (client_id,),
            ).fetchone()
            total = int(total_row["n"] if total_row else 0)
            if total == 0:
                return [], 0
            rows = conn.execute(
                f"""
                SELECT {FACT_SELECT}
                FROM publication_history_grain
                WHERE client_id = %s
                ORDER BY day, campaign_id, variation_id_key, client_id
                LIMIT %s OFFSET %s
                """,
                (client_id, limit, offset),
            ).fetchall()
        return [fact_from_row(row) for row in rows], total

    def list_published_month_starts(self, client_id: str) -> list[date]:
        sql = f"""
            SELECT DISTINCT {_MONTH_EXPR} AS month_start
            FROM publication_history_grain
            WHERE client_id = %s
            ORDER BY 1
        """
        fallback = f"""
            SELECT DISTINCT {_MONTH_EXPR} AS month_start
            FROM (
                {_NEWEST_WINS_SELECT}
            ) newest
            ORDER BY 1
        """
        with self._tx() as conn:
            if self._use_history_serving_table:
                rows = conn.execute(sql, (client_id,)).fetchall()
            else:
                rows = conn.execute(
                    fallback,
                    (client_id, client_id, SNAPSHOT_STATUS_COMPLETE),
                ).fetchall()
        months: list[date] = []
        for row in rows:
            value = as_optional_date(row["month_start"])
            if value is not None:
                months.append(value)
        return months

    def sum_published_history_month(
        self, client_id: str, month_start: date
    ) -> tuple[dict[str, object], int, date | None, date | None]:
        from dfip_analytics.filters import add_calendar_month

        start = date(month_start.year, month_start.month, 1)
        return self.sum_published_history(
            client_id,
            day_from=start,
            day_to_exclusive=add_calendar_month(start),
        )

    def published_day_bounds(self, client_id: str) -> tuple[date | None, date | None]:
        sql = """
            SELECT MIN(day) AS day_min, MAX(day) AS day_max
            FROM publication_history_grain
            WHERE client_id = %s
        """
        fallback = f"""
            SELECT MIN(day) AS day_min, MAX(day) AS day_max
            FROM (
                {_NEWEST_WINS_SELECT}
            ) newest
        """
        with self._tx() as conn:
            if self._use_history_serving_table:
                row = conn.execute(sql, (client_id,)).fetchone()
            else:
                row = conn.execute(
                    fallback,
                    (client_id, client_id, SNAPSHOT_STATUS_COMPLETE),
                ).fetchone()
        if row is None:
            return None, None
        return as_optional_date(row["day_min"]), as_optional_date(row["day_max"])

    def sum_published_history(
        self,
        client_id: str,
        *,
        day_from: date,
        day_to_exclusive: date,
        campaign_ids: tuple[str, ...] = (),
        channels: tuple[str, ...] = (),
        filter_logic_1: tuple[str, ...] = (),
        filter_logic_1_group: tuple[str, ...] = (),
    ) -> tuple[dict[str, object], int, date | None, date | None]:
        where, params = _history_where(
            client_id,
            day_from,
            day_to_exclusive,
            campaign_ids=campaign_ids,
            channels=channels,
            filter_logic_1=filter_logic_1,
            filter_logic_1_group=filter_logic_1_group,
            include_client=True,
        )
        serving = f"""
            SELECT
                COUNT(*)::bigint AS grain_row_count,
                MIN(day) AS day_min,
                MAX(day) AS day_max,
                {_OVERVIEW_SUM_SELECT}
            FROM publication_history_grain
            WHERE {where}
        """
        fb_where, fb_params = _history_where(
            client_id,
            day_from,
            day_to_exclusive,
            campaign_ids=campaign_ids,
            channels=channels,
            filter_logic_1=filter_logic_1,
            filter_logic_1_group=filter_logic_1_group,
            include_client=False,
        )
        fallback = f"""
            SELECT
                COUNT(*)::bigint AS grain_row_count,
                MIN(day) AS day_min,
                MAX(day) AS day_max,
                {_OVERVIEW_SUM_SELECT}
            FROM (
                {_NEWEST_WINS_SELECT}
            ) newest
            WHERE {fb_where}
        """
        with self._tx() as conn:
            if self._use_history_serving_table:
                row = conn.execute(serving, params).fetchone()
            else:
                row = conn.execute(
                    fallback,
                    (client_id, client_id, SNAPSHOT_STATUS_COMPLETE, *fb_params),
                ).fetchone()
        if row is None:
            return {}, 0, None, None
        measures = {name: as_decimal(row[name]) for name in ADDITIVE_MEASURES}
        count = int(row["grain_row_count"] or 0)
        return measures, count, as_optional_date(row["day_min"]), as_optional_date(row["day_max"])

    def list_published_filter_values(
        self,
        client_id: str,
        *,
        day_from: date,
        day_to_exclusive: date,
        dimension: str,
    ) -> list[tuple[str, str]]:
        from dfip_analytics.filters import DIMENSION_COLUMNS

        column = DIMENSION_COLUMNS.get(dimension)
        if column is None:
            return []
        label_expr = "campaign_name" if dimension == "campaign_id" else column
        where, params = _history_where(client_id, day_from, day_to_exclusive, include_client=True)
        serving = f"""
            SELECT DISTINCT {column} AS value, {label_expr} AS label
            FROM publication_history_grain
            WHERE {where}
            ORDER BY 2 NULLS LAST, 1 NULLS FIRST
        """
        fb_where, fb_params = _history_where(
            client_id, day_from, day_to_exclusive, include_client=False
        )
        fallback = f"""
            SELECT DISTINCT {column} AS value, {label_expr} AS label
            FROM (
                {_NEWEST_WINS_SELECT}
            ) newest
            WHERE {fb_where}
            ORDER BY 2 NULLS LAST, 1 NULLS FIRST
        """
        with self._tx() as conn:
            if self._use_history_serving_table:
                rows = conn.execute(serving, params).fetchall()
            else:
                rows = conn.execute(
                    fallback,
                    (client_id, client_id, SNAPSHOT_STATUS_COMPLETE, *fb_params),
                ).fetchall()
        out: list[tuple[str, str]] = []
        seen: set[str] = set()
        for row in rows:
            raw = row["value"]
            value = "" if raw is None else str(raw)
            if value in seen:
                continue
            seen.add(value)
            label_raw = row["label"]
            label = str(label_raw) if label_raw not in (None, "") else (value or "(blank)")
            if value == "" and label_raw in (None, ""):
                label = "(blank)"
            out.append((value, label))
        return out

    def list_published_history_series(
        self,
        client_id: str,
        *,
        day_from: date,
        day_to_exclusive: date,
        grain: str,
        breakdown: str | None = None,
        rank_measure: str | None = None,
        limit_series: int | None = None,
        campaign_ids: tuple[str, ...] = (),
        channels: tuple[str, ...] = (),
        filter_logic_1: tuple[str, ...] = (),
        filter_logic_1_group: tuple[str, ...] = (),
    ) -> tuple[list[tuple[date, str | None, str | None, dict[str, object], int]], int]:
        from dfip_analytics.filters import DIMENSION_COLUMNS
        from dfip_analytics.trends import BUCKET_SQL, MAX_BREAKDOWN_SERIES, OTHER_SERIES_KEY

        bucket_expr = BUCKET_SQL.get(grain)
        if bucket_expr is None:
            return [], 0
        rank_col = rank_measure if rank_measure in ADDITIVE_MEASURES else "total_cost"
        cap = limit_series if limit_series and limit_series > 0 else MAX_BREAKDOWN_SERIES
        where, params = _history_where(
            client_id,
            day_from,
            day_to_exclusive,
            campaign_ids=campaign_ids,
            channels=channels,
            filter_logic_1=filter_logic_1,
            filter_logic_1_group=filter_logic_1_group,
            include_client=True,
        )
        fb_where, fb_params = _history_where(
            client_id,
            day_from,
            day_to_exclusive,
            campaign_ids=campaign_ids,
            channels=channels,
            filter_logic_1=filter_logic_1,
            filter_logic_1_group=filter_logic_1_group,
            include_client=False,
        )
        newest_params = (client_id, client_id, SNAPSHOT_STATUS_COMPLETE)

        def parse(
            rows: list[Any],
        ) -> list[tuple[date, str | None, str | None, dict[str, object], int]]:
            out: list[tuple[date, str | None, str | None, dict[str, object], int]] = []
            for row in rows:
                bucket = as_optional_date(row["bucket"])
                if bucket is None:
                    continue
                raw_dim = row["dim_value"]
                dim = None if raw_dim is None else str(raw_dim)
                raw_label = row["dim_label"]
                label = None if raw_label is None else str(raw_label)
                measures = {name: as_decimal(row[name]) for name in ADDITIVE_MEASURES}
                count = int(row["grain_row_count"] or 0)
                out.append((bucket, dim, label, measures, count))
            return out

        if not breakdown:
            serving = f"""
                SELECT
                    {bucket_expr} AS bucket,
                    NULL::text AS dim_value,
                    NULL::text AS dim_label,
                    COUNT(*)::bigint AS grain_row_count,
                    {_OVERVIEW_SUM_SELECT}
                FROM publication_history_grain
                WHERE {where}
                GROUP BY 1
                ORDER BY 1
            """
            fallback = f"""
                SELECT
                    {bucket_expr} AS bucket,
                    NULL::text AS dim_value,
                    NULL::text AS dim_label,
                    COUNT(*)::bigint AS grain_row_count,
                    {_OVERVIEW_SUM_SELECT}
                FROM (
                    {_NEWEST_WINS_SELECT}
                ) newest
                WHERE {fb_where}
                GROUP BY 1
                ORDER BY 1
            """
            with self._tx() as conn:
                if self._use_history_serving_table:
                    rows = conn.execute(serving, params).fetchall()
                else:
                    rows = conn.execute(fallback, (*newest_params, *fb_params)).fetchall()
            parsed = parse(rows)
            return parsed, 1 if parsed else 0

        column = DIMENSION_COLUMNS.get(breakdown)
        if column is None:
            return [], 0
        label_expr = "campaign_name" if breakdown == "campaign_id" else column
        dim_sql = f"COALESCE({column}, '')"
        serving_distinct = f"""
            SELECT COUNT(DISTINCT {dim_sql}) AS n
            FROM publication_history_grain
            WHERE {where}
        """
        fallback_distinct = f"""
            SELECT COUNT(DISTINCT {dim_sql}) AS n
            FROM (
                {_NEWEST_WINS_SELECT}
            ) newest
            WHERE {fb_where}
        """
        serving = f"""
            WITH ranked AS (
                SELECT {dim_sql} AS dim_value
                FROM publication_history_grain
                WHERE {where}
                GROUP BY 1
                ORDER BY SUM({rank_col}) DESC NULLS LAST, 1
                LIMIT %s
            )
            SELECT
                {bucket_expr} AS bucket,
                CASE WHEN r.dim_value IS NOT NULL THEN {dim_sql} ELSE %s END AS dim_value,
                MAX(
                    CASE
                        WHEN r.dim_value IS NULL THEN 'Other'
                        WHEN COALESCE({label_expr}::text, '') = '' THEN '(blank)'
                        ELSE {label_expr}::text
                    END
                ) AS dim_label,
                COUNT(*)::bigint AS grain_row_count,
                {_OVERVIEW_SUM_SELECT}
            FROM publication_history_grain
            LEFT JOIN ranked r ON r.dim_value IS NOT DISTINCT FROM {dim_sql}
            WHERE {where}
            GROUP BY 1, 2
            ORDER BY 1, 2
        """
        fallback = f"""
            WITH newest AS (
                {_NEWEST_WINS_SELECT}
            ),
            ranked AS (
                SELECT {dim_sql} AS dim_value
                FROM newest
                WHERE {fb_where}
                GROUP BY 1
                ORDER BY SUM({rank_col}) DESC NULLS LAST, 1
                LIMIT %s
            )
            SELECT
                {bucket_expr} AS bucket,
                CASE WHEN r.dim_value IS NOT NULL THEN {dim_sql} ELSE %s END AS dim_value,
                MAX(
                    CASE
                        WHEN r.dim_value IS NULL THEN 'Other'
                        WHEN COALESCE({label_expr}::text, '') = '' THEN '(blank)'
                        ELSE {label_expr}::text
                    END
                ) AS dim_label,
                COUNT(*)::bigint AS grain_row_count,
                {_OVERVIEW_SUM_SELECT}
            FROM newest
            LEFT JOIN ranked r ON r.dim_value IS NOT DISTINCT FROM {dim_sql}
            WHERE {fb_where}
            GROUP BY 1, 2
            ORDER BY 1, 2
        """
        serving_params = [*params, cap, OTHER_SERIES_KEY, *params]
        fallback_params = [*newest_params, *fb_params, cap, OTHER_SERIES_KEY, *fb_params]
        with self._tx() as conn:
            if self._use_history_serving_table:
                distinct_row = conn.execute(serving_distinct, params).fetchone()
                rows = conn.execute(serving, serving_params).fetchall()
            else:
                distinct_row = conn.execute(
                    fallback_distinct, (*newest_params, *fb_params)
                ).fetchone()
                rows = conn.execute(fallback, fallback_params).fetchall()
        distinct = int(distinct_row["n"] if distinct_row else 0)
        return parse(rows), distinct

    def list_published_history_groups(
        self,
        client_id: str,
        *,
        day_from: date,
        day_to_exclusive: date,
        dimension: str,
        campaign_ids: tuple[str, ...] = (),
        channels: tuple[str, ...] = (),
        filter_logic_1: tuple[str, ...] = (),
        filter_logic_1_group: tuple[str, ...] = (),
    ) -> list[tuple[str, str, dict[str, object], int]]:
        from dfip_analytics.filters import DIMENSION_COLUMNS

        if dimension == "day":
            dim_sql = "day::text"
            label_sql = "day::text"
        else:
            column = DIMENSION_COLUMNS.get(dimension)
            if column is None:
                return []
            dim_sql = f"COALESCE({column}, '')"
            label_sql = "campaign_name" if dimension == "campaign_id" else column
        where, params = _history_where(
            client_id,
            day_from,
            day_to_exclusive,
            campaign_ids=campaign_ids,
            channels=channels,
            filter_logic_1=filter_logic_1,
            filter_logic_1_group=filter_logic_1_group,
            include_client=True,
        )
        fb_where, fb_params = _history_where(
            client_id,
            day_from,
            day_to_exclusive,
            campaign_ids=campaign_ids,
            channels=channels,
            filter_logic_1=filter_logic_1,
            filter_logic_1_group=filter_logic_1_group,
            include_client=False,
        )
        newest_params = (client_id, client_id, SNAPSHOT_STATUS_COMPLETE)
        serving = f"""
            SELECT
                {dim_sql} AS dim_value,
                MAX(
                    CASE
                        WHEN COALESCE({label_sql}::text, '') = '' THEN '(blank)'
                        ELSE {label_sql}::text
                    END
                ) AS dim_label,
                COUNT(*)::bigint AS grain_row_count,
                {_OVERVIEW_SUM_SELECT}
            FROM publication_history_grain
            WHERE {where}
            GROUP BY 1
        """
        fallback = f"""
            SELECT
                {dim_sql} AS dim_value,
                MAX(
                    CASE
                        WHEN COALESCE({label_sql}::text, '') = '' THEN '(blank)'
                        ELSE {label_sql}::text
                    END
                ) AS dim_label,
                COUNT(*)::bigint AS grain_row_count,
                {_OVERVIEW_SUM_SELECT}
            FROM (
                {_NEWEST_WINS_SELECT}
            ) newest
            WHERE {fb_where}
            GROUP BY 1
        """
        with self._tx() as conn:
            if self._use_history_serving_table:
                rows = conn.execute(serving, params).fetchall()
            else:
                rows = conn.execute(fallback, (*newest_params, *fb_params)).fetchall()
        out: list[tuple[str, str, dict[str, object], int]] = []
        for row in rows:
            raw_dim = row["dim_value"]
            value = "" if raw_dim is None else str(raw_dim)
            raw_label = row["dim_label"]
            label = str(raw_label) if raw_label not in (None, "") else (value or "(blank)")
            measures = {name: as_decimal(row[name]) for name in ADDITIVE_MEASURES}
            count = int(row["grain_row_count"] or 0)
            out.append((value, label, measures, count))
        return out

    def list_published_history_pairs(
        self,
        client_id: str,
        *,
        day_from: date,
        day_to_exclusive: date,
        primary: str,
        secondary: str,
        campaign_ids: tuple[str, ...] = (),
        channels: tuple[str, ...] = (),
        filter_logic_1: tuple[str, ...] = (),
        filter_logic_1_group: tuple[str, ...] = (),
    ) -> list[tuple[str, str, str, str, dict[str, object], int]]:
        primary_sql = _group_dimension_sql(primary)
        secondary_sql = _group_dimension_sql(secondary)
        if primary_sql is None or secondary_sql is None:
            return []
        p_dim, p_label = primary_sql
        s_dim, s_label = secondary_sql
        where, params = _history_where(
            client_id,
            day_from,
            day_to_exclusive,
            campaign_ids=campaign_ids,
            channels=channels,
            filter_logic_1=filter_logic_1,
            filter_logic_1_group=filter_logic_1_group,
            include_client=True,
        )
        fb_where, fb_params = _history_where(
            client_id,
            day_from,
            day_to_exclusive,
            campaign_ids=campaign_ids,
            channels=channels,
            filter_logic_1=filter_logic_1,
            filter_logic_1_group=filter_logic_1_group,
            include_client=False,
        )
        newest_params = (client_id, client_id, SNAPSHOT_STATUS_COMPLETE)
        serving = f"""
            SELECT
                {p_dim} AS primary_value,
                MAX(
                    CASE
                        WHEN COALESCE({p_label}::text, '') = '' THEN '(blank)'
                        ELSE {p_label}::text
                    END
                ) AS primary_label,
                {s_dim} AS secondary_value,
                MAX(
                    CASE
                        WHEN COALESCE({s_label}::text, '') = '' THEN '(blank)'
                        ELSE {s_label}::text
                    END
                ) AS secondary_label,
                COUNT(*)::bigint AS grain_row_count,
                {_OVERVIEW_SUM_SELECT}
            FROM publication_history_grain
            WHERE {where}
            GROUP BY 1, 3
        """
        fallback = f"""
            SELECT
                {p_dim} AS primary_value,
                MAX(
                    CASE
                        WHEN COALESCE({p_label}::text, '') = '' THEN '(blank)'
                        ELSE {p_label}::text
                    END
                ) AS primary_label,
                {s_dim} AS secondary_value,
                MAX(
                    CASE
                        WHEN COALESCE({s_label}::text, '') = '' THEN '(blank)'
                        ELSE {s_label}::text
                    END
                ) AS secondary_label,
                COUNT(*)::bigint AS grain_row_count,
                {_OVERVIEW_SUM_SELECT}
            FROM (
                {_NEWEST_WINS_SELECT}
            ) newest
            WHERE {fb_where}
            GROUP BY 1, 3
        """
        with self._tx() as conn:
            if self._use_history_serving_table:
                rows = conn.execute(serving, params).fetchall()
            else:
                rows = conn.execute(fallback, (*newest_params, *fb_params)).fetchall()
        out: list[tuple[str, str, str, str, dict[str, object], int]] = []
        for row in rows:
            pvalue = "" if row["primary_value"] is None else str(row["primary_value"])
            svalue = "" if row["secondary_value"] is None else str(row["secondary_value"])
            raw_plabel = row["primary_label"]
            raw_slabel = row["secondary_label"]
            plabel = str(raw_plabel) if raw_plabel not in (None, "") else (pvalue or "(blank)")
            slabel = str(raw_slabel) if raw_slabel not in (None, "") else (svalue or "(blank)")
            measures = {name: as_decimal(row[name]) for name in ADDITIVE_MEASURES}
            count = int(row["grain_row_count"] or 0)
            out.append((pvalue, plabel, svalue, slabel, measures, count))
        return out

    def copy_published_history_csv(self, client_id: str, *, max_rows: int) -> bytes:
        """Stream newest-wins history as CSV without building FactRecord objects."""
        cid = str(UUID(client_id))
        limit = int(max_rows)
        if limit < 1:
            raise ValueError("max_rows must be positive.")
        cols = ", ".join(FACT_WIRE_COLUMNS)
        if self._use_history_serving_table:
            inner = (
                f"SELECT {cols} FROM publication_history_grain "
                f"WHERE client_id = '{cid}'::uuid "
                f"ORDER BY day, campaign_id, variation_id_key, client_id "
                f"LIMIT {limit}"
            )
        else:
            inner_pf = ", ".join(f"pf.{name}" for name in FACT_WIRE_COLUMNS)
            inner = (
                f"SELECT {cols} FROM ("
                f"SELECT DISTINCT ON (pf.campaign_id, pf.variation_id_key, pf.day) "
                f"{inner_pf} "
                f"FROM publication_fact pf "
                f"INNER JOIN publication p ON p.id = pf.publication_id "
                f"WHERE pf.client_id = '{cid}'::uuid "
                f"AND p.client_id = '{cid}'::uuid "
                f"AND p.snapshot_status = '{SNAPSHOT_STATUS_COMPLETE}' "
                f"ORDER BY pf.campaign_id, pf.variation_id_key, pf.day, "
                f"p.published_at DESC, p.id DESC"
                f") newest "
                f"ORDER BY day, campaign_id, variation_id_key, client_id "
                f"LIMIT {limit}"
            )
        copy_sql = f"COPY ({inner}) TO STDOUT WITH (FORMAT csv, HEADER true)"
        payload = bytearray()
        with self._tx() as conn:
            with conn.cursor(row_factory=tuple_row) as cur:
                with cur.copy(copy_sql) as copy:
                    while True:
                        chunk = copy.read()
                        if not chunk:
                            break
                        payload.extend(chunk)
        return bytes(payload)

    def rebuild_published_history(self, client_id: str) -> None:
        """Rebuild one tenant's serving table from complete snapshots."""
        with self._tx() as conn:
            _rebuild_published_history_grains(conn, client_id)


def _rebuild_published_history_grains(conn, client_id: str) -> None:
    conn.execute(
        "DELETE FROM publication_history_grain WHERE client_id = %s",
        (client_id,),
    )
    conn.execute(
        _REBUILD_HISTORY_SQL,
        (client_id, client_id, SNAPSHOT_STATUS_COMPLETE),
    )


def _insert_snapshot_chunks(conn, publication_id: str, facts: Sequence[FactRecord]) -> int:
    rows = list(facts)
    if not rows:
        return 0
    try:
        for offset in range(0, len(rows), SNAPSHOT_CHUNK_SIZE):
            chunk = rows[offset : offset + SNAPSHOT_CHUNK_SIZE]
            placeholders = ",".join(
                ["(" + ",".join(["%s"] * _SNAPSHOT_VALUE_WIDTH) + ")"] * len(chunk)
            )
            params: list[object] = []
            for fact in chunk:
                params.append(publication_id)
                params.extend(fact_values(fact))
            conn.execute(_SNAPSHOT_INSERT_PREFIX + placeholders, params)
    except UniqueViolation as exc:
        raise PersistenceUnavailableError(
            "Publication snapshot contains duplicate grains."
        ) from exc
    return len(rows)


def _live_fact_filter(
    *,
    client_id: str,
    processing_run_id: str,
    period_start: date | None,
    period_end: date | None,
    fact_scope: str,
) -> tuple[str, list[object]]:
    where = ["client_id = %s", "day IS NOT NULL"]
    params: list[object] = [client_id]
    if fact_scope != FACT_SCOPE_CLIENT_CURRENT:
        where.append("processing_run_id = %s")
        params.append(processing_run_id)
    if period_start is not None:
        where.append("day >= %s")
        params.append(period_start)
    if period_end is not None:
        where.append("day <= %s")
        params.append(period_end)
    return " AND ".join(where), params


def _count_live_facts(
    conn,
    *,
    client_id: str,
    processing_run_id: str,
    period_start: date | None,
    period_end: date | None,
    fact_scope: str,
) -> int:
    clause, params = _live_fact_filter(
        client_id=client_id,
        processing_run_id=processing_run_id,
        period_start=period_start,
        period_end=period_end,
        fact_scope=fact_scope,
    )
    counted = conn.execute(
        f"SELECT COUNT(*) AS n FROM fact_campaign_day WHERE {clause}",
        params,
    ).fetchone()
    return int(counted["n"] if counted else 0)


def _copy_live_facts(
    conn,
    *,
    publication_id: str,
    client_id: str,
    processing_run_id: str,
    period_start: date | None,
    period_end: date | None,
    fact_scope: str,
) -> int:
    clause, params = _live_fact_filter(
        client_id=client_id,
        processing_run_id=processing_run_id,
        period_start=period_start,
        period_end=period_end,
        fact_scope=fact_scope,
    )
    conn.execute(
        f"""
        INSERT INTO publication_fact (publication_id, {FACT_SELECT})
        SELECT %s, {FACT_SELECT}
        FROM fact_campaign_day
        WHERE {clause}
        """,
        [publication_id, *params],
    )
    counted = conn.execute(
        "SELECT COUNT(*) AS n FROM publication_fact WHERE publication_id = %s",
        (publication_id,),
    ).fetchone()
    return int(counted["n"] if counted else 0)
