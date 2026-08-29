"""HTTP client for the P5 working-set API and P7 publication routes.

Used by tests and as the contract twin of ``apps/web/static/js/api-client.js``.
The browser never imports this module.
"""

from __future__ import annotations

import time
from datetime import date
from typing import Any

import httpx

NETWORK_FAILURE = "NETWORK_FAILURE"


class ApiClientError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


def _strip_slash(value: str) -> str:
    return value.rstrip("/")


def _query(params: dict[str, Any] | None) -> dict[str, str]:
    encoded: dict[str, str] = {}
    if not params:
        return encoded
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, date):
            encoded[key] = value.isoformat()
        else:
            encoded[key] = str(value)
    return encoded


class DfipApiClient:
    """Typed GET/POST client. Preserves JSON null vs empty string."""

    def __init__(
        self,
        base_url: str,
        *,
        prefix: str = "/api/v1",
        token: str | None = None,
        transport: httpx.BaseTransport | None = None,
        http_client: httpx.Client | None = None,
        timeout: float = 10.0,
    ) -> None:
        self.base_url = _strip_slash(base_url)
        self.prefix = "/" + _strip_slash(prefix).lstrip("/")
        self.token = token
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(
            base_url=self.base_url,
            transport=transport,
            timeout=timeout,
            follow_redirects=False,
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> DfipApiClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def list_fact_history(self, **params: Any) -> dict[str, Any]:
        return self._request("GET", f"{self.prefix}/facts/history", params=params)

    def create_publication(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", f"{self.prefix}/publications", json_body=payload)

    def list_publications(self, **params: Any) -> dict[str, Any]:
        return self._request("GET", f"{self.prefix}/publications", params=params)

    def get_current_publication(self, **params: Any) -> dict[str, Any]:
        return self._request("GET", f"{self.prefix}/publications/current", params=params)

    def list_published_facts(self, **params: Any) -> dict[str, Any]:
        return self._request("GET", f"{self.prefix}/publications/current/facts", params=params)

    def list_publication_facts(self, publication_id: str, **params: Any) -> dict[str, Any]:
        return self._request(
            "GET", f"{self.prefix}/publications/{publication_id}/facts", params=params
        )

    def upload_workbook(
        self,
        content: bytes,
        filename: str,
        *,
        client_id: str | None = None,
        force: bool = False,
        wait: bool = True,
        timeout: float = 120.0,
        poll_interval: float = 0.05,
        extra_files: list[tuple[str, bytes]] | None = None,
    ) -> dict[str, Any]:
        data: dict[str, str] = {}
        if client_id is not None:
            data["client_id"] = client_id
        if force:
            data["force"] = "true"
        headers: dict[str, str] = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        xlsx_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        if extra_files:
            files: Any = [("files", (filename, content, xlsx_type))]
            files.extend(("files", (name, payload, xlsx_type)) for name, payload in extra_files)
        else:
            files = {
                "file": (
                    filename,
                    content,
                    xlsx_type,
                )
            }
        try:
            response = self._client.post(
                f"{self.prefix}/uploads",
                headers=headers,
                files=files,
                data=data or None,
            )
        except httpx.RequestError as exc:
            raise ApiClientError(0, NETWORK_FAILURE, "Network failure contacting the API.") from exc
        payload = _parse_response(response)
        if wait and response.status_code == 202:
            if payload.get("items"):
                return self.wait_for_upload_group(
                    payload, timeout=timeout, poll_interval=poll_interval
                )
            return self.wait_for_upload(payload, timeout=timeout, poll_interval=poll_interval)
        return payload

    def wait_for_upload(
        self,
        accepted: dict[str, Any],
        *,
        timeout: float = 120.0,
        poll_interval: float = 0.05,
    ) -> dict[str, Any]:
        """Poll GET /batches/{id} until ingest/transform reaches a terminal status."""
        batch = accepted.get("batch") or {}
        batch_id = batch.get("batch_id")
        if not batch_id:
            raise ApiClientError(
                422, "VALIDATION_ERROR", "Upload acknowledgement is missing batch_id."
            )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            app = getattr(self._client, "app", None)
            service = getattr(getattr(app, "state", None), "upload_service", None)
            if service is not None:
                stored = service.completed_result(batch_id)
                if stored is not None:
                    return stored.model_dump(mode="json")
                time.sleep(poll_interval)
                continue
            batch_body = self.get_batch(batch_id)
            runs = self.list_processing_runs(batch_id=batch_id, limit=20, offset=0)
            items = runs.get("items") or []
            run = items[-1] if items else None
            run_status = None if run is None else run.get("status")
            if run_status in {"succeeded", "failed"} or batch_body.get("status") in {
                "processed",
                "failed",
            }:
                body = dict(accepted)
                body["batch"] = batch_body
                body["processing_run"] = run
                return body
            time.sleep(poll_interval)
        raise ApiClientError(504, "INTERNAL_ERROR", "Upload processing did not complete in time.")

    def wait_for_upload_group(
        self,
        accepted: dict[str, Any],
        *,
        timeout: float = 120.0,
        poll_interval: float = 0.05,
    ) -> dict[str, Any]:
        items = []
        for item in accepted.get("items") or []:
            batch = item.get("batch") or {}
            if item.get("replayed") or batch.get("status") in {"processed", "failed"}:
                items.append(item)
                continue
            items.append(self.wait_for_upload(item, timeout=timeout, poll_interval=poll_interval))
        completed = dict(accepted)
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

    def list_catalogs(self, kind: str, **params: Any) -> dict[str, Any]:
        return self._request("GET", f"{self.prefix}/catalogs/{kind}", params=params)

    def get_catalog_version(self, kind: str, version_id: str, **params: Any) -> dict[str, Any]:
        return self._request("GET", f"{self.prefix}/catalogs/{kind}/{version_id}", params=params)

    def upload_catalog(
        self,
        kind: str,
        content: bytes,
        filename: str,
        *,
        client_id: str | None = None,
    ) -> dict[str, Any]:
        data: dict[str, str] = {}
        if client_id is not None:
            data["client_id"] = client_id
        headers: dict[str, str] = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            response = self._client.post(
                f"{self.prefix}/catalogs/{kind}",
                headers=headers,
                files={
                    "file": (
                        filename,
                        content,
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )
                },
                data=data or None,
            )
        except httpx.RequestError as exc:
            raise ApiClientError(0, NETWORK_FAILURE, "Network failure contacting the API.") from exc
        return _parse_response(response)

    def activate_catalog(self, kind: str, version_id: str, **params: Any) -> dict[str, Any]:
        return self._request(
            "POST", f"{self.prefix}/catalogs/{kind}/{version_id}/activate", params=params
        )

    def deactivate_catalog(self, kind: str, version_id: str, **params: Any) -> dict[str, Any]:
        return self._request(
            "POST", f"{self.prefix}/catalogs/{kind}/{version_id}/deactivate", params=params
        )

    def download_catalog(
        self,
        kind: str,
        version_id: str,
        *,
        client_id: str | None = None,
    ) -> bytes:
        headers: dict[str, str] = {"Accept": "*/*"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        params = _query({"client_id": client_id} if client_id else {})
        try:
            response = self._client.get(
                f"{self.prefix}/catalogs/{kind}/{version_id}/download",
                params=params,
                headers=headers,
            )
        except httpx.RequestError as exc:
            raise ApiClientError(0, NETWORK_FAILURE, "Network failure contacting the API.") from exc
        if response.status_code >= 400:
            _parse_response(response)
        return response.content

    def download_published_facts(
        self,
        *,
        kind: str = "csv",
        client_id: str | None = None,
    ) -> bytes:
        if kind not in {"csv", "xlsx"}:
            raise ApiClientError(422, "VALIDATION_ERROR", "Download format must be csv or xlsx.")
        headers: dict[str, str] = {"Accept": "*/*"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        params = _query({"client_id": client_id} if client_id else {})
        try:
            response = self._client.get(
                f"{self.prefix}/publications/current/facts.{kind}",
                params=params,
                headers=headers,
            )
        except httpx.RequestError as exc:
            raise ApiClientError(0, NETWORK_FAILURE, "Network failure contacting the API.") from exc
        if response.status_code >= 400:
            _parse_response(response)
        return response.content

    def download_client_report(
        self,
        *,
        publication_id: str | None = None,
        client_id: str | None = None,
    ) -> bytes:
        headers: dict[str, str] = {"Accept": "*/*"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        params = _query({"client_id": client_id} if client_id else {})
        path = (
            f"{self.prefix}/publications/{publication_id}/client-report.xlsx"
            if publication_id
            else f"{self.prefix}/publications/current/client-report.xlsx"
        )
        try:
            response = self._client.get(path, params=params, headers=headers)
        except httpx.RequestError as exc:
            raise ApiClientError(0, NETWORK_FAILURE, "Network failure contacting the API.") from exc
        if response.status_code >= 400:
            _parse_response(response)
        return response.content

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health", auth=False)

    def session(self) -> dict[str, Any]:
        return self._request("GET", f"{self.prefix}/session")

    def login(
        self,
        username: str,
        password: str,
        *,
        client_id: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"username": username, "password": password}
        if client_id:
            body["client_id"] = client_id
        payload = self._request("POST", f"{self.prefix}/auth/login", json_body=body, auth=False)
        token = payload.get("access_token") if isinstance(payload, dict) else None
        if isinstance(token, str) and token:
            self.token = token
        return payload

    def logout(self) -> dict[str, Any]:
        return self._request("POST", f"{self.prefix}/auth/logout")

    def refresh_session(self) -> dict[str, Any]:
        payload = self._request("POST", f"{self.prefix}/auth/refresh")
        token = payload.get("access_token") if isinstance(payload, dict) else None
        if isinstance(token, str) and token:
            self.token = token
        return payload

    def list_source_files(self, **params: Any) -> dict[str, Any]:
        return self._request("GET", f"{self.prefix}/source-files", params=params)

    def get_source_file(self, source_file_id: str) -> dict[str, Any]:
        return self._request("GET", f"{self.prefix}/source-files/{source_file_id}")

    def list_batches(self, **params: Any) -> dict[str, Any]:
        return self._request("GET", f"{self.prefix}/batches", params=params)

    def get_batch(self, batch_id: str) -> dict[str, Any]:
        return self._request("GET", f"{self.prefix}/batches/{batch_id}")

    def process_batch(self, batch_id: str, **params: Any) -> dict[str, Any]:
        return self._request("POST", f"{self.prefix}/batches/{batch_id}/process", params=params)

    def list_staged_rows(self, batch_id: str, **params: Any) -> dict[str, Any]:
        return self._request("GET", f"{self.prefix}/batches/{batch_id}/staged-rows", params=params)

    def list_processing_runs(self, **params: Any) -> dict[str, Any]:
        return self._request("GET", f"{self.prefix}/processing-runs", params=params)

    def get_processing_run(self, processing_run_id: str) -> dict[str, Any]:
        return self._request("GET", f"{self.prefix}/processing-runs/{processing_run_id}")

    def evaluate_processing_run_qa(self, processing_run_id: str, **params: Any) -> dict[str, Any]:
        return self._request(
            "POST",
            f"{self.prefix}/processing-runs/{processing_run_id}/qa",
            params=params,
        )

    def list_qa_findings(self, processing_run_id: str, **params: Any) -> dict[str, Any]:
        return self._request(
            "GET",
            f"{self.prefix}/processing-runs/{processing_run_id}/qa-findings",
            params=params,
        )

    def list_facts(self, **params: Any) -> dict[str, Any]:
        return self._request("GET", f"{self.prefix}/facts", params=params)

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        auth: bool = True,
    ) -> dict[str, Any]:
        headers: dict[str, str] = {"Accept": "application/json"}
        if auth:
            if not self.token:
                raise ApiClientError(401, "AUTHENTICATION_FAILED", "Authentication required.")
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            response = self._client.request(
                method,
                path,
                params=_query(params),
                json=json_body,
                headers=headers,
            )
        except httpx.RequestError as exc:
            raise ApiClientError(0, NETWORK_FAILURE, "Network failure contacting the API.") from exc
        return _parse_response(response)


def _parse_response(response: httpx.Response) -> dict[str, Any]:
    if response.status_code == 204 or not response.content:
        if response.status_code >= 400:
            raise ApiClientError(
                response.status_code,
                "INTERNAL_ERROR",
                "An unexpected error occurred.",
            )
        return {}
    try:
        payload = response.json()
    except ValueError as exc:
        if response.status_code >= 400:
            raise ApiClientError(
                response.status_code,
                "INTERNAL_ERROR",
                "An unexpected error occurred.",
            ) from exc
        raise ApiClientError(
            response.status_code, "INTERNAL_ERROR", "Invalid JSON response."
        ) from exc

    if response.status_code >= 400:
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            raise ApiClientError(
                response.status_code,
                str(error.get("code") or "INTERNAL_ERROR"),
                str(error.get("message") or "Request failed."),
                error.get("details") if isinstance(error.get("details"), list) else None,
            )
        raise ApiClientError(response.status_code, "INTERNAL_ERROR", "Request failed.")
    if not isinstance(payload, dict):
        raise ApiClientError(response.status_code, "INTERNAL_ERROR", "Invalid JSON response.")
    return payload
