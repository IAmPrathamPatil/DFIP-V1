"""Data Model .xlsm grant stamper: preserve VertiPaq, mint a fresh Excel JWT."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path
from zipfile import ZipFile

import jwt
from dfip_api.excel_grant import EXCEL_TOKEN_TYP
from dfip_db.local_demo_guard import COMPANY_2_CLIENT_ID, DEFAULT_CLIENT_ID
from dfip_web.refreshable_xlsm_stamp import (
    CANONICAL_SHA256,
    MODEL_PART,
    PRODUCTION_API_BASE_URL,
    VBA_PART,
    resolve_refreshable_xlsm_template,
    stamp_refreshable_xlsm,
    stamp_refreshable_xlsm_file,
    zip_uncompressed_diffs,
)
from fastapi.testclient import TestClient

from refreshable_xlsm_fixture import (
    TEMPLATE_PLACEHOLDER_JWT,
    build_minimal_refreshable_xlsm,
)
from test_company_registry import (
    CLIENT2_PASSWORD,
    CLIENT_PASSWORD,
    DEMO_CLIENT_2_SUBJECT,
    DEMO_CLIENT_SUBJECT,
    DEMO_PUBLISHER_SUBJECT,
    PUBLISHER_PASSWORD,
    _bearer,
    _memory_app,
    _publish_synthetic,
    _select,
    _token,
)
from test_company_workbook import CAMP_A, CAMP_B, _settings_value
from test_p5_api import JWT_SECRET
from test_p8_client_report import _session_jwt_stamped

ROOT = Path(__file__).resolve().parents[1]
SURVIVING = (
    MODEL_PART,
    VBA_PART,
    "xl/pivotCache/pivotCacheDefinition1.xml",
    "xl/slicerCaches/slicerCache1.xml",
)


def _claims(token: str) -> dict:
    return jwt.decode(token, JWT_SECRET, algorithms=["HS256"], options={"verify_exp": False})


def _canonical_path() -> Path | None:
    for path in (
        ROOT / "excel" / "Client_Report_Refreshable.xlsm",
        ROOT / "Client_Report_DataModel_Typed_POC.xlsm",
        ROOT / "DFIP_Client_Refreshable_Final.xlsm",
    ):
        if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == CANONICAL_SHA256:
            return path
    return None


def test_mini_template_integrity_after_stamping() -> None:
    template = build_minimal_refreshable_xlsm()
    stamped = stamp_refreshable_xlsm(template, bearer_token="aaa.bbb.ccc")
    assert stamped.startswith(b"PK")
    with ZipFile(io.BytesIO(stamped)) as archive:
        names = archive.namelist()
        assert MODEL_PART in names
        assert VBA_PART in names
        assert archive.read(MODEL_PART) == b"MODEL-ITEM-DATA"
        assert archive.read(VBA_PART) == b"VBA-PROJECT"
    assert zip_uncompressed_diffs(template, stamped) == ("xl/sharedStrings.xml",)
    assert _settings_value(stamped, "ApiBaseUrl") == PRODUCTION_API_BASE_URL
    assert _settings_value(stamped, "BearerToken") == "aaa.bbb.ccc"
    assert _settings_value(stamped, "ClientId") in {None, ""}
    assert TEMPLATE_PLACEHOLDER_JWT not in stamped.decode("latin-1")
    assert "localhost" not in stamped.decode("latin-1")
    assert "127.0.0.1" not in stamped.decode("latin-1")
    assert "dev_token" not in stamped.decode("latin-1")


def test_blank_stamp_removes_template_grant() -> None:
    template = build_minimal_refreshable_xlsm()
    stamped = stamp_refreshable_xlsm(template, bearer_token="")
    assert _settings_value(stamped, "BearerToken") in {None, ""}
    assert TEMPLATE_PLACEHOLDER_JWT not in stamped.decode("latin-1")


def test_canonical_template_sha_is_enforced() -> None:
    template = build_minimal_refreshable_xlsm()
    huge = template + b"\x00" * 1_000_001
    try:
        stamp_refreshable_xlsm(huge, bearer_token="aaa.bbb.ccc")
    except ValueError as exc:
        assert "SHA-256" in str(exc)
    else:
        raise AssertionError("canonical SHA mismatch must fail")


def test_fresh_client_grants_differ_per_download(tmp_path: Path) -> None:
    http = TestClient(_memory_app())
    unbound = _token(http, DEMO_PUBLISHER_SUBJECT, PUBLISHER_PASSWORD)
    pub_a = _bearer(_select(http, unbound, DEFAULT_CLIENT_ID).json()["access_token"])
    pub_b = _bearer(_select(http, unbound, COMPANY_2_CLIENT_ID).json()["access_token"])
    _publish_synthetic(
        http, tmp_path, publisher=pub_a, client_id=DEFAULT_CLIENT_ID, campaign_id=CAMP_A
    )
    _publish_synthetic(
        http, tmp_path, publisher=pub_b, client_id=COMPANY_2_CLIENT_ID, campaign_id=CAMP_B
    )
    live_a = http.get(
        "/api/v1/publications/current/refreshable-client-report.xlsx",
        headers=pub_a,
    )
    live_b = http.get(
        "/api/v1/publications/current/refreshable-client-report.xlsx",
        headers=pub_b,
    )
    assert live_a.status_code == 200, live_a.text[:300]
    assert live_b.status_code == 200, live_b.text[:300]
    assert live_a.headers.get("content-type", "").startswith(
        "application/vnd.ms-excel.sheet.macroEnabled.12"
    )
    assert live_a.content.startswith(b"PK")
    assert live_b.content.startswith(b"PK")
    assert live_a.content != live_b.content
    token_a = _settings_value(live_a.content, "BearerToken")
    token_b = _settings_value(live_b.content, "BearerToken")
    assert _session_jwt_stamped(token_a)
    assert _session_jwt_stamped(token_b)
    assert token_a != token_b
    assert isinstance(token_a, str) and isinstance(token_b, str)
    assert token_a not in live_b.content.decode("latin-1")
    assert token_b not in live_a.content.decode("latin-1")
    claims_a = _claims(token_a)
    claims_b = _claims(token_b)
    assert claims_a["typ"] == EXCEL_TOKEN_TYP
    assert claims_b["typ"] == EXCEL_TOKEN_TYP
    assert claims_a["role"] == "client"
    assert claims_b["role"] == "client"
    assert claims_a["client_id"] == DEFAULT_CLIENT_ID
    assert claims_b["client_id"] == COMPANY_2_CLIENT_ID
    assert claims_a.get("jti") != claims_b.get("jti")
    assert claims_a.get("platform_admin") is not True
    assert claims_b.get("platform_admin") is not True
    assert claims_a["role"] != "publisher"
    assert claims_b["role"] != "admin"
    for body in (live_a.content, live_b.content):
        with ZipFile(io.BytesIO(body)) as archive:
            names = set(archive.namelist())
            for part in SURVIVING:
                assert part in names
                assert archive.read(part)
        assert _settings_value(body, "ApiBaseUrl") == PRODUCTION_API_BASE_URL
        assert "localhost" not in body.decode("latin-1")
        assert TEMPLATE_PLACEHOLDER_JWT not in body.decode("latin-1")


def test_client_download_stays_on_own_grant(tmp_path: Path) -> None:
    http = TestClient(_memory_app())
    unbound = _token(http, DEMO_PUBLISHER_SUBJECT, PUBLISHER_PASSWORD)
    pub_a = _bearer(_select(http, unbound, DEFAULT_CLIENT_ID).json()["access_token"])
    pub_b = _bearer(_select(http, unbound, COMPANY_2_CLIENT_ID).json()["access_token"])
    _publish_synthetic(
        http, tmp_path, publisher=pub_a, client_id=DEFAULT_CLIENT_ID, campaign_id=CAMP_A
    )
    _publish_synthetic(
        http, tmp_path, publisher=pub_b, client_id=COMPANY_2_CLIENT_ID, campaign_id=CAMP_B
    )
    client = _bearer(_token(http, DEMO_CLIENT_SUBJECT, CLIENT_PASSWORD))
    denied = http.get(
        "/api/v1/publications/current/refreshable-client-report.xlsx",
        headers=client,
        params={"client_id": COMPANY_2_CLIENT_ID},
    )
    assert denied.status_code == 403
    own = http.get(
        "/api/v1/publications/current/refreshable-client-report.xlsx",
        headers=client,
    )
    assert own.status_code == 200
    claims = _claims(_settings_value(own.content, "BearerToken"))
    assert claims["typ"] == EXCEL_TOKEN_TYP
    assert claims["role"] == "client"
    assert claims["client_id"] == DEFAULT_CLIENT_ID
    other = _bearer(_token(http, DEMO_CLIENT_2_SUBJECT, CLIENT2_PASSWORD))
    other_file = http.get(
        "/api/v1/publications/current/refreshable-client-report.xlsx",
        headers=other,
    )
    assert other_file.status_code == 200
    other_claims = _claims(_settings_value(other_file.content, "BearerToken"))
    assert other_claims["client_id"] == COMPANY_2_CLIENT_ID
    assert other_claims["role"] == "client"


def test_canonical_workbook_parts_survive_stamping() -> None:
    path = _canonical_path()
    if path is None:
        import pytest

        pytest.skip("canonical Data Model xlsm is not in the workspace")
    template = path.read_bytes()
    assert hashlib.sha256(template).hexdigest() == CANONICAL_SHA256
    from dfip_web.published_facts_mashup import extract_published_facts_section_from_package

    with ZipFile(io.BytesIO(template)) as archive:
        section = extract_published_facts_section_from_package(archive)
    assert "dfip-bearer=" in section
    assert 'Authorization = "Bearer "' not in section
    stamped = stamp_refreshable_xlsm_file(path, bearer_token="aaa.bbb.ccc")
    changed = zip_uncompressed_diffs(template, stamped)
    assert changed in {
        ("xl/sharedStrings.xml",),
        ("xl/worksheets/sheet2.xml",),
    }
    with ZipFile(io.BytesIO(template)) as before, ZipFile(io.BytesIO(stamped)) as after:
        assert before.namelist() == after.namelist()
        assert MODEL_PART in after.namelist()
        assert after.read(MODEL_PART) == before.read(MODEL_PART)
        assert after.read(VBA_PART) == before.read(VBA_PART)
        model_parts = [name for name in after.namelist() if name.startswith("xl/model/")]
        assert model_parts
        for name in after.namelist():
            if name in changed:
                continue
            assert after.read(name) == before.read(name)
        caches = [
            name
            for name in after.namelist()
            if name.startswith("xl/pivotCache/") and name.endswith(".xml")
        ]
        slicers = [
            name
            for name in after.namelist()
            if name.startswith("xl/slicerCaches/") and name.endswith(".xml")
        ]
        assert len(caches) >= 10
        assert len(slicers) == 35
    assert _settings_value(stamped, "ApiBaseUrl") == PRODUCTION_API_BASE_URL
    assert _settings_value(stamped, "BearerToken") == "aaa.bbb.ccc"
    assert _settings_value(stamped, "ClientId") in {None, ""}
    assert "localhost" not in stamped.decode("latin-1")
    assert "127.0.0.1" not in stamped.decode("latin-1")


def test_resolve_prefers_settings_override(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture.xlsm"
    fixture.write_bytes(build_minimal_refreshable_xlsm())
    resolved = resolve_refreshable_xlsm_template(str(fixture))
    assert resolved == fixture
