"""P8 UAT: client published slice vs admin working set."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from dfip_api.app import create_app
from dfip_api.publication_store import InMemoryPublicationStore
from dfip_api.schemas import MAX_PAGE_LIMIT
from dfip_core.transform.fact import FactRecord
from dfip_web.api_client import DfipApiClient
from dfip_web.client_workbook import M_PATH, XLSX_PATH, mashup_text
from fastapi.testclient import TestClient

from test_p4_workbook_reconciliation import VINTAGES, find_workbook, reconcile_workbook
from test_p5_api import (
    AUTH,
    CLIENT_ID,
    DEV_TOKEN,
    JWT_SECRET,
    RUN_A,
    RUN_C,
    _encode_jwt,
    make_settings,
    seed_stores,
)
from test_p7_publication import (
    FORBIDDEN_TEMPLATE_TOKENS,
    PUBLISHED_FACTS_PATH,
    WORKING_SET_FACTS_PATH,
    _omits_working_set_facts_url,
    _publish,
    assert_no_fake_mashup_parts,
    publisher_app,
)

ROOT = Path(__file__).resolve().parents[1]
WEB_STATIC = ROOT / "apps" / "web" / "static"
CLIENT_B = "a0000000-0000-4000-8000-000000000002"


def _error(response) -> dict:
    payload = response.json()
    assert "error" in payload
    return payload["error"]


def test_client_facts_route_uses_published_endpoint_not_working_set() -> None:
    app_js = (WEB_STATIC / "js" / "app.js").read_text(encoding="utf-8")
    views = (WEB_STATIC / "js" / "views.js").read_text(encoding="utf-8")
    client_js = (WEB_STATIC / "js" / "api-client.js").read_text(encoding="utf-8")

    client_list = app_js[
        app_js.index('if (name === "client-facts")') : app_js.index(
            'if (name === "client-fact-detail")'
        )
    ]
    assert "listPublishedFacts" in client_list
    assert "listFacts" not in client_list

    client_detail = app_js[
        app_js.index('if (name === "client-fact-detail")') : app_js.index("return notFoundView();")
    ]
    assert "loadPublishedFact" in client_detail
    assert "loadFact(" not in client_detail

    admin_list = app_js[
        app_js.index('if (name === "admin-facts")') : app_js.index(
            'if (name === "admin-fact-detail")'
        )
    ]
    assert "listFacts" in admin_list
    assert "listPublishedFacts" not in admin_list

    assert "listPublishedFacts" in client_js
    assert "/publications/current/facts" in client_js
    assert "No published facts." in views
    assert "GET /api/v1/publications/current/facts" in views


def test_empty_current_publication_client_facts_are_empty_not_working_set() -> None:
    app, _ingest, _facts, _store = publisher_app()
    client = TestClient(app)
    working = client.get("/api/v1/facts", headers=AUTH).json()
    assert working["pagination"]["total"] == 3
    published = client.get(
        "/api/v1/publications/current/facts",
        headers=AUTH,
        params={"client_id": CLIENT_ID},
    ).json()
    assert published["items"] == []
    assert published["pagination"]["total"] == 0
    current = client.get(
        "/api/v1/publications/current",
        headers=AUTH,
        params={"client_id": CLIENT_ID},
    ).json()
    assert current == {"publication": None, "current": None}


def test_admin_working_set_and_client_published_slice_diverge() -> None:
    app, _ingest, facts, _store = publisher_app()
    extra = FactRecord(
        client_id=CLIENT_ID,
        campaign_id="camp-unpublished",
        variation_id="var-z",
        variation_id_key="var-z",
        day=date(2025, 8, 4),
        processing_run_id=RUN_C,
        total_cost=None,
        template_status="",
    )
    facts.facts[extra.key] = extra
    client = TestClient(app)
    working_before = client.get("/api/v1/facts", headers=AUTH).json()
    assert working_before["pagination"]["total"] == 4
    created = _publish(client)
    assert created.status_code == 201
    published = client.get(
        "/api/v1/publications/current/facts",
        headers=AUTH,
        params={"client_id": CLIENT_ID},
    ).json()
    working_after = client.get("/api/v1/facts", headers=AUTH).json()
    assert working_after["pagination"]["total"] == 4
    assert published["pagination"]["total"] == 3
    assert {item["campaign_id"] for item in published["items"]} == {
        "camp-1",
        "camp-2",
        "camp-3",
    }
    assert all(item["processing_run_id"] == RUN_A for item in published["items"])
    camp1 = next(item for item in published["items"] if item["campaign_id"] == "camp-1")
    assert camp1["total_cost"] == "2.50"
    assert camp1["filter_logic_1"] is None
    assert camp1["template_status"] == ""


def test_republish_updates_client_published_slice() -> None:
    app, _ingest, facts, store = publisher_app()
    extra = FactRecord(
        client_id=CLIENT_ID,
        campaign_id="camp-run-c",
        variation_id="var-c",
        variation_id_key="var-c",
        day=date(2025, 8, 4),
        processing_run_id=RUN_C,
        template_status="",
    )
    facts.facts[extra.key] = extra
    ingest = app.state.ingest_store
    ingest.processing_runs[RUN_C].status = "succeeded"
    ingest.processing_runs[RUN_C].qa_verdict = "pass"
    client = TestClient(app)
    first = _publish(client, notes="first")
    second = _publish(client, processing_run_id=RUN_C, notes="second")
    assert first.status_code == 201
    assert second.status_code == 201
    first_id = first.json()["publication"]["publication_id"]
    second_id = second.json()["publication"]["publication_id"]
    assert first_id != second_id
    current = client.get(
        "/api/v1/publications/current",
        headers=AUTH,
        params={"client_id": CLIENT_ID},
    ).json()
    assert current["publication"]["publication_id"] == second_id
    published = client.get(
        "/api/v1/publications/current/facts",
        headers=AUTH,
        params={"client_id": CLIENT_ID},
    ).json()
    assert published["pagination"]["total"] == 1
    assert published["items"][0]["campaign_id"] == "camp-run-c"
    history = store.list_for_client(CLIENT_ID)
    assert [item.id for item in history] == [first_id, second_id]
    working = client.get("/api/v1/facts", headers=AUTH).json()
    assert working["pagination"]["total"] == 4


def test_period_publication_clips_client_facts() -> None:
    app, *_rest = publisher_app()
    client = TestClient(app)
    _publish(client, period_start="2025-08-01", period_end="2025-08-01")
    published = client.get(
        "/api/v1/publications/current/facts",
        headers=AUTH,
        params={"client_id": CLIENT_ID},
    ).json()
    assert published["pagination"]["total"] == 1
    assert published["items"][0]["campaign_id"] == "camp-1"
    assert published["items"][0]["day"] == "2025-08-01"
    working = client.get("/api/v1/facts", headers=AUTH).json()
    assert working["pagination"]["total"] == 3


def test_published_facts_paginate_without_raising_global_limit() -> None:
    app, _ingest, facts, _store = publisher_app()
    for index in range(201):
        record = FactRecord(
            client_id=CLIENT_ID,
            campaign_id=f"extra-{index:03d}",
            variation_id="var-x",
            variation_id_key="var-x",
            day=date(2025, 9, 1),
            processing_run_id=RUN_A,
        )
        facts.facts[record.key] = record
    client = TestClient(app)
    _publish(client)
    rejected = client.get(
        "/api/v1/publications/current/facts",
        headers=AUTH,
        params={"client_id": CLIENT_ID, "limit": MAX_PAGE_LIMIT + 1},
    )
    assert rejected.status_code == 422
    assert _error(rejected)["code"] in {"INVALID_PAGINATION", "VALIDATION_ERROR"}
    first = client.get(
        "/api/v1/publications/current/facts",
        headers=AUTH,
        params={"client_id": CLIENT_ID, "limit": 200, "offset": 0},
    ).json()
    second = client.get(
        "/api/v1/publications/current/facts",
        headers=AUTH,
        params={"client_id": CLIENT_ID, "limit": 200, "offset": 200},
    ).json()
    assert first["pagination"]["limit"] == 200
    assert first["pagination"]["total"] == 204
    assert len(first["items"]) == 200
    assert len(second["items"]) == 4
    working = client.get(f"/api/v1/facts?limit={MAX_PAGE_LIMIT}", headers=AUTH).json()
    assert working["pagination"]["total"] == 204
    assert MAX_PAGE_LIMIT == 200


def test_jwt_client_cannot_read_another_clients_publication() -> None:
    ingest, facts = seed_stores()
    store = InMemoryPublicationStore()
    publisher = create_app(
        settings=make_settings(dfip_dev_auth_role="publisher"),
        ingest_store=ingest,
        fact_store=facts,
        publication_store=store,
    )
    reader = create_app(
        settings=make_settings(dfip_auth_mode="jwt", dfip_auth_secret=JWT_SECRET),
        ingest_store=ingest,
        fact_store=facts,
        publication_store=store,
    )
    created = _publish(TestClient(publisher))
    assert created.status_code == 201
    token = _encode_jwt(role="client", client_id=CLIENT_B)
    headers = {"Authorization": f"Bearer {token}"}
    http = TestClient(reader)
    scoped = http.get("/api/v1/publications/current/facts", headers=headers)
    assert scoped.status_code == 200
    assert scoped.json()["items"] == []
    denied = http.get(
        "/api/v1/publications/current/facts",
        headers=headers,
        params={"client_id": CLIENT_ID},
    )
    assert denied.status_code == 403
    working = http.get("/api/v1/facts", headers=headers)
    assert working.status_code == 403
    assert _error(working)["code"] == "AUTHORIZATION_FAILED"
    assert working.json() == {
        "error": {
            "code": "AUTHORIZATION_FAILED",
            "message": "Not authorized to access this resource.",
        }
    }


def test_get_facts_remains_unfiltered_working_set() -> None:
    app, *_rest = publisher_app()
    client = TestClient(app)
    before = client.get("/api/v1/facts", headers=AUTH).json()
    _publish(client, period_start="2025-08-01", period_end="2025-08-01")
    after = client.get("/api/v1/facts", headers=AUTH).json()
    assert before["pagination"]["total"] == 3
    assert after["pagination"]["total"] == 3
    assert {item["campaign_id"] for item in after["items"]} == {
        "camp-1",
        "camp-2",
        "camp-3",
    }
    unauth = client.get("/api/v1/facts")
    assert unauth.status_code == 401


def test_python_client_and_p7_publication_routes_still_work() -> None:
    app, *_rest = publisher_app()
    with DfipApiClient("http://testserver", token=DEV_TOKEN, http_client=TestClient(app)) as api:
        empty = api.list_published_facts(client_id=CLIENT_ID)
        created = api.create_publication({"client_id": CLIENT_ID, "processing_run_id": RUN_A})
        published = api.list_published_facts(client_id=CLIENT_ID, limit=200)
        working = api.list_facts()
    assert empty["pagination"]["total"] == 0
    assert created["publication"]["processing_run_id"] == RUN_A
    assert published["pagination"]["total"] == 3
    assert working["pagination"]["total"] == 3


def test_excel_and_power_query_have_no_secrets_or_staging() -> None:
    assert M_PATH.is_file()
    mashup = mashup_text()
    disk = M_PATH.read_text(encoding="utf-8")
    assert mashup == disk
    assert "/api/v1/publications/history/facts.csv" in mashup
    assert PUBLISHED_FACTS_PATH not in mashup
    assert _omits_working_set_facts_url(mashup)
    assert WORKING_SET_FACTS_PATH not in mashup.replace(
        "/api/v1/publications/history/facts.csv", ""
    )
    assert "stg_source_row" not in mashup
    raw = XLSX_PATH.read_bytes()
    for token in FORBIDDEN_TEMPLATE_TOKENS:
        assert token not in mashup
        assert token.encode("utf-8") not in raw
    assert_no_fake_mashup_parts(XLSX_PATH)
    assert _omits_working_set_facts_url(raw.decode("latin-1"))


@pytest.mark.parametrize("vintage", VINTAGES, ids=lambda v: v.rate_version)
def test_p8_full_vintage_reconciliation(vintage) -> None:
    """Full available vintage when evidence xlsx exists; skip cleanly otherwise."""
    path = find_workbook(vintage.glob)
    if path is None:
        pytest.skip(
            "P8 full-vintage reconciliation skipped: required evidence workbook unavailable."
        )
    tally = reconcile_workbook(path, vintage, row_limit=-1)
    assert tally.compared > 0, f"no rows in {vintage.day_from}..{vintage.day_to}"
    assert tally.cost_mismatch == [], tally.cost_mismatch[:5]
    assert tally.template_mismatch == [], tally.template_mismatch[:5]
    assert tally.label_mismatch == [], tally.label_mismatch[:5]
    assert tally.month_mismatch == [], tally.month_mismatch[:5]
    assert tally.hhh_mismatch == [], tally.hhh_mismatch[:5]
    assert tally.cost_mismatch == [], tally.cost_mismatch[:5]
    assert tally.template_mismatch == [], tally.template_mismatch[:5]
    assert tally.label_mismatch == [], tally.label_mismatch[:5]
    assert tally.month_mismatch == [], tally.month_mismatch[:5]
    assert tally.hhh_mismatch == [], tally.hhh_mismatch[:5]
