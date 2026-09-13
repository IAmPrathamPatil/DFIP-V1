"""Phase 1 configuration: production requires DATABASE_URL."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from dfip_api.app import create_app
from dfip_api.errors import AuthConfigurationError, PersistenceConfigurationError
from dfip_core.ingest.store import InMemoryIngestStore
from dfip_core.transform.store import InMemoryFactStore
from fastapi.testclient import TestClient

from test_p5_api import JWT_SECRET, make_settings, production_settings


def test_production_requires_database_url() -> None:
    with pytest.raises(PersistenceConfigurationError):
        create_app(
            settings=production_settings(database_url=""),
            ingest_store=InMemoryIngestStore(),
            fact_store=InMemoryFactStore(),
        )


def test_production_still_refuses_dev_token_before_dsn() -> None:
    with pytest.raises(AuthConfigurationError):
        create_app(
            settings=make_settings(
                dfip_env="production",
                dfip_auth_mode="dev_token",
                database_url="",
            ),
            ingest_store=InMemoryIngestStore(),
            fact_store=InMemoryFactStore(),
        )


def test_unreachable_database_is_503_not_a_stack_trace() -> None:
    app = create_app(
        settings=make_settings(
            dfip_auth_mode="jwt",
            dfip_auth_secret=JWT_SECRET,
            database_url="postgresql://postgres@127.0.0.1:1/dfip",
            dfip_env="test",
        )
    )
    token = jwt.encode(
        {
            "sub": "anyone",
            "role": "publisher",
            "exp": datetime.now(tz=UTC) + timedelta(minutes=5),
        },
        JWT_SECRET,
        algorithm="HS256",
    )
    response = TestClient(app).get(
        "/api/v1/session",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 503
    body = response.json()["error"]
    assert body["code"] == "PERSISTENCE_UNAVAILABLE"
    assert "traceback" not in response.text.lower()
    assert "password" not in response.text.lower()


def test_health_stays_not_checked_when_dsn_is_configured() -> None:
    app = create_app(
        settings=make_settings(
            dfip_auth_mode="jwt",
            dfip_auth_secret="health-secret-not-for-reuse",
            database_url="postgresql://postgres@127.0.0.1:1/dfip",
            dfip_env="test",
        )
    )
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["database"]["configured"] is True
    assert payload["database"]["status"] == "not_checked"
