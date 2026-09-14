"""P13E upload / request limits.

In-memory only. Does not open hosted databases or August files.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import struct
import time
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_STORED, ZipFile

import pytest
from dfip_api.app import create_app
from dfip_api.errors import PayloadTooLarge
from dfip_api.identity_store import InMemoryIdentityStore
from dfip_api.limits import JSON_BODY_TOO_LARGE, AttemptLimiter
from dfip_api.publication_routes import _REPORT_GENERATION
from dfip_api.publication_service import ROW_CAP_WORKBOOK_MESSAGE
from dfip_api.schemas import MAX_PAGE_LIMIT
from dfip_api.upload_service import (
    MAX_UNCOMPRESSED_BYTES,
    MAX_ZIP_MEMBERS,
    UploadService,
    _assert_xlsx_payload,
)
from dfip_config.settings import BOOTSTRAP_TOKEN_HEADER, Settings
from dfip_core.ingest.store import InMemoryIngestStore
from dfip_core.transform.store import InMemoryFactStore
from dfip_db.identity import MembershipRow
from fastapi.testclient import TestClient

from catalog_support import logic_xlsx
from http_ingest_support import (
    complete_reprocess_response,
    post_upload,
    source_row,
    upload_workbook,
    upload_workbooks,
    workbook_bytes,
)
from test_company_management_acceptance import OPERATOR_USER, _setup_app
from test_p5_api import (
    AUTH,
    CLIENT_ID,
    JWT_SECRET,
    PRODUCTION_BOOTSTRAP_TOKEN,
    _encode_jwt,
    inspector_settings,
    make_settings,
    production_settings,
)
from test_p7_publication import _publish, publisher_app, publisher_settings
from test_p9_authz import _error
from test_p13a_production import OPERATOR_PROD_PASS
from test_p13b_recovery import AUTH_A, _memory_app
from test_v2_upload_async import GatedIngestStore

ITERATIONS = 1000
PASSWORD = "correct-password"
USER_A = "limit.user"
USER_B = "limit.other"
CLIENT_B = "a0000000-0000-4000-8000-000000000002"
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
TOO_MANY = "Too many requests."


def _error_message(response) -> str:
    return _error(response)["message"]


def _password_app(**overrides):
    identity = InMemoryIdentityStore()
    identity.put_password_user(
        subject=USER_A,
        password=PASSWORD,
        iterations=ITERATIONS,
        memberships=(MembershipRow(CLIENT_ID, "client"),),
    )
    identity.put_password_user(
        subject=USER_B,
        password=PASSWORD,
        iterations=ITERATIONS,
        memberships=(MembershipRow(CLIENT_ID, "client"),),
    )
    return create_app(
        settings=make_settings(
            dfip_auth_mode="jwt",
            dfip_auth_secret=JWT_SECRET,
            dfip_password_pbkdf2_iterations=ITERATIONS,
            **overrides,
        ),
        ingest_store=InMemoryIngestStore(),
        fact_store=InMemoryFactStore(),
        identity_store=identity,
    )


def _publisher_app(**overrides):
    ingest = overrides.pop("ingest_store", None)
    return create_app(
        settings=publisher_settings(**overrides),
        ingest_store=ingest if ingest is not None else InMemoryIngestStore(),
        fact_store=InMemoryFactStore(),
    )


def _login(http: TestClient, username: str, password: str, extra_headers=None):
    return http.post(
        "/api/v1/auth/login",
        headers=extra_headers or {},
        json={"username": username, "password": password},
    )


def _xlsx_zip(*, names: list[str] | None = None, extra_count: int = 0) -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_STORED) as archive:
        archive.writestr("[Content_Types].xml", b"<Types/>")
        archive.writestr("xl/workbook.xml", b"<workbook/>")
        if names:
            for name in names:
                archive.writestr(name, b"<x/>")
        for index in range(extra_count):
            archive.writestr(f"xl/part{index}.xml", b"<x/>")
    return buffer.getvalue()


def _claimed_uncompressed_zip(claimed: int) -> bytes:
    payload = bytearray(_xlsx_zip())
    packed = struct.pack("<I", claimed & 0xFFFFFFFF)
    pos = 0
    while True:
        loc = payload.find(b"PK\x03\x04", pos)
        if loc < 0:
            break
        payload[loc + 22 : loc + 26] = packed
        pos = loc + 4
    pos = 0
    while True:
        cen = payload.find(b"PK\x01\x02", pos)
        if cen < 0:
            break
        payload[cen + 24 : cen + 28] = packed
        pos = cen + 4
    return bytes(payload)


# Byte size of repo-root `Web Engage - Daily Report - FY-2026.xlsx`.
# Tests compare integers; they do not load that workbook.
FY2026_RAW_BYTES = 53_832_336
PRODUCT_PER_FILE = 64 * 1024 * 1024
PRODUCT_TOTAL = 128 * 1024 * 1024
OPERATOR_OVERRIDE_PER_FILE = 50 * 1024 * 1024
OPERATOR_OVERRIDE_TOTAL = 100 * 1024 * 1024


def test_settings_p13e_defaults(monkeypatch) -> None:
    monkeypatch.delenv("DFIP_UPLOAD_MAX_BYTES", raising=False)
    monkeypatch.delenv("DFIP_UPLOAD_MAX_FILES", raising=False)
    monkeypatch.delenv("DFIP_UPLOAD_MAX_TOTAL_BYTES", raising=False)
    monkeypatch.delenv("DFIP_UPLOAD_MAX_FACTS", raising=False)
    monkeypatch.delenv("DFIP_UPLOAD_MAX_PROCESSING_SECONDS", raising=False)
    settings = Settings(_env_file=None)
    assert settings.dfip_upload_max_bytes == PRODUCT_PER_FILE
    assert settings.dfip_upload_max_files == 5
    assert settings.dfip_upload_max_total_bytes == PRODUCT_TOTAL
    assert settings.upload_max_total_bytes == PRODUCT_TOTAL
    assert settings.dfip_json_max_body_bytes == 256 * 1024
    assert settings.dfip_download_max_rows == 75_000
    assert settings.dfip_upload_max_facts == 750_000
    assert settings.dfip_upload_max_processing_seconds == 0
    assert settings.dfip_upload_max_facts > 200_000
    assert settings.dfip_worker_concurrency == 1
    assert MAX_PAGE_LIMIT == 200
    assert MAX_ZIP_MEMBERS == 1024
    assert MAX_UNCOMPRESSED_BYTES == 512 * 1024 * 1024


def test_fy2026_raw_size_is_allowed_by_product_per_file_limit() -> None:
    settings = Settings(_env_file=None)
    assert PRODUCT_PER_FILE == 67_108_864
    assert PRODUCT_TOTAL == 134_217_728
    assert FY2026_RAW_BYTES == 53_832_336
    assert FY2026_RAW_BYTES > OPERATOR_OVERRIDE_PER_FILE
    assert FY2026_RAW_BYTES <= settings.dfip_upload_max_bytes
    assert FY2026_RAW_BYTES <= settings.dfip_upload_max_total_bytes
    assert settings.dfip_upload_max_bytes == PRODUCT_PER_FILE
    assert settings.dfip_upload_max_total_bytes == PRODUCT_TOTAL


def test_operator_override_caps_remain_env_configurable(monkeypatch) -> None:
    """50/100 MiB remain valid operator env overrides, not the product default."""
    monkeypatch.delenv("DFIP_UPLOAD_MAX_BYTES", raising=False)
    monkeypatch.delenv("DFIP_UPLOAD_MAX_FILES", raising=False)
    monkeypatch.delenv("DFIP_UPLOAD_MAX_TOTAL_BYTES", raising=False)
    settings = inspector_settings(
        dfip_upload_max_bytes=OPERATOR_OVERRIDE_PER_FILE,
        dfip_upload_max_files=5,
        dfip_upload_max_total_bytes=OPERATOR_OVERRIDE_TOTAL,
    )
    assert settings.dfip_upload_max_bytes == 52_428_800
    assert settings.upload_max_total_bytes == 104_857_600
    assert settings.dfip_upload_max_files == 5
    assert settings.dfip_json_max_body_bytes == 256 * 1024
    assert settings.dfip_download_max_rows == 75_000
    assert MAX_ZIP_MEMBERS == 1024
    assert MAX_UNCOMPRESSED_BYTES == 512 * 1024 * 1024
    defaults = Settings(_env_file=None)
    assert defaults.dfip_upload_max_bytes == PRODUCT_PER_FILE
    assert defaults.dfip_upload_max_total_bytes == PRODUCT_TOTAL


def test_zero_total_bytes_means_twice_per_file() -> None:
    settings = inspector_settings(dfip_upload_max_bytes=64, dfip_upload_max_total_bytes=0)
    assert settings.upload_max_total_bytes == 128


def test_first_five_failed_logins_are_401() -> None:
    http = TestClient(_password_app())
    for _ in range(5):
        response = _login(http, USER_A, "wrong-password")
        assert response.status_code == 401
        assert _error(response)["code"] == "AUTHENTICATION_FAILED"
        assert _error_message(response) == "Invalid authentication credentials."
        assert USER_A not in response.text
        assert "wrong-password" not in response.text


def test_sixth_failed_login_is_429() -> None:
    http = TestClient(_password_app())
    for _ in range(5):
        assert _login(http, USER_A, "wrong-password").status_code == 401
    sixth = _login(http, USER_A, "wrong-password")
    assert sixth.status_code == 429
    assert _error(sixth)["code"] == "TOO_MANY_REQUESTS"
    assert _error_message(sixth) == TOO_MANY
    assert USER_A not in sixth.text
    assert "remaining" not in sixth.text.lower()


def test_username_enumeration_stays_impossible() -> None:
    http = TestClient(_password_app())
    missing = _login(http, "nobody.here", "wrong-password")
    wrong = _login(http, USER_A, "wrong-password")
    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert _error_message(missing) == _error_message(wrong)
    assert "nobody.here" not in missing.text
    assert USER_A not in wrong.text


def test_ip_bucket_throttles_across_usernames() -> None:
    http = TestClient(_password_app())
    for index in range(5):
        assert _login(http, f"missing.user.{index}", "wrong-password").status_code == 401
    sixth = _login(http, "another.missing", "wrong-password")
    assert sixth.status_code == 429
    assert _error_message(sixth) == TOO_MANY


def test_username_bucket_is_independent() -> None:
    limiter = AttemptLimiter(max_failures=5, window_seconds=600)
    for _ in range(5):
        limiter.record_failure("login:user:alice")
    assert limiter.is_blocked("login:user:alice")
    assert not limiter.is_blocked("login:user:bob")
    assert not limiter.is_blocked("login:ip:1.2.3.4")
    for _ in range(5):
        limiter.record_failure("login:ip:9.9.9.9")
    assert limiter.is_blocked("login:ip:9.9.9.9")
    assert not limiter.is_blocked("login:ip:8.8.8.8")


def test_successful_login_clears_failures() -> None:
    http = TestClient(_password_app())
    for _ in range(4):
        assert _login(http, USER_A, "wrong-password").status_code == 401
    ok = _login(http, USER_A, PASSWORD)
    assert ok.status_code == 200, ok.text
    assert "access_token" in ok.json()
    for _ in range(5):
        assert _login(http, USER_A, "wrong-password").status_code == 401
    assert _login(http, USER_A, "wrong-password").status_code == 429


def test_throttle_expires_after_window() -> None:
    app = _password_app()
    app.state.attempt_limiter = AttemptLimiter(max_failures=5, window_seconds=0.2)
    http = TestClient(app)
    for _ in range(5):
        assert _login(http, USER_A, "wrong-password").status_code == 401
    assert _login(http, USER_A, "wrong-password").status_code == 429
    time.sleep(0.25)
    later = _login(http, USER_A, "wrong-password")
    assert later.status_code == 401
    assert _error_message(later) == "Invalid authentication credentials."


def test_forwarded_for_is_not_trusted() -> None:
    http = TestClient(_password_app())
    for index in range(5):
        response = _login(
            http,
            USER_A,
            "wrong-password",
            extra_headers={"X-Forwarded-For": f"203.0.113.{index}"},
        )
        assert response.status_code == 401
    sixth = _login(
        http,
        USER_A,
        "wrong-password",
        extra_headers={"X-Forwarded-For": "198.51.100.9"},
    )
    assert sixth.status_code == 429


def test_bootstrap_failures_are_throttled() -> None:
    app = _setup_app()
    app.state.settings = production_settings(dfip_bootstrap_token=PRODUCTION_BOOTSTRAP_TOKEN)
    http = TestClient(app)
    body = {
        "username": OPERATOR_USER,
        "password": OPERATOR_PROD_PASS,
        "confirm_password": OPERATOR_PROD_PASS,
    }
    for _ in range(5):
        refused = http.post(
            "/api/v1/auth/setup-publisher",
            headers={BOOTSTRAP_TOKEN_HEADER: "wrong-bootstrap-token-value"},
            json=body,
        )
        assert refused.status_code == 401
        assert PRODUCTION_BOOTSTRAP_TOKEN not in refused.text
        assert "wrong-bootstrap-token-value" not in refused.text
    sixth = http.post(
        "/api/v1/auth/setup-publisher",
        headers={BOOTSTRAP_TOKEN_HEADER: "wrong-bootstrap-token-value"},
        json=body,
    )
    assert sixth.status_code == 429
    assert _error_message(sixth) == TOO_MANY
    assert OPERATOR_USER not in sixth.text


def test_valid_bootstrap_still_works_when_allowed() -> None:
    app = _setup_app()
    app.state.settings = production_settings(dfip_bootstrap_token=PRODUCTION_BOOTSTRAP_TOKEN)
    http = TestClient(app)
    created = http.post(
        "/api/v1/auth/setup-publisher",
        headers={BOOTSTRAP_TOKEN_HEADER: PRODUCTION_BOOTSTRAP_TOKEN},
        json={
            "username": OPERATOR_USER,
            "password": OPERATOR_PROD_PASS,
            "confirm_password": OPERATOR_PROD_PASS,
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["username"] == OPERATOR_USER


def test_oversized_single_file_remains_413() -> None:
    app = _publisher_app(dfip_upload_max_bytes=64)
    payload = b"PK" + b"A" * 200
    response = upload_workbook(
        TestClient(app), payload, "big.xlsx", headers=AUTH, client_id=CLIENT_ID
    )
    assert response.status_code == 413
    assert _error(response)["code"] == "PAYLOAD_TOO_LARGE"
    assert "64" in _error_message(response)


class _ReportedSize:
    """Chunk whose len() is independent of allocated bytes. Avoids 64 MiB fixtures."""

    def __init__(self, size: int) -> None:
        self._size = size

    def __bool__(self) -> bool:
        return self._size > 0

    def __len__(self) -> int:
        return self._size


class _ReportedUpload:
    def __init__(self, size: int, name: str = "reported.xlsx") -> None:
        self.filename = name
        self._size = size
        self._sent = False

    async def read(self, _n: int = 65536):
        if self._sent:
            return b""
        self._sent = True
        return _ReportedSize(self._size)


def test_product_per_file_limit_rejects_over_sixty_four_mib() -> None:
    service = _publisher_app().state.upload_service

    async def _run() -> bytes:
        return await service.read_upload(_ReportedUpload(PRODUCT_PER_FILE + 1))

    with pytest.raises(PayloadTooLarge) as caught:
        asyncio.run(_run())
    assert caught.value.message == (
        f"Workbook exceeds the maximum allowed size of {PRODUCT_PER_FILE} bytes."
    )


def test_product_total_limit_rejects_over_remaining_budget() -> None:
    """A part can be under 64 MiB and still exceed leftover budget of the 128 MiB total."""
    service = _publisher_app().state.upload_service
    leftover = 32 * 1024 * 1024
    assert leftover < PRODUCT_PER_FILE
    assert leftover + 1 <= PRODUCT_PER_FILE

    async def _run() -> bytes:
        return await service.read_upload(
            _ReportedUpload(leftover + 1),
            remaining_total_bytes=leftover,
        )

    with pytest.raises(PayloadTooLarge) as caught:
        asyncio.run(_run())
    assert caught.value.message == "Upload request exceeds the maximum allowed size."


def test_exactly_max_size_succeeds(tmp_path: Path) -> None:
    content = workbook_bytes(tmp_path / "exact.xlsx", [source_row()])
    app = _publisher_app(dfip_upload_max_bytes=len(content))
    response = upload_workbook(
        TestClient(app), content, "exact.xlsx", headers=AUTH, client_id=CLIENT_ID
    )
    assert response.status_code == 201, response.text
    assert response.json()["processing_run"]["status"] == "succeeded"


def test_small_workbook_accepted_under_product_caps(tmp_path: Path) -> None:
    content = workbook_bytes(tmp_path / "under.xlsx", [source_row()])
    assert len(content) < PRODUCT_PER_FILE
    app = _publisher_app(
        dfip_upload_max_bytes=PRODUCT_PER_FILE,
        dfip_upload_max_files=5,
        dfip_upload_max_total_bytes=PRODUCT_TOTAL,
    )
    response = upload_workbook(
        TestClient(app), content, "under.xlsx", headers=AUTH, client_id=CLIENT_ID
    )
    assert response.status_code == 201, response.text


def test_exactly_configured_cap_succeeds_without_large_disk_fixture(tmp_path: Path) -> None:
    """Exact-size acceptance is the configured integer, not a 64 MiB disk fixture."""
    content = workbook_bytes(tmp_path / "exact-cap.xlsx", [source_row()])
    app = _publisher_app(
        dfip_upload_max_bytes=len(content),
        dfip_upload_max_total_bytes=PRODUCT_TOTAL,
    )
    response = upload_workbook(
        TestClient(app), content, "exact-cap.xlsx", headers=AUTH, client_id=CLIENT_ID
    )
    assert response.status_code == 201, response.text


def test_default_five_file_limit_still_enforced(tmp_path: Path) -> None:
    content = workbook_bytes(tmp_path / "a.xlsx", [source_row()])
    app = _publisher_app()
    parts = [(f"f{index}.xlsx", content) for index in range(6)]
    response = upload_workbooks(TestClient(app), parts, headers=AUTH, client_id=CLIENT_ID)
    assert response.status_code == 413
    assert _error_message(response) == "Too many files in one upload request."


def test_five_files_at_default_count_cap_still_accepted(tmp_path: Path) -> None:
    parts = [
        (
            f"s{index}.xlsx",
            workbook_bytes(
                tmp_path / f"s{index}.xlsx",
                [source_row(**{"Campaign ID": f"camp-{index}"})],
            ),
        )
        for index in range(5)
    ]
    app = _publisher_app()
    response = upload_workbooks(TestClient(app), parts, headers=AUTH, client_id=CLIENT_ID)
    assert response.status_code in {200, 201, 202}, response.text


def test_n_plus_one_files_rejected(tmp_path: Path) -> None:
    content = workbook_bytes(tmp_path / "a.xlsx", [source_row()])
    app = _publisher_app(dfip_upload_max_files=2)
    parts = [(f"f{index}.xlsx", content) for index in range(3)]
    response = upload_workbooks(TestClient(app), parts, headers=AUTH, client_id=CLIENT_ID)
    assert response.status_code == 413
    assert _error_message(response) == "Too many files in one upload request."


def test_total_byte_cap_rejected(tmp_path: Path) -> None:
    first = workbook_bytes(tmp_path / "a.xlsx", [source_row()])
    second = workbook_bytes(tmp_path / "b.xlsx", [source_row(**{"Campaign ID": "camp-b"})])
    third = workbook_bytes(tmp_path / "c.xlsx", [source_row(**{"Campaign ID": "camp-c"})])
    total_cap = len(first) + len(second) - 1
    app = _publisher_app(
        dfip_upload_max_bytes=max(len(first), len(second), len(third)),
        dfip_upload_max_files=5,
        dfip_upload_max_total_bytes=total_cap,
    )
    response = upload_workbooks(
        TestClient(app),
        [("a.xlsx", first), ("b.xlsx", second), ("c.xlsx", third)],
        headers=AUTH,
        client_id=CLIENT_ID,
    )
    assert response.status_code == 413
    assert _error_message(response) == "Upload request exceeds the maximum allowed size."


def test_several_small_files_under_total_cap_succeed(tmp_path: Path) -> None:
    parts = [
        (f"s{index}.xlsx", workbook_bytes(tmp_path / f"s{index}.xlsx", [source_row()]))
        for index in range(3)
    ]
    total = sum(len(item[1]) for item in parts)
    app = _publisher_app(
        dfip_upload_max_files=5,
        dfip_upload_max_total_bytes=total,
    )
    response = upload_workbooks(TestClient(app), parts, headers=AUTH, client_id=CLIENT_ID)
    assert response.status_code in {200, 201, 202}, response.text


def test_catalog_upload_still_accepts_one_file() -> None:
    app = _publisher_app(dfip_upload_max_files=1, dfip_upload_max_total_bytes=64)
    http = TestClient(app)
    response = http.post(
        "/api/v1/catalogs/logic",
        headers=AUTH,
        files={"file": ("logic.xlsx", logic_xlsx(), XLSX_TYPE)},
        data={"client_id": CLIENT_ID},
    )
    assert response.status_code == 201, response.text
    assert response.json()["version"]["kind"] == "logic"


def test_no_extra_work_after_file_or_total_limit(tmp_path: Path, monkeypatch) -> None:
    content = workbook_bytes(tmp_path / "a.xlsx", [source_row()])
    reads: list[str] = []
    original = UploadService.read_upload

    async def counting(self, upload, remaining_total_bytes=None):
        reads.append(upload.filename or "")
        return await original(self, upload, remaining_total_bytes=remaining_total_bytes)

    monkeypatch.setattr(UploadService, "read_upload", counting)
    too_many = _publisher_app(dfip_upload_max_files=1)
    upload_workbooks(
        TestClient(too_many),
        [("a.xlsx", content), ("b.xlsx", content)],
        headers=AUTH,
        client_id=CLIENT_ID,
    )
    assert reads == []
    reads.clear()
    total_app = _publisher_app(
        dfip_upload_max_bytes=len(content),
        dfip_upload_max_files=5,
        dfip_upload_max_total_bytes=len(content),
    )
    upload_workbooks(
        TestClient(total_app),
        [("a.xlsx", content), ("b.xlsx", content)],
        headers=AUTH,
        client_id=CLIENT_ID,
    )
    assert reads == ["a.xlsx"]


def test_json_body_under_cap_succeeds() -> None:
    http = TestClient(_password_app())
    response = _login(http, USER_A, PASSWORD)
    assert response.status_code == 200, response.text


def test_json_body_over_cap_with_content_length_is_413() -> None:
    app = _password_app(dfip_json_max_body_bytes=256 * 1024)
    http = TestClient(app)
    payload = b"x" * (256 * 1024 + 1)
    response = http.post(
        "/api/v1/auth/login",
        content=payload,
        headers={"Content-Type": "application/json", "Content-Length": str(len(payload))},
    )
    assert response.status_code == 413
    assert _error(response)["code"] == "PAYLOAD_TOO_LARGE"
    assert _error_message(response) == JSON_BODY_TOO_LARGE
    assert "traceback" not in response.text.lower()


def test_oversized_chunked_body_without_length_is_413() -> None:
    import asyncio

    app = _password_app(dfip_json_max_body_bytes=256 * 1024)
    TestClient(app).get("/health")
    chunks = [b"x" * 80_000, b"x" * 80_000, b"x" * 80_000, b"x" * 40_000]
    state = {"index": 0, "status": 0, "body": b""}

    async def receive():
        index = state["index"]
        if index >= len(chunks):
            return {"type": "http.request", "body": b"", "more_body": False}
        state["index"] += 1
        return {
            "type": "http.request",
            "body": chunks[index],
            "more_body": index < len(chunks) - 1,
        }

    async def send(message):
        if message["type"] == "http.response.start":
            state["status"] = message["status"]
        if message["type"] == "http.response.body":
            state["body"] += message.get("body", b"")

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/v1/auth/login",
        "raw_path": b"/api/v1/auth/login",
        "query_string": b"",
        "headers": [(b"content-type", b"application/json"), (b"host", b"testserver")],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
    }

    asyncio.run(app(scope, receive, send))
    assert state["status"] == 413
    payload = json.loads(state["body"].decode())
    assert payload["error"]["message"] == JSON_BODY_TOO_LARGE


def test_normal_json_requests_unaffected() -> None:
    app, *_rest = publisher_app()
    http = TestClient(app)
    created = _publish(http, notes="p13e notes stay under the JSON cap")
    assert created.status_code == 201, created.text
    login = TestClient(_password_app())
    assert _login(login, USER_A, PASSWORD).status_code == 200


def test_malformed_zip_rejected() -> None:
    app = _publisher_app()
    response = upload_workbook(
        TestClient(app), b"PK\x03\x04not-a-zip", "bad.xlsx", headers=AUTH, client_id=CLIENT_ID
    )
    assert response.status_code == 422
    assert "Workbook could not be read." in _error_message(response)


def test_claimed_uncompressed_july_scale_is_accepted() -> None:
    """July Prod claims ~160 MiB uncompressed; that is a legitimate monthly file."""
    payload = _claimed_uncompressed_zip(160 * 1024 * 1024)
    _assert_xlsx_payload(payload)


def test_claimed_uncompressed_april_may_scale_is_accepted() -> None:
    # Default fixture has two ZIP members; 147 MiB each ≈ 294 MiB total (Apr–May Prod).
    payload = _claimed_uncompressed_zip(147 * 1024 * 1024)
    _assert_xlsx_payload(payload)


def test_claimed_uncompressed_over_cap_rejected() -> None:
    payload = _claimed_uncompressed_zip(MAX_UNCOMPRESSED_BYTES + 1)
    try:
        _assert_xlsx_payload(payload)
        raised = False
    except Exception as exc:
        raised = True
        assert "uncompressed size exceeds the allowed limit" in str(exc)
    assert raised
    app = _publisher_app()
    response = upload_workbook(
        TestClient(app), payload, "bomb.xlsx", headers=AUTH, client_id=CLIENT_ID
    )
    assert response.status_code == 422
    assert _error(response)["code"] == "VALIDATION_ERROR"
    assert "uncompressed size exceeds the allowed limit" in _error_message(response)
    assert "traceback" not in response.text.lower()


def test_excessive_zip_entry_count_rejected() -> None:
    payload = _xlsx_zip(extra_count=MAX_ZIP_MEMBERS)
    try:
        _assert_xlsx_payload(payload)
        raised = False
    except Exception:
        raised = True
    assert raised
    app = _publisher_app()
    response = upload_workbook(
        TestClient(app), payload, "many.xlsx", headers=AUTH, client_id=CLIENT_ID
    )
    assert response.status_code == 422
    assert "Workbook could not be read." in _error_message(response)


def test_zip_traversal_and_absolute_names_rejected() -> None:
    names = (
        "../xl/workbook.xml",
        "/tmp/evil.xml",
        "C:/Windows/evil.xml",
        "xl/../../etc/passwd",
    )
    for name in names:
        payload = _xlsx_zip(names=[name])
        try:
            _assert_xlsx_payload(payload)
            raised = False
        except Exception as exc:
            raised = True
            assert "Workbook could not be read." in str(exc)
        assert raised, name


def test_legitimate_workbook_accepted(tmp_path: Path) -> None:
    content = workbook_bytes(tmp_path / "ok.xlsx", [source_row()])
    _assert_xlsx_payload(content)
    response = upload_workbook(
        TestClient(_publisher_app()), content, "ok.xlsx", headers=AUTH, client_id=CLIENT_ID
    )
    assert response.status_code == 201, response.text


def test_pagination_limit_200_succeeds() -> None:
    app, *_rest = publisher_app()
    http = TestClient(app)
    response = http.get(f"/api/v1/facts?limit={MAX_PAGE_LIMIT}", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["pagination"]["limit"] == 200


def test_pagination_limit_201_remains_422() -> None:
    app, *_rest = publisher_app()
    http = TestClient(app)
    response = http.get(f"/api/v1/facts?limit={MAX_PAGE_LIMIT + 1}", headers=AUTH)
    assert response.status_code == 422
    assert _error(response)["code"] == "INVALID_PAGINATION"


def test_active_run_returns_existing_202(tmp_path: Path) -> None:
    store = GatedIngestStore()
    store.release.clear()
    app = create_app(
        settings=publisher_settings(),
        ingest_store=store,
        fact_store=InMemoryFactStore(),
    )
    http = TestClient(app)
    content = workbook_bytes(tmp_path / "same.xlsx", [source_row()])
    try:
        first = post_upload(http, content, "same.xlsx", headers=AUTH, client_id=CLIENT_ID)
        assert first.status_code == 202
        second = post_upload(http, content, "same.xlsx", headers=AUTH, client_id=CLIENT_ID)
        assert second.status_code == 202
        assert second.json()["batch"]["batch_id"] == first.json()["batch"]["batch_id"]
    finally:
        store.release.set()


def test_p13b_retry_remains_available(tmp_path: Path) -> None:
    from dfip_api.recovery import ABANDONED_RUN_REASON

    app = _memory_app()
    http = TestClient(app)
    content = workbook_bytes(tmp_path / "retry.xlsx", [source_row()])
    first = upload_workbook(http, content, "retry.xlsx", headers=AUTH_A)
    batch_id = first.json()["batch"]["batch_id"]
    run_id = first.json()["processing_run"]["processing_run_id"]
    ingest = app.state.ingest_store
    run = ingest.get_processing_run(run_id)
    assert run is not None
    run.status = "failed"
    run.error_summary = ABANDONED_RUN_REASON
    ingest.save_processing_run(run)
    ingest.reset_staging_for_batch(batch_id)
    recovered = http.post(f"/api/v1/batches/{batch_id}/process", headers=AUTH_A)
    body = complete_reprocess_response(http, recovered, headers=AUTH_A).json()
    assert body["processing_run"]["processing_run_id"] != run_id
    assert body["processing_run"]["status"] == "succeeded"
    assert body["published"] is False


def test_no_duplicate_fact_grains(tmp_path: Path) -> None:
    app = _publisher_app()
    http = TestClient(app)
    content = workbook_bytes(tmp_path / "one.xlsx", [source_row()])
    first = upload_workbook(http, content, "one.xlsx", headers=AUTH, client_id=CLIENT_ID)
    assert first.status_code == 201
    replay = upload_workbook(http, content, "one.xlsx", headers=AUTH, client_id=CLIENT_ID)
    assert replay.status_code == 200
    facts = http.get("/api/v1/facts", headers=AUTH).json()
    assert facts["pagination"]["total"] == 1


def test_download_row_cap_remains_enforced() -> None:
    app, *_rest = publisher_app(dfip_download_max_rows=1)
    http = TestClient(app)
    created = _publish(http)
    assert created.status_code == 201
    response = http.get(
        "/api/v1/publications/current/client-report.xlsx",
        headers=AUTH,
        params={"client_id": CLIENT_ID},
    )
    assert response.status_code == 422
    assert _error_message(response) == ROW_CAP_WORKBOOK_MESSAGE
    assert make_settings().dfip_download_max_rows == 75_000


def test_concurrent_report_generation_is_bounded() -> None:
    app, *_rest = publisher_app()
    http = TestClient(app)
    assert _REPORT_GENERATION.acquire(blocking=False)
    try:
        response = http.get(
            "/api/v1/publications/current/client-report.xlsx",
            headers=AUTH,
            params={"client_id": CLIENT_ID},
        )
        assert response.status_code == 429
        assert _error_message(response) == (
            "A workbook is already being generated. Wait for it to finish, then try again."
        )
    finally:
        _REPORT_GENERATION.release()


def test_health_remains_cheap_and_public() -> None:
    http = TestClient(_publisher_app())
    response = http.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["database"]["status"] == "not_checked"


def test_health_source_has_no_limiter_or_database() -> None:
    source = inspect.getsource(create_app)
    start = source.index("async def health")
    health_src = source[start : source.index("application.include_router", start)]
    assert "attempt_limiter" not in health_src
    assert "db_pool" not in health_src
    assert "probe_database" not in health_src
    assert "read_upload" not in health_src


def test_bound_jwt_client_id_cannot_be_overridden(tmp_path: Path) -> None:
    app = create_app(
        settings=make_settings(
            dfip_auth_mode="jwt",
            dfip_auth_secret=JWT_SECRET,
            dfip_dev_auth_role="publisher",
        ),
        ingest_store=InMemoryIngestStore(),
        fact_store=InMemoryFactStore(),
    )
    http = TestClient(app)
    publisher = {"Authorization": f"Bearer {_encode_jwt(role='publisher', client_id=CLIENT_ID)}"}
    content = workbook_bytes(tmp_path / "a.xlsx", [source_row()])
    uploaded = upload_workbook(
        http, content, "a.xlsx", headers=publisher, client_id=CLIENT_ID
    ).json()
    http.post(
        "/api/v1/publications",
        headers=publisher,
        json={
            "client_id": CLIENT_ID,
            "processing_run_id": uploaded["processing_run"]["processing_run_id"],
        },
    )
    headers = {"Authorization": f"Bearer {_encode_jwt(role='client', client_id=CLIENT_B)}"}
    denied = http.get(
        "/api/v1/publications/current/facts",
        headers=headers,
        params={"client_id": CLIENT_ID},
    )
    assert denied.status_code == 403
    scoped = http.get("/api/v1/publications/current/facts", headers=headers)
    assert scoped.status_code == 200
    assert scoped.json()["pagination"]["total"] == 0
