"""In-memory vs PostgreSQL store contract tests."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from dfip_core.ingest import ingest_workbook
from dfip_core.transform.engine import run_transformation

from postgres_support import CLIENT_A, CLIENT_B, memory_pair, postgres_only, requires_postgres
from test_p3_ingest import _write_workbook

pytestmark = [postgres_only, requires_postgres]


def test_ingest_and_transform_match_memory(tmp_path: Path, pg_conn, pg_stores) -> None:
    ingest_pg, facts_pg, _pubs, _read = pg_stores
    ingest_mem, facts_mem, _ = memory_pair()
    path = _write_workbook(
        tmp_path / "contract.xlsx",
        [
            {
                "Day": date(2025, 8, 1),
                "Campaign ID": "camp-1",
                "Campaign Name": "Alpha",
                "Variation ID": "var-1",
                "Channel": "SMS",
                "Delivered": 10,
                "Template Name (WhatsApp)": "",
            }
        ],
    )
    mem_result = ingest_workbook(path, ingest_mem, client_id=CLIENT_A)
    pg_result = ingest_workbook(path, ingest_pg, client_id=CLIENT_A)
    assert mem_result.replayed is False
    assert pg_result.replayed is False
    assert mem_result.staged_count == pg_result.staged_count
    assert mem_result.rejected_count == pg_result.rejected_count
    assert mem_result.batch.status == pg_result.batch.status
    assert mem_result.source_file.sha256 == pg_result.source_file.sha256
    mem_staged = ingest_mem.staged_for_batch(mem_result.batch.id)
    pg_staged = ingest_pg.staged_for_batch(pg_result.batch.id)
    assert [row.raw for row in mem_staged] == [row.raw for row in pg_staged]

    mem_tx = run_transformation(
        ingest_mem,
        facts_mem,
        mem_result.batch.id,
        processing_run_id=mem_result.processing_run.id,
    )
    pg_tx = run_transformation(
        ingest_pg,
        facts_pg,
        pg_result.batch.id,
        processing_run_id=pg_result.processing_run.id,
    )
    assert mem_tx.transformed == pg_tx.transformed
    assert mem_tx.inserted == pg_tx.inserted
    mem_facts = sorted(facts_mem.list_current(), key=lambda item: item.key)
    pg_facts = sorted(facts_pg.list_current(), key=lambda item: item.key)
    assert len(mem_facts) == len(pg_facts) == 1

    def _semantic(record) -> dict:
        values = record.business_values()
        for generated in ("processing_run_id", "batch_id"):
            assert values.pop(generated)
        return values

    assert _semantic(mem_facts[0]) == _semantic(pg_facts[0])


def test_postgres_sha_is_per_client(tmp_path: Path, pg_conn, pg_stores) -> None:
    ingest_pg, _facts, _pubs, _read = pg_stores
    path = _write_workbook(
        tmp_path / "sha.xlsx",
        [{"Day": date(2025, 8, 1), "Campaign ID": "camp-1", "Campaign Name": "A"}],
    )
    first = ingest_workbook(path, ingest_pg, client_id=CLIENT_A)
    second = ingest_workbook(path, ingest_pg, client_id=CLIENT_B)
    assert first.source_file.id != second.source_file.id
    assert first.source_file.sha256 == second.source_file.sha256
    assert first.source_file.client_id == CLIENT_A
    assert second.source_file.client_id == CLIENT_B
    replay = ingest_workbook(path, ingest_pg, client_id=CLIENT_A)
    assert replay.replayed is True
    assert replay.source_file.id == first.source_file.id


def test_failed_batch_can_be_retried(tmp_path: Path, pg_conn, pg_stores) -> None:
    ingest_pg, _facts, _pubs, _read = pg_stores
    path = _write_workbook(
        tmp_path / "retry.xlsx",
        [{"Day": date(2025, 8, 1), "Campaign ID": "camp-1", "Campaign Name": "A"}],
        header_override={11: "Not Day"},
    )
    first = ingest_workbook(path, ingest_pg, client_id=CLIENT_A)
    assert first.replayed is False
    assert first.batch.status == "failed"
    second = ingest_workbook(path, ingest_pg, client_id=CLIENT_A)
    assert second.replayed is False
    assert second.batch.id != first.batch.id
    assert second.batch.status == "failed"
    assert second.source_file.id == first.source_file.id
