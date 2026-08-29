"""Publication client/run integrity. Runs on the V1 in-memory path."""

from __future__ import annotations

from dfip_api.app import create_app
from dfip_api.publication_store import InMemoryPublicationStore
from fastapi.testclient import TestClient

from test_p5_api import AUTH, RUN_A, inspector_settings, seed_stores
from test_p7_publication import CLIENT_B


def test_cannot_publish_another_clients_processing_run() -> None:
    ingest, facts = seed_stores()
    app = create_app(
        settings=inspector_settings(),
        ingest_store=ingest,
        fact_store=facts,
        publication_store=InMemoryPublicationStore(),
    )
    client = TestClient(app)
    response = client.post(
        "/api/v1/publications",
        headers=AUTH,
        json={"client_id": CLIENT_B, "processing_run_id": RUN_A},
    )
    assert response.status_code == 422
    body = response.json()["error"]
    assert body["code"] == "VALIDATION_ERROR"
    assert "does not belong" in body["message"]
