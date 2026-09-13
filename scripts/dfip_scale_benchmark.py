"""Measure DFIP ingest/transform scaling. Does not skip QA or invent an SLA.

Usage:
  python scripts/dfip_scale_benchmark.py --sizes 1000,5000
  python scripts/dfip_scale_benchmark.py --sizes 50000,100000 --out tmp/scale_bench.json

HTTP 500K acceptance against a live API is a separate operator run.
This script times the same ingest_workbook + run_transformation path used by
upload, with optional PostgreSQL when DATABASE_URL is set.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import tracemalloc
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4

from openpyxl import Workbook

from dfip_api.qa_store import InMemoryQaFindingStore
from dfip_api.qa_workflow import evaluate_and_persist_run, processing_rls
from dfip_core.ingest.pipeline import DEFAULT_CLIENT_ID, STAGING_CHUNK_SIZE, ingest_workbook
from dfip_core.ingest.store import InMemoryIngestStore
from dfip_core.transform.engine import FACT_PERSIST_CHUNK_SIZE, run_transformation
from dfip_core.transform.store import InMemoryFactStore
from dfip_db.catalog import SOURCE_COLUMNS, WEB_ENGAGE_SOURCE_HEADERS

ROOT = Path(__file__).resolve().parents[1]
DERIVED_HEADERS = tuple(
    col.excel_header for col in SOURCE_COLUMNS if col.source_role == "derived_excel"
)
GROUP7_CAMPAIGN = "Campaign_May25_Trans_NLC_NTB_May'25"


def write_scale_workbook(
    path: Path,
    n_rows: int,
    *,
    day: date | None = None,
    campaign_prefix: str | None = None,
) -> tuple[Path, str]:
    """Write a representative Web Engage raw workbook with ``n_rows`` data rows.

    Campaign IDs are unique per file so repeated benches measure first-insert
    persistence, not restatement of leftover grains.
    """
    day = day or date(2025, 6, 1)
    prefix = campaign_prefix or uuid4().hex[:12]
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook(write_only=True)
    worksheet = workbook.create_sheet("Web-Engage Raw")
    worksheet.append(list(DERIVED_HEADERS) + list(WEB_ENGAGE_SOURCE_HEADERS))
    header_index = {name: offset for offset, name in enumerate(WEB_ENGAGE_SOURCE_HEADERS)}
    for index in range(n_rows):
        values = [None] * len(WEB_ENGAGE_SOURCE_HEADERS)
        values[header_index["Day"]] = day
        values[header_index["Campaign ID"]] = f"{prefix}-{index}"
        values[header_index["Variation ID"]] = "var-1"
        values[header_index["Campaign Name"]] = GROUP7_CAMPAIGN
        values[header_index["Channel"]] = "SMS"
        values[header_index["Sent"]] = 100
        values[header_index["Delivered"]] = 100
        values[header_index["Unique Impressions"]] = 100
        values[header_index["Unique Clicks"]] = 2
        worksheet.append([None] * len(DERIVED_HEADERS) + values)
    workbook.save(path)
    workbook.close()
    return path, prefix


def _peak_mb() -> float | None:
    current, peak = tracemalloc.get_traced_memory()
    if peak <= 0:
        return None
    return round(peak / (1024 * 1024), 2)


def run_one(path: Path, n_rows: int, *, postgres: bool = False) -> dict[str, object]:
    stages: dict[str, float] = {}
    last = {"t": time.perf_counter(), "stage": "start"}

    def on_progress(stage, current, total, message) -> None:
        now = time.perf_counter()
        key = stage
        stages[key] = stages.get(key, 0.0) + (now - last["t"])
        last["t"] = now
        last["stage"] = stage

    pool = None
    client_id = DEFAULT_CLIENT_ID
    ingest = None
    ingest_s = None
    transform_s = None
    qa_s = None
    run_status = None
    batch_status = None
    inserted = restated = unchanged = transformed_n = None
    if postgres:
        from dfip_config.settings import Settings
        from dfip_db.connection import close_pool, create_pool, open_pool
        from dfip_db.fact_store import PostgresFactStore
        from dfip_db.ingest_store import PostgresIngestStore
        from dfip_db.migrate import apply_migrations
        from psycopg import connect

        settings = Settings()
        if not settings.database_url:
            raise SystemExit("DATABASE_URL is not configured")
        with connect(settings.database_url) as conn:
            apply_migrations(conn)
            conn.commit()
        pool = create_pool(settings.database_url)
        open_pool(pool)
        store = PostgresIngestStore(pool)
        facts = PostgresFactStore(pool)
    else:
        store = InMemoryIngestStore()
        facts = InMemoryFactStore()
    qa_store = InMemoryQaFindingStore()
    tracemalloc.start()
    t0 = time.perf_counter()
    try:
        if postgres:
            context = processing_rls(client_id)
        else:
            from contextlib import nullcontext

            context = nullcontext()
        with context:
            ingest = ingest_workbook(
                path,
                store,
                client_id=client_id,
                force=True,
                on_progress=on_progress,
            )
            ingest_s = time.perf_counter() - t0
            batch_status = ingest.batch.status
            run_status = None if ingest.processing_run is None else ingest.processing_run.status
            if ingest.processing_run is not None:
                t1 = time.perf_counter()
                transformed = run_transformation(
                    store,
                    facts,
                    ingest.batch.id,
                    processing_run_id=ingest.processing_run.id,
                    on_progress=on_progress,
                )
                transform_s = time.perf_counter() - t1
                inserted = transformed.inserted
                restated = transformed.restated
                unchanged = transformed.unchanged
                transformed_n = transformed.transformed
                t2 = time.perf_counter()
                evaluate_and_persist_run(
                    ingest_store=store,
                    fact_store=facts,
                    qa_store=qa_store,
                    run=transformed.processing_run,
                    client_id=ingest.batch.client_id,
                    rate_card_version_labels=transformed.version_labels.get("rate_card"),
                    on_progress=on_progress,
                )
                qa_s = time.perf_counter() - t2
                refreshed = store.get_processing_run(ingest.processing_run.id)
                run_status = None if refreshed is None else refreshed.status
                latest_batch = store.get_batch(ingest.batch.id)
                if latest_batch is not None:
                    batch_status = latest_batch.status
    finally:
        if pool is not None:
            from dfip_db.connection import close_pool

            close_pool(pool)
    total_s = time.perf_counter() - t0
    peak = _peak_mb()
    tracemalloc.stop()
    if ingest is None:
        raise RuntimeError("ingest did not start")
    staging_commits = (
        0
        if ingest.staged_count == 0
        else (ingest.staged_count + STAGING_CHUNK_SIZE - 1) // STAGING_CHUNK_SIZE
    )
    persist_commits = (
        0
        if not transformed_n
        else (int(transformed_n) + FACT_PERSIST_CHUNK_SIZE - 1) // FACT_PERSIST_CHUNK_SIZE
    )
    return {
        "store": "postgres" if postgres else "memory",
        "rows_requested": n_rows,
        "byte_size": path.stat().st_size,
        "ingest_seconds": None if ingest_s is None else round(ingest_s, 3),
        "transform_seconds": None if transform_s is None else round(transform_s, 3),
        "qa_seconds": None if qa_s is None else round(qa_s, 3),
        "total_seconds": round(total_s, 3),
        "stage_seconds": {key: round(value, 3) for key, value in stages.items()},
        "peak_python_mb": peak,
        "staged": ingest.staged_count,
        "rejected": ingest.rejected_count,
        "empty": ingest.empty_row_count,
        "facts": transformed_n,
        "inserted": inserted,
        "restated": restated,
        "unchanged": unchanged,
        "estimated_staging_commits": staging_commits,
        "estimated_persist_commits": persist_commits,
        "batch_status": batch_status if batch_status is not None else ingest.batch.status,
        "run_status": run_status,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "run_id": str(uuid4()),
    }


def clean_seed_client_working_set(database_url: str, client_id: str) -> None:
    """Remove leftover bench rows for the seed client. Does not drop catalogs."""
    from psycopg import connect

    statements = (
        "DELETE FROM qa_finding WHERE client_id = %s",
        "DELETE FROM publication_history_grain WHERE client_id = %s",
        "DELETE FROM publication_fact WHERE client_id = %s",
        "DELETE FROM publication_current WHERE client_id = %s",
        "DELETE FROM publication WHERE client_id = %s",
        "DELETE FROM fact_campaign_day_history WHERE client_id = %s",
        "DELETE FROM fact_campaign_day WHERE client_id = %s",
        "DELETE FROM stg_source_row WHERE client_id = %s",
        "DELETE FROM excel_workbook_grant WHERE client_id = %s",
        """
        DELETE FROM stg_rejected_row
        WHERE batch_id IN (SELECT id FROM batch WHERE client_id = %s)
        """,
        """
        DELETE FROM processing_run
        WHERE batch_id IN (SELECT id FROM batch WHERE client_id = %s)
        """,
        "DELETE FROM batch WHERE client_id = %s",
        "DELETE FROM source_file WHERE client_id = %s",
    )
    with connect(database_url) as conn:
        for sql in statements:
            try:
                conn.execute(sql, (client_id,))
            except Exception as exc:  # noqa: BLE001 — table may be absent on older schemas
                conn.rollback()
                raise RuntimeError(f"seed-client cleanup failed: {exc}") from exc
        conn.execute("ANALYZE fact_campaign_day")
        conn.execute("ANALYZE stg_source_row")
        conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", default="1000", help="Comma-separated row counts")
    parser.add_argument("--out", default="", help="Optional JSON output path")
    parser.add_argument("--keep-files", action="store_true")
    parser.add_argument(
        "--postgres",
        action="store_true",
        help="Use DATABASE_URL PostgreSQL stores instead of in-memory",
    )
    parser.add_argument(
        "--clean-client",
        action="store_true",
        help="Delete leftover seed-client working-set rows before each PostgreSQL size",
    )
    args = parser.parse_args()
    sizes = [int(item.strip()) for item in args.sizes.split(",") if item.strip()]
    work = ROOT / "tmp" / "scale_bench"
    work.mkdir(parents=True, exist_ok=True)
    results = []
    for size in sizes:
        if args.postgres and args.clean_client:
            from dfip_config.settings import Settings

            settings = Settings()
            if not settings.database_url:
                raise SystemExit("DATABASE_URL is not configured")
            print(f"cleaning seed client working set before {size}", flush=True)
            clean_seed_client_working_set(settings.database_url, DEFAULT_CLIENT_ID)
        path = work / f"scale_{size}.xlsx"
        print(f"generating {size} rows -> {path}", flush=True)
        t_gen = time.perf_counter()
        path, prefix = write_scale_workbook(path, size)
        gen_s = time.perf_counter() - t_gen
        print(f"  generated in {gen_s:.1f}s ({path.stat().st_size} bytes)", flush=True)
        print("  ingest+transform...", flush=True)
        row = run_one(path, size, postgres=args.postgres)
        row["generate_seconds"] = round(gen_s, 3)
        row["campaign_prefix"] = prefix
        results.append(row)
        print(
            f"  staged={row['staged']} facts={row['facts']} inserted={row['inserted']} "
            f"restated={row['restated']} rejected={row['rejected']} "
            f"status={row['batch_status']} run={row['run_status']} "
            f"total={row['total_seconds']}s peak_mb={row['peak_python_mb']}",
            flush=True,
        )
        if not args.keep_files:
            path.unlink(missing_ok=True)
    payload = {
        "environment": os.getenv("DFIP_ENV", ""),
        "results": results,
    }
    text = json.dumps(payload, indent=2)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"wrote {out}", flush=True)
    else:
        print(text)


if __name__ == "__main__":
    main()
