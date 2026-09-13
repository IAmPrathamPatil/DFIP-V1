"""Shared Web Engage workbook builders and HTTP upload wait helpers."""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from test_p3_ingest import _write_workbook
from test_p4_transform import GROUP7_CAMPAIGN

AUG_DAY = datetime(2025, 8, 1)
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
TERMINAL_BATCH = frozenset({"processed", "failed", "cancelled"})
TERMINAL_RUN = frozenset({"succeeded", "failed", "cancelled"})


def source_row(**overrides: Any) -> dict[str, object]:
    row: dict[str, object] = {
        "Day": AUG_DAY,
        "Campaign ID": "camp-a",
        "Variation ID": "var-1",
        "Campaign Name": GROUP7_CAMPAIGN,
        "Channel": "SMS",
        "Sent": 100,
        "Delivered": 100,
        "Unique Impressions": 100,
        "Unique Clicks": 2,
    }
    row.update(overrides)
    return row


def workbook_bytes(path: Path, rows: list[dict[str, object]], **kwargs: Any) -> bytes:
    _write_workbook(path, rows, **kwargs)
    return path.read_bytes()


class CompletedUploadResponse:
    """Stand-in for a TestClient response after background ingest finishes."""

    def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)
        self.content = self.text.encode("utf-8")

    def json(self) -> dict[str, Any]:
        return self._payload


def post_upload(client, content: bytes, filename: str, *, headers=None, **form):
    files = {"file": (filename, content, XLSX_TYPE)}
    data = {
        key: str(value).lower() if isinstance(value, bool) else str(value)
        for key, value in form.items()
    }
    return client.post(
        "/api/v1/uploads",
        headers=headers or {},
        files=files,
        data=data or None,
    )


def post_upload_many(
    client,
    parts: list[tuple[str, bytes]],
    *,
    headers=None,
    **form,
):
    files = [("files", (filename, content, XLSX_TYPE)) for filename, content in parts]
    data = {
        key: str(value).lower() if isinstance(value, bool) else str(value)
        for key, value in form.items()
    }
    return client.post(
        "/api/v1/uploads",
        headers=headers or {},
        files=files,
        data=data or None,
    )


def wait_for_upload(
    client,
    accepted: dict[str, Any],
    *,
    headers=None,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Poll until ingest finishes. Prefer the in-process completed stash."""
    batch = accepted.get("batch") or {}
    batch_id = batch.get("batch_id")
    if not batch_id:
        raise AssertionError("upload acknowledgement is missing batch.batch_id")
    deadline = time.monotonic() + timeout
    auth = headers or {}
    has_stash = _upload_service(client) is not None
    while time.monotonic() < deadline:
        stored = _completed_payload(client, batch_id)
        if stored is not None:
            return stored
        if not has_stash and auth:
            http_body = _http_upload_snapshot(client, batch_id, accepted, headers=auth)
            if http_body is not None:
                return http_body
        time.sleep(0.02)
    raise AssertionError(f"upload batch {batch_id} did not complete within {timeout}s")


def complete_upload_response(client, response, *, headers=None, timeout: float = 60.0):
    """Turn 202 acknowledgements into the completed 201/200 body tests already assert."""
    if response.status_code not in {200, 201, 202}:
        return response
    body = response.json()
    if "items" in body:
        completed = _complete_group_items(
            client, body, headers=headers, timeout=timeout
        )
        status = 200 if completed.get("replayed") else 201
        return CompletedUploadResponse(status, completed)
    if response.status_code == 200:
        return response
    if response.status_code == 201:
        return response
    body = wait_for_upload(client, body, headers=headers, timeout=timeout)
    status = 200 if body.get("replayed") else 201
    return CompletedUploadResponse(status, body)


def upload_workbook(
    client,
    content: bytes,
    filename: str,
    *,
    headers=None,
    wait: bool = True,
    timeout: float = 60.0,
    **form,
):
    response = post_upload(client, content, filename, headers=headers, **form)
    if not wait:
        return response
    return complete_upload_response(client, response, headers=headers, timeout=timeout)


def upload_workbooks(
    client,
    parts: list[tuple[str, bytes]],
    *,
    headers=None,
    wait: bool = True,
    timeout: float = 60.0,
    **form,
):
    response = post_upload_many(client, parts, headers=headers, **form)
    if not wait:
        return response
    return complete_upload_response(client, response, headers=headers, timeout=timeout)


def _complete_group_items(client, body: dict[str, Any], *, headers=None, timeout: float = 60.0):
    items = []
    for item in body.get("items") or []:
        batch = item.get("batch") or {}
        if item.get("replayed") or batch.get("status") in TERMINAL_BATCH:
            items.append(item)
            continue
        items.append(wait_for_upload(client, item, headers=headers, timeout=timeout))
    completed = dict(body)
    completed["items"] = items
    completed["replayed"] = all(item.get("replayed") for item in items)
    completed["file_count"] = len(items)
    completed["staged_row_count"] = sum(
        (item.get("batch") or {}).get("row_count_staged") or 0 for item in items
    )
    completed["fact_count"] = sum(
        (item.get("transform") or {}).get("transformed") or 0 for item in items
    )
    return completed


def _upload_service(client):
    app = getattr(client, "app", None)
    return getattr(getattr(app, "state", None), "upload_service", None)


def _completed_payload(client, batch_id: str) -> dict[str, Any] | None:
    service = _upload_service(client)
    if service is None:
        return None
    stored = service.completed_result(batch_id)
    if stored is None:
        return None
    return stored.model_dump(mode="json")


def _http_upload_snapshot(
    client, batch_id: str, accepted: dict[str, Any], *, headers
) -> dict[str, Any] | None:
    batch_response = client.get(f"/api/v1/batches/{batch_id}", headers=headers)
    if batch_response.status_code != 200:
        return None
    batch = batch_response.json()
    runs_response = client.get(
        "/api/v1/processing-runs",
        headers=headers,
        params={"batch_id": batch_id, "limit": 20, "offset": 0},
    )
    run = None
    if runs_response.status_code == 200:
        items = runs_response.json().get("items") or []
        run = items[-1] if items else None
    run_status = None if run is None else run.get("status")
    batch_status = batch.get("status")
    if run_status in TERMINAL_RUN or batch_status in TERMINAL_BATCH:
        body = dict(accepted)
        body["batch"] = batch
        body["processing_run"] = run
        return body
    return None


def wait_for_reprocess(
    client,
    accepted: dict[str, Any],
    *,
    headers=None,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Poll until a reprocess run reaches succeeded or failed."""
    run = accepted.get("processing_run") or {}
    run_id = run.get("processing_run_id")
    if not run_id:
        raise AssertionError("reprocess acknowledgement is missing processing_run_id")
    deadline = time.monotonic() + timeout
    auth = headers or {}
    service = _upload_service(client)
    while time.monotonic() < deadline:
        if service is not None:
            stored = service.reprocess_result(run_id)
            if stored is not None:
                return stored.model_dump(mode="json")
        if auth:
            response = client.get(f"/api/v1/processing-runs/{run_id}", headers=auth)
            if response.status_code == 200:
                body = response.json()
                if body.get("status") in TERMINAL_RUN:
                    merged = dict(accepted)
                    merged["processing_run"] = body
                    return merged
        time.sleep(0.02)
    raise AssertionError(f"reprocess run {run_id} did not complete within {timeout}s")


def complete_reprocess_response(client, response, *, headers=None, timeout: float = 60.0):
    if response.status_code == 200:
        return response
    if response.status_code != 202:
        return response
    body = wait_for_reprocess(client, response.json(), headers=headers, timeout=timeout)
    return CompletedUploadResponse(200, body)
