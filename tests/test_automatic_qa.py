"""Automatic QA after processing: verdict, persistence, isolation, publish gate."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from dfip_api.app import create_app
from dfip_api.qa_store import InMemoryQaFindingStore
from dfip_api.qa_workflow import evaluate_and_persist_run
from dfip_core.ingest.store import InMemoryIngestStore
from dfip_core.transform.fact import FactRecord
from dfip_core.transform.store import InMemoryFactStore
from fastapi.testclient import TestClient

from http_ingest_support import source_row, workbook_bytes
from test_p5_api import AUTH, CLIENT_ID, JWT_SECRET, RUN_A, _encode_jwt, make_settings, seed_stores
from test_p7_publication import publisher_settings
from test_v2_http_ingest import CLIENT_B, _publisher_app, _upload
from test_v2_http_qa import _error, _findings


class _BoomFactStore(InMemoryFactStore):
    def for_run(self, processing_run_id: str):
        raise RuntimeError("simulated qa load failure")


def _failed_gt_sent(**overrides: object) -> dict[str, object]:
    row = source_row(
        **{
            "Sent": 10,
            "Failed": 12,
            "Delivered": 8,
            "Unique Impressions": 7,
            "Unique Clicks": 1,
        }
    )
    row.update(overrides)
    return row


def test_qa_executes_after_processing_with_correct_run_and_client(tmp_path: Path) -> None:
    app = _publisher_app()
    http = TestClient(app)
    content = workbook_bytes(tmp_path / "a.xlsx", [_failed_gt_sent()])
    uploaded = _upload(http, content, "a.xlsx", client_id=CLIENT_ID)
    assert uploaded.status_code == 201
    run = uploaded.json()["processing_run"]
    assert run["status"] == "succeeded"
    assert run["qa_verdict"] == "warn"
    run_id = run["processing_run_id"]
    listed = _findings(http, run_id).json()
    assert listed["pagination"]["total"] >= 1
    assert listed["summary"]["total"] == listed["pagination"]["total"]
    assert all(item["processing_run_id"] == run_id for item in listed["items"])
    assert all(item["client_id"] == CLIENT_ID for item in listed["items"])
    assert "QA-IMPOSSIBLE-FAILED" in {item["rule_id"] for item in listed["items"]}


def test_zero_findings_persists_pass_not_null(tmp_path: Path) -> None:
    app = _publisher_app()
    http = TestClient(app)
    content = workbook_bytes(tmp_path / "clean.xlsx", [source_row()])
    uploaded = _upload(http, content, "clean.xlsx", client_id=CLIENT_ID)
    run = uploaded.json()["processing_run"]
    assert run["qa_verdict"] == "pass"
    listed = _findings(http, run["processing_run_id"]).json()
    assert listed["items"] == []
    assert listed["summary"]["total"] == 0
    persisted = http.get(
        f"/api/v1/processing-runs/{run['processing_run_id']}",
        headers=AUTH,
    ).json()
    assert persisted["qa_verdict"] == "pass"


def test_125_failed_gt_sent_cases_persist(tmp_path: Path) -> None:
    app = _publisher_app()
    http = TestClient(app)
    rows = [_failed_gt_sent(**{"Campaign ID": f"camp-{index}"}) for index in range(125)]
    content = workbook_bytes(tmp_path / "many.xlsx", rows)
    uploaded = _upload(http, content, "many.xlsx", client_id=CLIENT_ID)
    assert uploaded.status_code == 201
    run_id = uploaded.json()["processing_run"]["processing_run_id"]
    assert uploaded.json()["processing_run"]["qa_verdict"] == "warn"
    listed = _findings(http, run_id, headers=AUTH).json()
    # First page is capped; summary is the complete persisted set.
    assert listed["summary"]["by_rule"].get("QA-IMPOSSIBLE-FAILED") == 125
    assert listed["summary"]["error"] == 0
    refreshed = http.get(
        f"/api/v1/processing-runs/{run_id}/qa-findings",
        headers=AUTH,
        params={"limit": 200, "offset": 0},
    ).json()
    page_ids = [item["rule_id"] for item in refreshed["items"]]
    assert page_ids.count("QA-IMPOSSIBLE-FAILED") == 125


def test_qa_failure_is_unavailable_not_pass(tmp_path: Path) -> None:
    app = create_app(
        settings=publisher_settings(),
        ingest_store=InMemoryIngestStore(),
        fact_store=_BoomFactStore(),
        qa_store=InMemoryQaFindingStore(),
    )
    http = TestClient(app)
    content = workbook_bytes(tmp_path / "boom.xlsx", [source_row()])
    uploaded = _upload(http, content, "boom.xlsx", client_id=CLIENT_ID)
    assert uploaded.status_code == 201
    run = uploaded.json()["processing_run"]
    assert run["status"] == "succeeded"
    assert run["qa_verdict"] == "unavailable"
    listed = _findings(http, run["processing_run_id"]).json()
    assert listed["items"] == []
    assert listed["summary"]["total"] == 0
    blocked = http.post(
        "/api/v1/publications",
        headers=AUTH,
        json={"client_id": CLIENT_ID, "processing_run_id": run["processing_run_id"]},
    )
    assert blocked.status_code == 422
    assert _error(blocked)["message"] == "Processing run QA did not complete."


def test_null_verdict_cannot_publish() -> None:
    ingest, facts = seed_stores()
    ingest.processing_runs[RUN_A].qa_verdict = None
    app = create_app(
        settings=publisher_settings(),
        ingest_store=ingest,
        fact_store=facts,
    )
    blocked = TestClient(app).post(
        "/api/v1/publications",
        headers=AUTH,
        json={"client_id": CLIENT_ID, "processing_run_id": RUN_A},
    )
    assert blocked.status_code == 422
    assert _error(blocked)["message"] == "Processing run has no QA verdict."


def test_old_run_qa_unchanged_when_another_run_is_evaluated(tmp_path: Path) -> None:
    app = _publisher_app()
    http = TestClient(app)
    first = _upload(
        http,
        workbook_bytes(tmp_path / "first.xlsx", [_failed_gt_sent()]),
        "first.xlsx",
        client_id=CLIENT_ID,
    )
    second = _upload(
        http,
        workbook_bytes(tmp_path / "second.xlsx", [_failed_gt_sent(**{"Campaign ID": "camp-b"})]),
        "second.xlsx",
        client_id=CLIENT_ID,
    )
    run_a = first.json()["processing_run"]["processing_run_id"]
    run_b = second.json()["processing_run"]["processing_run_id"]
    before = _findings(http, run_a).json()
    again = http.post(f"/api/v1/processing-runs/{run_b}/qa", headers=AUTH)
    assert again.status_code == 200
    assert again.json()["qa_verdict"] == "warn"
    after = _findings(http, run_a).json()
    assert after["summary"] == before["summary"]
    assert [item["finding_id"] for item in after["items"]] == [
        item["finding_id"] for item in before["items"]
    ]
    assert after["items"][0]["processing_run_id"] == run_a


def test_repeated_qa_is_deterministic_without_duplicate_rows(tmp_path: Path) -> None:
    app = _publisher_app()
    http = TestClient(app)
    uploaded = _upload(
        http,
        workbook_bytes(tmp_path / "repeat.xlsx", [_failed_gt_sent()]),
        "repeat.xlsx",
        client_id=CLIENT_ID,
    )
    run_id = uploaded.json()["processing_run"]["processing_run_id"]
    first = http.post(f"/api/v1/processing-runs/{run_id}/qa", headers=AUTH).json()
    listed_once = _findings(http, run_id).json()
    second = http.post(f"/api/v1/processing-runs/{run_id}/qa", headers=AUTH).json()
    listed_twice = _findings(http, run_id).json()
    assert first["qa_verdict"] == second["qa_verdict"] == "warn"
    assert listed_once["summary"] == listed_twice["summary"]
    keys_once = sorted((item["rule_id"], item["entity_key"]) for item in listed_once["items"])
    keys_twice = sorted((item["rule_id"], item["entity_key"]) for item in listed_twice["items"])
    assert keys_once == keys_twice
    assert len(keys_twice) == len(set(keys_twice))


def test_complete_dataset_includes_first_and_last_facts(tmp_path: Path) -> None:
    app = _publisher_app()
    http = TestClient(app)
    rows = [
        _failed_gt_sent(**{"Campaign ID": "camp-first"}),
        source_row(**{"Campaign ID": "camp-mid", "Variation ID": "var-mid"}),
        _failed_gt_sent(**{"Campaign ID": "camp-last"}),
    ]
    uploaded = _upload(
        http,
        workbook_bytes(tmp_path / "span.xlsx", rows),
        "span.xlsx",
        client_id=CLIENT_ID,
    )
    run_id = uploaded.json()["processing_run"]["processing_run_id"]
    listed = http.get(
        f"/api/v1/processing-runs/{run_id}/qa-findings",
        headers=AUTH,
        params={"limit": 200, "offset": 0},
    ).json()
    failed_keys = [
        item["entity_key"]
        for item in listed["items"]
        if item["rule_id"] == "QA-IMPOSSIBLE-FAILED"
    ]
    assert any("camp-first" in key for key in failed_keys)
    assert any("camp-last" in key for key in failed_keys)
    assert listed["summary"]["by_rule"].get("QA-IMPOSSIBLE-FAILED") == 2


def test_cross_client_qa_cannot_leak_facts(tmp_path: Path) -> None:
    ingest = InMemoryIngestStore()
    facts = InMemoryFactStore()
    app = create_app(
        settings=make_settings(dfip_auth_mode="jwt", dfip_auth_secret=JWT_SECRET),
        ingest_store=ingest,
        fact_store=facts,
    )
    http = TestClient(app)
    headers_a = {"Authorization": f"Bearer {_encode_jwt(role='publisher', client_id=CLIENT_ID)}"}
    headers_b = {"Authorization": f"Bearer {_encode_jwt(role='publisher', client_id=CLIENT_B)}"}
    uploaded = _upload(
        http,
        workbook_bytes(tmp_path / "a.xlsx", [_failed_gt_sent()]),
        "a.xlsx",
        headers=headers_a,
    )
    run_id = uploaded.json()["processing_run"]["processing_run_id"]
    own = _findings(http, run_id, headers=headers_a)
    assert own.status_code == 200
    assert own.json()["summary"]["total"] >= 1
    leak = _findings(http, run_id, headers=headers_b)
    assert leak.status_code == 404
    assert "camp-a" not in leak.text
    assert "QA-IMPOSSIBLE-FAILED" not in leak.text


def test_evaluate_and_persist_run_uses_for_run_dataset() -> None:
    ingest, facts = seed_stores()
    qa = InMemoryQaFindingStore()
    run = ingest.processing_runs[RUN_A]
    foreign = FactRecord(
        client_id=CLIENT_B,
        campaign_id="foreign-camp",
        variation_id="var-x",
        variation_id_key="var-x",
        day=date(2025, 8, 1),
        processing_run_id="e0000000-0000-4000-8000-000000000099",
        batch_id="c0000000-0000-4000-8000-000000000099",
        template_status="",
        sent=1,
        failed=9,
        delivered=1,
    )
    facts.facts[foreign.key] = foreign
    evaluate_and_persist_run(
        ingest_store=ingest,
        fact_store=facts,
        qa_store=qa,
        run=run,
        client_id=CLIENT_ID,
    )
    rows = qa.list_for_run(RUN_A)
    assert all(item["processing_run_id"] == RUN_A for item in rows)
    assert all("foreign-camp" not in item["entity_key"] for item in rows)
    assert ingest.processing_runs[RUN_A].qa_verdict is not None
