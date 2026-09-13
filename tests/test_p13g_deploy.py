"""P13G deployment artifacts, loopback proxy IP trust, production-local CLI.

Does not provision DigitalOcean. Does not use hosted/live/August data.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from dfip_api.backup import BackupError, assert_safe_restore_target
from dfip_api.limits import _forwarded_client_ip, _is_loopback_peer, request_peer_ip
from dfip_api.purge import PurgeError, assert_safe_purge_target
from fastapi import Request
from fastapi.testclient import TestClient

from test_p13e_limits import USER_A, USER_B, _login, _password_app

REPO = Path(__file__).resolve().parents[1]
DEPLOY = REPO / "deploy"
SECRET_MARKERS = (
    "sk_live",
    "BEGIN PRIVATE KEY",
    "aws_secret",
    "ghp_",
    "password=hunter",
    "changemechangemechangemechangeme",
)


def test_deployment_artifacts_exist() -> None:
    required = [
        DEPLOY / "Caddyfile",
        DEPLOY / "README.md",
        DEPLOY / "env" / "production.env.example",
        DEPLOY / "env" / "operator.env.example",
        DEPLOY / "systemd" / "dfip-api.service",
        DEPLOY / "systemd" / "dfip-web.service",
        DEPLOY / "systemd" / "dfip-backup.service",
        DEPLOY / "systemd" / "dfip-backup.timer",
        DEPLOY / "scripts" / "backup-run.sh",
        DEPLOY / "scripts" / "deploy.sh",
        DEPLOY / "scripts" / "migrate.sh",
        DEPLOY / "scripts" / "rollback.sh",
        DEPLOY / "scripts" / "smoke.py",
        DEPLOY / "scripts" / "rehearse.py",
        REPO / "documentation" / "V1_DEPLOYMENT.md",
    ]
    missing = [str(path) for path in required if not path.is_file()]
    assert missing == []


def test_deployment_files_contain_no_secrets() -> None:
    texts: list[str] = []
    for path in DEPLOY.rglob("*"):
        if path.is_file():
            texts.append(path.read_text(encoding="utf-8"))
    blob = "\n".join(texts).lower()
    for marker in SECRET_MARKERS:
        assert marker.lower() not in blob
    assert (
        "dfip_auth_secret="
        in (DEPLOY / "env" / "production.env.example").read_text(encoding="utf-8").lower()
    )
    env = (DEPLOY / "env" / "production.env.example").read_text(encoding="utf-8")
    assert "DFIP_AUTH_SECRET=\n" in env.replace("\r\n", "\n")
    assert "YOUR_PASSWORD" in env


def test_systemd_units_are_loopback_and_non_root() -> None:
    api = (DEPLOY / "systemd" / "dfip-api.service").read_text(encoding="utf-8")
    web = (DEPLOY / "systemd" / "dfip-web.service").read_text(encoding="utf-8")
    for body in (api, web):
        assert "User=dfip" in body
        assert "EnvironmentFile=/etc/dfip/dfip.env" in body
        assert "Restart=on-failure" in body
        assert "StandardOutput=journal" in body
        assert "python -m dfip_" in body
        assert "reload=True" not in body
    assert "python -m dfip_api" in api
    assert "python -m dfip_web" in web
    env = (DEPLOY / "env" / "production.env.example").read_text(encoding="utf-8")
    assert "DFIP_API_HOST=127.0.0.1" in env
    assert "DFIP_WEB_HOST=127.0.0.1" in env
    assert "DFIP_ENV=production" in env
    assert "DFIP_AUTH_MODE=jwt" in env
    assert "0.0.0.0" not in env


def test_caddyfile_routes_health_and_api() -> None:
    text = (DEPLOY / "Caddyfile").read_text(encoding="utf-8")
    assert "127.0.0.1:8000" in text
    assert "127.0.0.1:3000" in text
    assert "/api/v1" in text
    assert "handle /health" in text
    assert "dfip.example.com" in text
    assert "YOUR_PASSWORD" not in text
    assert "DFIP_AUTH_SECRET" not in text


def test_production_local_restore_guard() -> None:
    local_dfip = "postgresql://dfip_operator@127.0.0.1:5432/dfip"
    assert assert_safe_restore_target(local_dfip, confirmed=False, production_local=True) == "dfip"
    with pytest.raises(BackupError, match="combine"):
        assert_safe_restore_target(local_dfip, confirmed=True, production_local=True)
    with pytest.raises(BackupError, match="database name"):
        assert_safe_restore_target(local_dfip, confirmed=True)
    with pytest.raises(BackupError, match="only database name"):
        assert_safe_restore_target(
            "postgresql://dfip_operator@127.0.0.1:5432/dfip_other",
            confirmed=False,
            production_local=True,
        )
    with pytest.raises(BackupError, match="hosted"):
        assert_safe_restore_target(
            "postgresql://postgres.abc:x@aws-0-us.pooler.supabase.com:5432/dfip",
            confirmed=False,
            production_local=True,
        )


def test_production_local_purge_guard() -> None:
    local_dfip = "postgresql://dfip_operator@127.0.0.1:5432/dfip"
    assert assert_safe_purge_target(local_dfip, confirmed=False, production_local=True) == "dfip"
    with pytest.raises(PurgeError, match="combine"):
        assert_safe_purge_target(local_dfip, confirmed=True, production_local=True)
    with pytest.raises(PurgeError, match="database name"):
        assert_safe_purge_target(local_dfip, confirmed=True)
    with pytest.raises(PurgeError, match="hosted"):
        assert_safe_purge_target(
            "postgresql://postgres.abc:x@aws-0-us.pooler.supabase.com:5432/dfip",
            confirmed=False,
            production_local=True,
        )


def test_loopback_peer_helper() -> None:
    assert _is_loopback_peer("127.0.0.1")
    assert _is_loopback_peer("::1")
    assert not _is_loopback_peer("testclient")
    assert not _is_loopback_peer("203.0.113.9")


def test_forwarded_for_from_loopback_uses_rightmost() -> None:
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": [(b"x-forwarded-for", b"203.0.113.1, 198.51.100.9")],
        "client": ("127.0.0.1", 50000),
        "server": ("127.0.0.1", 8000),
    }
    request = Request(scope)
    assert request_peer_ip(request) == "198.51.100.9"
    assert _forwarded_client_ip(request) == "198.51.100.9"
    remote = Request({**scope, "client": ("203.0.113.10", 50000)})
    assert request_peer_ip(remote) == "203.0.113.10"


def test_forwarded_for_is_not_trusted_from_testclient() -> None:
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


def test_loopback_proxy_throttles_by_forwarded_ip() -> None:
    http = TestClient(_password_app(), client=("127.0.0.1", 50000))
    for index in range(4):
        response = _login(
            http,
            USER_A,
            "wrong-password",
            extra_headers={"X-Forwarded-For": f"198.51.100.{index + 1}"},
        )
        assert response.status_code == 401
    other_user = _login(
        http,
        USER_B,
        "wrong-password",
        extra_headers={"X-Forwarded-For": "198.51.100.5"},
    )
    assert other_user.status_code == 401

    same = TestClient(_password_app(), client=("127.0.0.1", 50000))
    for _ in range(5):
        response = _login(
            same,
            USER_A,
            "wrong-password",
            extra_headers={"X-Forwarded-For": "198.51.100.20"},
        )
        assert response.status_code == 401
    blocked = _login(
        same,
        USER_A,
        "wrong-password",
        extra_headers={"X-Forwarded-For": "198.51.100.20"},
    )
    assert blocked.status_code == 429


def test_rehearse_script_documents_production_grade_mode() -> None:
    text = (DEPLOY / "scripts" / "rehearse.py").read_text(encoding="utf-8")
    assert "--production-grade" in text
    assert '"DFIP_ENV": "production"' in text
    assert "https://dfip.example.com" in text
    assert "production-like rehearsal" in text
    assert "package-only" in text
    assert "HOST-DEFINED" in text
    assert "https_tls" in text
    assert "digitalocean" in text

