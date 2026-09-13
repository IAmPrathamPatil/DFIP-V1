"""P13F company lifecycle: active/inactive, access guard, no HTTP purge.

In-memory tests do not open PostgreSQL. Does not touch hosted/live/August data.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from dfip_api.app import create_app
from dfip_api.company_delete import (
    IN_FLIGHT_MESSAGE,
    MUST_DEACTIVATE,
    PROTECTED_MESSAGE,
)
from dfip_api.lifecycle import INACTIVE_COMPANY_MESSAGE, is_purge_eligible
from dfip_api.local_demo_seed import seed_company2_identities, seed_demo_identities
from dfip_api.publication_store import InMemoryPublicationStore
from dfip_core.ingest.store import ProcessingRunRecord
from dfip_db.local_demo_guard import (
    COMPANY_2_CLIENT_ID,
    DEFAULT_CLIENT_ID,
    DEMO_CLIENT_SUBJECT,
    DEMO_PUBLISHER_2_SUBJECT,
    DEMO_PUBLISHER_SUBJECT,
)
from fastapi.testclient import TestClient

from catalog_support import logic_xlsx
from postgres_support import postgres_only, requires_postgres
from test_company_registry import (
    CAMP_1,
    CLIENT_PASSWORD,
    ITERATIONS,
    PUBLISHER2_PASSWORD,
    PUBLISHER_PASSWORD,
    WEB_STATIC,
    _bearer,
    _identity,
    _publish_synthetic,
    _select,
    _token,
)
from test_p5_api import JWT_SECRET, make_settings
from test_p9_authz import _error


def _app(**overrides):
    return create_app(
        settings=make_settings(
            dfip_auth_mode="jwt",
            dfip_auth_secret=JWT_SECRET,
            dfip_password_pbkdf2_iterations=ITERATIONS,
            dfip_env="test",
            **overrides,
        ),
        identity_store=_identity(),
        publication_store=InMemoryPublicationStore(),
    )


def _inactive(response) -> None:
    assert response.status_code == 403, response.text
    body = _error(response)
    assert body["code"] == "AUTHORIZATION_FAILED"
    assert body["message"] == INACTIVE_COMPANY_MESSAGE


def test_settings_purge_policy_defaults() -> None:
    settings = make_settings()
    assert settings.dfip_company_purge_min_age_days is None
    assert settings.dfip_backup_keep_count == 0
    assert settings.dfip_backup_keep_days == 0
    assert settings.dfip_temp_upload_max_age_hours == 24


def test_active_company_operates_then_deactivate_blocks_live_ops(tmp_path: Path) -> None:
    http = TestClient(_app())
    unbound = _token(http, DEMO_PUBLISHER_SUBJECT, PUBLISHER_PASSWORD)
    company1 = _bearer(_select(http, unbound, DEFAULT_CLIENT_ID).json()["access_token"])
    listed = http.get("/api/v1/clients", headers=company1)
    assert listed.status_code == 200
    row = next(item for item in listed.json()["items"] if item["client_id"] == DEFAULT_CLIENT_ID)
    assert row["lifecycle_status"] == "active"
    assert row["deactivated_at"] is None
    assert row["purge_eligible_after"] is None
    assert row["code"] == "default"

    published = _publish_synthetic(
        http, tmp_path, publisher=company1, client_id=DEFAULT_CLIENT_ID, campaign_id=CAMP_1
    )
    publication_id = published["publication"]["publication_id"]
    store = http.app.state.identity_store
    before_client = store.get_by_subject(DEMO_CLIENT_SUBJECT).token_version
    before_publisher = store.get_by_subject(DEMO_PUBLISHER_SUBJECT).token_version

    deactivated = http.post(f"/api/v1/clients/{DEFAULT_CLIENT_ID}/deactivate", headers=company1)
    assert deactivated.status_code == 200, deactivated.text
    body = deactivated.json()
    assert body["lifecycle_status"] == "inactive"
    assert body["deactivated_at"]
    assert body["purge_eligible_after"] is None
    assert body["client_id"] == DEFAULT_CLIENT_ID
    assert body["code"] == "default"
    hidden = http.get("/api/v1/clients", headers=company1)
    assert hidden.status_code == 200
    assert DEFAULT_CLIENT_ID not in {item["client_id"] for item in hidden.json()["items"]}
    listed_inactive = http.get(
        "/api/v1/clients", headers=company1, params={"include_inactive": True}
    )
    assert listed_inactive.status_code == 200
    inactive_row = next(
        item
        for item in listed_inactive.json()["items"]
        if item["client_id"] == DEFAULT_CLIENT_ID
    )
    assert inactive_row["lifecycle_status"] == "inactive"
    record = http.app.state.client_directory.get(DEFAULT_CLIENT_ID)
    assert record.lifecycle_status == "inactive"
    assert not is_purge_eligible(record)
    assert store.get_by_subject(DEMO_CLIENT_SUBJECT).token_version == before_client
    assert store.get_by_subject(DEMO_PUBLISHER_SUBJECT).token_version == before_publisher

    current = http.get("/api/v1/publications/current", headers=company1)
    assert current.status_code == 200
    assert current.json()["current"]["publication_id"] == publication_id
    historical = http.get(f"/api/v1/publications/{publication_id}/facts", headers=company1)
    assert historical.status_code == 200
    historical_xlsx = http.get(
        f"/api/v1/publications/{publication_id}/client-report.xlsx", headers=company1
    )
    assert historical_xlsx.status_code == 200

    _inactive(http.get("/api/v1/publications/current/facts", headers=company1))
    _inactive(http.get("/api/v1/publications/current/client-report.xlsx", headers=company1))
    _inactive(
        http.get("/api/v1/publications/current/refreshable-client-report.xlsx", headers=company1)
    )
    _inactive(
        http.post(
            "/api/v1/uploads",
            headers=company1,
            files={
                "file": (
                    "blocked.xlsx",
                    b"not-a-real-xlsx",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )
    )
    run_id = published["publication"]["processing_run_id"]
    _inactive(http.post(f"/api/v1/processing-runs/{run_id}/qa", headers=company1))
    _inactive(
        http.post(
            "/api/v1/publications",
            headers=company1,
            json={"client_id": DEFAULT_CLIENT_ID, "processing_run_id": run_id},
        )
    )
    catalog = http.post(
        "/api/v1/catalogs/logic",
        headers=company1,
        files={
            "file": (
                "logic.xlsx",
                logic_xlsx(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    _inactive(catalog)
    session = http.get("/api/v1/session", headers=company1)
    assert session.status_code == 200
    memberships = {item["client_id"] for item in session.json()["clients"]}
    assert DEFAULT_CLIENT_ID in memberships
    assert COMPANY_2_CLIENT_ID in memberships

    client_login = http.post(
        "/api/v1/auth/login",
        json={"username": DEMO_CLIENT_SUBJECT, "password": CLIENT_PASSWORD},
    )
    _inactive(client_login)
    select = _select(http, unbound, DEFAULT_CLIENT_ID)
    _inactive(select)
    refresh = http.post("/api/v1/auth/refresh", headers=company1)
    _inactive(refresh)

    health = http.get("/health")
    assert health.status_code == 200
    ready = http.get("/api/v1/ops/ready", headers=company1)
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"

    other = _bearer(_token(http, DEMO_PUBLISHER_2_SUBJECT, PUBLISHER2_PASSWORD))
    other_list = http.get("/api/v1/clients", headers=other)
    assert other_list.status_code == 200
    other_ids = {item["client_id"] for item in other_list.json()["items"]}
    assert COMPANY_2_CLIENT_ID in other_ids
    assert all(item["lifecycle_status"] == "active" for item in other_list.json()["items"])


def test_reactivate_restores_operations(tmp_path: Path) -> None:
    http = TestClient(_app())
    unbound = _token(http, DEMO_PUBLISHER_SUBJECT, PUBLISHER_PASSWORD)
    company1 = _bearer(_select(http, unbound, DEFAULT_CLIENT_ID).json()["access_token"])
    http.post(f"/api/v1/clients/{DEFAULT_CLIENT_ID}/deactivate", headers=company1)
    restored = http.post(f"/api/v1/clients/{DEFAULT_CLIENT_ID}/reactivate", headers=company1)
    assert restored.status_code == 200
    assert restored.json()["lifecycle_status"] == "active"
    assert restored.json()["deactivated_at"] is None
    assert restored.json()["purge_eligible_after"] is None
    published = _publish_synthetic(
        http, tmp_path, publisher=company1, client_id=DEFAULT_CLIENT_ID, campaign_id="camp-re"
    )
    assert published["publication"]["client_id"] == DEFAULT_CLIENT_ID
    client = _bearer(_token(http, DEMO_CLIENT_SUBJECT, CLIENT_PASSWORD))
    facts = http.get("/api/v1/publications/current/facts", headers=client)
    assert facts.status_code == 200


def test_in_flight_processing_is_not_cancelled(tmp_path: Path) -> None:
    http = TestClient(_app())
    unbound = _token(http, DEMO_PUBLISHER_SUBJECT, PUBLISHER_PASSWORD)
    company1 = _bearer(_select(http, unbound, DEFAULT_CLIENT_ID).json()["access_token"])
    published = _publish_synthetic(
        http, tmp_path, publisher=company1, client_id=DEFAULT_CLIENT_ID, campaign_id="camp-run"
    )
    run_id = published["publication"]["processing_run_id"]
    ingest = http.app.state.ingest_store
    original = ingest.processing_runs[run_id]
    ingest.processing_runs[run_id] = replace(original, status="running", finished_at=None)
    http.post(f"/api/v1/clients/{DEFAULT_CLIENT_ID}/deactivate", headers=company1)
    assert ingest.processing_runs[run_id].status == "running"
    still = http.get(f"/api/v1/processing-runs/{run_id}", headers=company1)
    assert still.status_code == 200
    assert still.json()["status"] == "running"


def test_publisher_login_without_select_still_works() -> None:
    http = TestClient(_app())
    unbound = _token(http, DEMO_PUBLISHER_SUBJECT, PUBLISHER_PASSWORD)
    company1 = _bearer(_select(http, unbound, DEFAULT_CLIENT_ID).json()["access_token"])
    http.post(f"/api/v1/clients/{DEFAULT_CLIENT_ID}/deactivate", headers=company1)
    again = http.post(
        "/api/v1/auth/login",
        json={"username": DEMO_PUBLISHER_SUBJECT, "password": PUBLISHER_PASSWORD},
    )
    assert again.status_code == 200, again.text
    assert again.json()["session"]["client_id"] is None
    explicit = http.post(
        "/api/v1/auth/login",
        json={
            "username": DEMO_PUBLISHER_SUBJECT,
            "password": PUBLISHER_PASSWORD,
            "client_id": DEFAULT_CLIENT_ID,
        },
    )
    _inactive(explicit)


def test_process_retry_blocked_after_deactivate(tmp_path: Path) -> None:
    http = TestClient(_app())
    unbound = _token(http, DEMO_PUBLISHER_SUBJECT, PUBLISHER_PASSWORD)
    company1 = _bearer(_select(http, unbound, DEFAULT_CLIENT_ID).json()["access_token"])
    published = _publish_synthetic(
        http, tmp_path, publisher=company1, client_id=DEFAULT_CLIENT_ID, campaign_id="camp-retry"
    )
    run = http.get(
        f"/api/v1/processing-runs/{published['publication']['processing_run_id']}",
        headers=company1,
    ).json()
    batch_id = run["batch_id"]
    http.post(f"/api/v1/clients/{DEFAULT_CLIENT_ID}/deactivate", headers=company1)
    _inactive(http.post(f"/api/v1/batches/{batch_id}/process", headers=company1))


def test_protected_company_cannot_be_deleted() -> None:
    http = TestClient(_app())
    unbound = _token(http, DEMO_PUBLISHER_SUBJECT, PUBLISHER_PASSWORD)
    company1 = _bearer(_select(http, unbound, DEFAULT_CLIENT_ID).json()["access_token"])
    active = http.delete(f"/api/v1/clients/{DEFAULT_CLIENT_ID}", headers=company1)
    assert active.status_code == 409, active.text
    assert _error(active)["message"] == MUST_DEACTIVATE
    http.post(f"/api/v1/clients/{DEFAULT_CLIENT_ID}/deactivate", headers=company1)
    deleted = http.delete(f"/api/v1/clients/{DEFAULT_CLIENT_ID}", headers=company1)
    assert deleted.status_code == 403, deleted.text
    assert _error(deleted)["message"] == PROTECTED_MESSAGE
    purged = http.post(f"/api/v1/clients/{DEFAULT_CLIENT_ID}/purge", headers=company1)
    assert purged.status_code in {404, 405}


def test_spa_exposes_deactivate_then_delete_company() -> None:
    views = (WEB_STATIC / "js" / "views.js").read_text(encoding="utf-8")
    app_js = (WEB_STATIC / "js" / "app.js").read_text(encoding="utf-8")
    client_js = (WEB_STATIC / "js" / "api-client.js").read_text(encoding="utf-8")
    roles = (WEB_STATIC / "js" / "roles.js").read_text(encoding="utf-8")
    assert "data-company-deactivate" in views
    assert "data-company-reactivate" in views
    assert "data-company-delete" in views
    assert "This permanently deletes" in app_js
    assert "deleteClient" in client_js
    assert "include_inactive" in client_js
    assert "runCompanyDelete" in app_js
    assert "deactivateClient" in client_js
    assert "reactivateClient" in client_js
    assert "runCompanyLifecycle" in app_js
    assert "operationalClients" in roles
    assert "allowsInactiveCompanyRoute" in roles
    assert "isInactiveCompanyError" in app_js
    assert "This company is inactive." in app_js
    assert "confirm-purge" not in views


def test_deactivate_unauthorized_company_is_forbidden() -> None:
    http = TestClient(_app())
    other = _bearer(_token(http, DEMO_PUBLISHER_2_SUBJECT, PUBLISHER2_PASSWORD))
    response = http.post(f"/api/v1/clients/{DEFAULT_CLIENT_ID}/deactivate", headers=other)
    assert response.status_code == 403
    assert _error(response)["message"] != INACTIVE_COMPANY_MESSAGE
    listed = http.get("/api/v1/clients", headers=other).json()
    assert DEFAULT_CLIENT_ID not in {item["client_id"] for item in listed["items"]}


@requires_postgres
@postgres_only
def test_postgres_lifecycle_defaults_active(pg_conn, postgres_url: str) -> None:
    seed_demo_identities(
        pg_conn,
        publisher_password=PUBLISHER_PASSWORD,
        client_password=CLIENT_PASSWORD,
        iterations=ITERATIONS,
    )
    seed_company2_identities(
        pg_conn,
        publisher_password=PUBLISHER2_PASSWORD,
        client_password="local-client2-pass",
        iterations=ITERATIONS,
    )
    pg_conn.commit()
    rows = pg_conn.execute(
        "SELECT lifecycle_status FROM client WHERE id IN (%s, %s)",
        (DEFAULT_CLIENT_ID, COMPANY_2_CLIENT_ID),
    ).fetchall()
    assert {row["lifecycle_status"] for row in rows} == {"active"}
    app = create_app(
        settings=make_settings(
            dfip_auth_mode="jwt",
            dfip_auth_secret=JWT_SECRET,
            database_url=postgres_url,
            dfip_password_pbkdf2_iterations=ITERATIONS,
            dfip_env="test",
            dfip_company_purge_min_age_days=0,
        )
    )
    with TestClient(app) as http:
        unbound = _token(http, DEMO_PUBLISHER_SUBJECT, PUBLISHER_PASSWORD)
        company1 = _bearer(_select(http, unbound, DEFAULT_CLIENT_ID).json()["access_token"])
        deactivated = http.post(f"/api/v1/clients/{DEFAULT_CLIENT_ID}/deactivate", headers=company1)
        assert deactivated.status_code == 200
        assert deactivated.json()["purge_eligible_after"]
        persisted = pg_conn.execute(
            "SELECT lifecycle_status FROM client WHERE id = %s",
            (DEFAULT_CLIENT_ID,),
        ).fetchone()
        assert persisted["lifecycle_status"] == "inactive"
        pointer = pg_conn.execute(
            "SELECT COUNT(*) AS n FROM publication_current WHERE client_id = %s",
            (DEFAULT_CLIENT_ID,),
        ).fetchone()
        assert int(pointer["n"]) >= 0
        memberships = pg_conn.execute(
            "SELECT COUNT(*) AS n FROM client_membership WHERE client_id = %s",
            (DEFAULT_CLIENT_ID,),
        ).fetchone()
        assert int(memberships["n"]) >= 1
        http.post(f"/api/v1/clients/{DEFAULT_CLIENT_ID}/reactivate", headers=company1)
        again = pg_conn.execute(
            "SELECT lifecycle_status, deactivated_at, purge_eligible_after "
            "FROM client WHERE id = %s",
            (DEFAULT_CLIENT_ID,),
        ).fetchone()
        assert again["lifecycle_status"] == "active"
        assert again["deactivated_at"] is None
        assert again["purge_eligible_after"] is None


def test_delete_company_after_deactivate_is_permanent(tmp_path: Path) -> None:
    http = TestClient(_app())
    unbound = _token(http, DEMO_PUBLISHER_SUBJECT, PUBLISHER_PASSWORD)
    company1 = _bearer(_select(http, unbound, DEFAULT_CLIENT_ID).json()["access_token"])
    kept = _publish_synthetic(
        http, tmp_path, publisher=company1, client_id=DEFAULT_CLIENT_ID, campaign_id="camp-keep"
    )
    created = http.post("/api/v1/clients", headers=company1, json={"name": "DELETE TEST COMPANY"})
    assert created.status_code == 201, created.text
    target = created.json()["client_id"]
    assert target != DEFAULT_CLIENT_ID
    scoped = _bearer(_select(http, unbound, target).json()["access_token"])
    listed = http.get("/api/v1/clients", headers=scoped)
    assert target in {item["client_id"] for item in listed.json()["items"]}
    _publish_synthetic(
        http, tmp_path, publisher=scoped, client_id=target, campaign_id="camp-delete-me"
    )
    facts = http.get("/api/v1/publications/current/facts", headers=scoped)
    assert facts.status_code == 200
    assert facts.json()["pagination"]["total"] >= 1

    deactivated = http.post(f"/api/v1/clients/{target}/deactivate", headers=scoped)
    assert deactivated.status_code == 200
    hidden = http.get("/api/v1/clients", headers=scoped)
    assert target not in {item["client_id"] for item in hidden.json()["items"]}
    shown = http.get("/api/v1/clients", headers=scoped, params={"include_inactive": True})
    assert target in {item["client_id"] for item in shown.json()["items"]}
    _inactive(http.get("/api/v1/publications/current/facts", headers=scoped))

    restored = http.post(f"/api/v1/clients/{target}/reactivate", headers=scoped)
    assert restored.status_code == 200
    assert target in {
        item["client_id"] for item in http.get("/api/v1/clients", headers=scoped).json()["items"]
    }
    assert http.get("/api/v1/publications/current/facts", headers=scoped).status_code == 200

    http.post(f"/api/v1/clients/{target}/deactivate", headers=scoped)
    ingest = http.app.state.ingest_store
    fake_id = str(uuid4())
    ingest.processing_runs[fake_id] = ProcessingRunRecord(
        id=fake_id,
        batch_id=str(uuid4()),
        campaign_label_version_id=None,
        template_label_version_id=None,
        rate_card_version_id=None,
        label_group_version_id=None,
        engine_version=None,
        started_at=datetime.now(tz=UTC),
        finished_at=None,
        status="running",
        qa_verdict=None,
        client_id=target,
    )
    blocked = http.delete(f"/api/v1/clients/{target}", headers=scoped)
    assert blocked.status_code == 409, blocked.text
    assert _error(blocked)["message"] == IN_FLIGHT_MESSAGE
    del ingest.processing_runs[fake_id]

    deleted = http.delete(f"/api/v1/clients/{target}", headers=scoped)
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["deleted"] is True
    assert deleted.json()["already_absent"] is False
    again = http.delete(f"/api/v1/clients/{target}", headers=company1)
    assert again.status_code == 200
    assert again.json()["already_absent"] is True

    assert http.app.state.client_directory.get(target) is None
    leftover_facts = [
        fact
        for fact in http.app.state.fact_store.list_current()
        if fact.client_id == target
    ]
    assert leftover_facts == []
    assert target not in {
        item["client_id"]
        for item in http.get("/api/v1/clients", headers=company1, params={"include_inactive": True}).json()[
            "items"
        ]
    }
    kept_headers = _bearer(_select(http, unbound, DEFAULT_CLIENT_ID).json()["access_token"])
    kept_facts = http.get("/api/v1/publications/current/facts", headers=kept_headers)
    assert kept_facts.status_code == 200
    assert kept_facts.json()["pagination"]["total"] >= 1
    assert kept["publication"]["client_id"] == DEFAULT_CLIENT_ID
    gone = http.get("/api/v1/publications/current/facts", headers=scoped)
    assert gone.status_code in {401, 403, 404}


def test_cors_preflight_allows_delete_company_without_changing_get_post() -> None:
    http = TestClient(_app())
    origin = "http://127.0.0.1:3000"
    deleted = http.options(
        f"/api/v1/clients/{DEFAULT_CLIENT_ID}",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "DELETE",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert deleted.status_code == 200, deleted.text
    methods = deleted.headers.get("access-control-allow-methods", "").upper()
    assert "DELETE" in methods
    assert "GET" in methods
    assert "POST" in methods
    assert deleted.headers.get("access-control-allow-origin") == origin
    posted = http.options(
        f"/api/v1/clients/{DEFAULT_CLIENT_ID}/deactivate",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert posted.status_code == 200, posted.text
    assert "POST" in posted.headers.get("access-control-allow-methods", "").upper()
    listed = http.options(
        "/api/v1/clients",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert listed.status_code == 200, listed.text
    assert "GET" in listed.headers.get("access-control-allow-methods", "").upper()
