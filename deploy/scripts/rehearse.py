#!/usr/bin/env python3
"""Disposable local P13G rehearsal. Does not create DigitalOcean resources.

Two modes:

- default: DFIP_ENV=test on loopback HTTP. Package start/restart only.
- --production-grade: DFIP_ENV=production, process environment only (no
  repository .env in the child), JWT, HTTPS *origin strings*, OpenAPI off.
  Uses the operator DATABASE_URL / archive / JWT secret from the parent
  environment (or a parent-only .env overlay to copy those values). Does not
  claim TLS termination, systemd, or a DigitalOcean Droplet.

PostgreSQL migrate/backup/restore/purge are exercised by tests/test_p13c_backup.py
and tests/test_p13f_purge.py when DFIP_TEST_DATABASE_URL is set.

systemd and Caddy HTTPS on a public hostname are HOST-DEFINED.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "packages" / "config"))
from dfip_config.settings import KNOWN_BAD_JWT_SECRETS, MIN_JWT_SECRET_LENGTH


def _wait(url: str, attempts: int = 40) -> None:
    for _ in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.25)
    raise RuntimeError(f"timed out waiting for {url}")


def _http_json(url: str) -> tuple[int, object]:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            raw = response.read().decode("utf-8")
            try:
                return response.status, json.loads(raw)
            except json.JSONDecodeError:
                return response.status, raw[:200]
    except urllib.error.HTTPError as exc:
        return exc.code, None


def _read_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _forward_operator_secrets(env: dict[str, str]) -> dict[str, str]:
    """Copy DSN/secret/archive into the child. Never print values."""
    overlay = _read_dotenv(REPO / ".env")
    forwarded = dict(env)
    for key in (
        "DATABASE_URL",
        "DFIP_AUTH_SECRET",
        "DFIP_STORAGE_ENDPOINT",
        "DFIP_STORAGE_BUCKET",
        "DFIP_AUTH_ISSUER",
        "DFIP_AUTH_AUDIENCE",
    ):
        if not forwarded.get(key, "").strip() and overlay.get(key, "").strip():
            forwarded[key] = overlay[key]
    return forwarded


def _test_env(archive: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "DFIP_ENV": "test",
            "DFIP_AUTH_MODE": "jwt",
            "DFIP_AUTH_SECRET": "p13g-rehearsal-secret-not-for-production",
            "DFIP_API_HOST": "127.0.0.1",
            "DFIP_API_PORT": "18080",
            "DFIP_WEB_HOST": "127.0.0.1",
            "DFIP_WEB_PORT": "13000",
            "DFIP_API_BASE_URL": "http://127.0.0.1:18080",
            "DFIP_WEB_ORIGIN": "http://127.0.0.1:13000",
            "DFIP_STORAGE_ENDPOINT": str(archive),
            "DFIP_STORAGE_BUCKET": "dfip-source-files",
            "DFIP_LOG_LEVEL": "WARNING",
        }
    )
    env.pop("DFIP_BOOTSTRAP_TOKEN", None)
    return env


def _production_grade_env(archive: Path | None) -> dict[str, str]:
    env = _forward_operator_secrets(os.environ.copy())
    secret = env.get("DFIP_AUTH_SECRET", "").strip()
    if len(secret) < MIN_JWT_SECRET_LENGTH or secret.lower() in KNOWN_BAD_JWT_SECRETS:
        raise RuntimeError("production-grade rehearsal needs a strong DFIP_AUTH_SECRET.")
    if not env.get("DATABASE_URL", "").strip():
        raise RuntimeError("production-grade rehearsal needs DATABASE_URL.")
    storage = (archive.as_posix() if archive is not None else "") or env.get(
        "DFIP_STORAGE_ENDPOINT", ""
    ).strip()
    if not storage:
        raise RuntimeError("production-grade rehearsal needs DFIP_STORAGE_ENDPOINT.")
    env.update(
        {
            "DFIP_ENV": "production",
            "DFIP_AUTH_MODE": "jwt",
            "DFIP_API_HOST": "127.0.0.1",
            "DFIP_API_PORT": "18080",
            "DFIP_WEB_HOST": "127.0.0.1",
            "DFIP_WEB_PORT": "13000",
            "DFIP_API_BASE_URL": "https://dfip.example.com",
            "DFIP_WEB_ORIGIN": "https://dfip.example.com",
            "DFIP_STORAGE_ENDPOINT": storage,
            "DFIP_STORAGE_BUCKET": env.get("DFIP_STORAGE_BUCKET", "").strip()
            or "dfip-source-files",
            "DFIP_LOG_LEVEL": "INFO",
        }
    )
    env.pop("DFIP_BOOTSTRAP_TOKEN", None)
    env.pop("DFIP_DEV_AUTH_TOKEN", None)
    env.pop("DFIP_DEV_AUTH_CLIENT_ID", None)
    return env


def _terminate(proc: subprocess.Popen[bytes] | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def _maybe_caddy_http_proxy() -> dict[str, object]:
    caddy = shutil.which("caddy")
    if not caddy:
        return {
            "binary": False,
            "http_proxy": False,
            "https_tls": False,
            "note": "caddy is not on PATH. HTTP reverse-proxy and ACME TLS were not executed.",
        }
    work = Path(tempfile.mkdtemp(prefix="dfip-p14-caddy-"))
    caddyfile = work / "Caddyfile"
    caddyfile.write_text(
        "http://127.0.0.1:18081 {\n"
        "\thandle /health {\n"
        "\t\treverse_proxy 127.0.0.1:18080\n"
        "\t}\n"
        "\t@api path /api/v1 /api/v1/*\n"
        "\thandle @api {\n"
        "\t\treverse_proxy 127.0.0.1:18080\n"
        "\t}\n"
        "\thandle {\n"
        "\t\treverse_proxy 127.0.0.1:13000\n"
        "\t}\n"
        "}\n",
        encoding="utf-8",
    )
    proc = subprocess.Popen(
        [caddy, "run", "--config", str(caddyfile), "--adapter", "caddyfile"],
        cwd=str(work),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait("http://127.0.0.1:18081/health")
        status, body = _http_json("http://127.0.0.1:18081/health")
        return {
            "binary": True,
            "http_proxy": status == 200,
            "https_tls": False,
            "proxied_health_application": (body or {}).get("application")
            if isinstance(body, dict)
            else None,
            "note": "Loopback HTTP reverse-proxy only. Not ACME, not a public hostname, not HTTPS.",
        }
    except Exception as exc:
        return {
            "binary": True,
            "http_proxy": False,
            "https_tls": False,
            "note": f"caddy HTTP proxy failed ({type(exc).__name__}). Not TLS.",
        }
    finally:
        _terminate(proc)
        shutil.rmtree(work, ignore_errors=True)


def _run_services(env: dict[str, str], *, production_grade: bool) -> dict[str, object]:
    python = sys.executable
    api = subprocess.Popen(
        [python, "-m", "dfip_api"],
        cwd=str(REPO),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    web = subprocess.Popen(
        [python, "-m", "dfip_web"],
        cwd=str(REPO),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait("http://127.0.0.1:18080/health")
        _wait("http://127.0.0.1:13000/health")
        health_status, api_health = _http_json("http://127.0.0.1:18080/health")
        docs_status, _docs = _http_json("http://127.0.0.1:18080/docs")
        openapi_status, _spec = _http_json("http://127.0.0.1:18080/openapi.json")
        web_docs_status, _web_docs = _http_json("http://127.0.0.1:13000/docs")
        config_status, config = _http_json("http://127.0.0.1:13000/config.json")
        if health_status != 200 or not isinstance(api_health, dict):
            raise RuntimeError("API health failed")
        if api_health.get("application") != "dfip-api":
            raise RuntimeError("API health is not dfip-api")
        if not isinstance(config, dict) or set(config) != {
            "apiBaseUrl",
            "apiPrefix",
            "adminRoles",
        }:
            raise RuntimeError("config.json is not the public key set")
        expected_env = "production" if production_grade else None
        if production_grade:
            if api_health.get("environment") != "production":
                raise RuntimeError("API health environment is not production")
            if docs_status != 404 or openapi_status != 404:
                raise RuntimeError("OpenAPI surface is still exposed")
            if not str(config.get("apiBaseUrl", "")).startswith("https://"):
                raise RuntimeError("production-grade config.json is not an HTTPS origin string")
        marker = Path(env["DFIP_STORAGE_ENDPOINT"]) / "rehearsal-marker"
        marker.write_text("persist", encoding="utf-8")
        api.terminate()
        api.wait(timeout=20)
        api = subprocess.Popen(
            [python, "-m", "dfip_api"],
            cwd=str(REPO),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _wait("http://127.0.0.1:18080/health")
        if marker.read_text(encoding="utf-8") != "persist":
            raise RuntimeError("source archive did not survive restart")
        caddy = _maybe_caddy_http_proxy() if production_grade else {
            "binary": bool(shutil.which("caddy")),
            "http_proxy": False,
            "https_tls": False,
            "note": "default rehearsal does not start Caddy.",
        }
        return {
            "ok": True,
            "mode": "production-grade" if production_grade else "test-http",
            "evidence_class": "production-like rehearsal"
            if production_grade
            else "package-only loopback start",
            "digitalocean": False,
            "systemd": "HOST-DEFINED / not executed on this machine",
            "https_tls": False,
            "caddy": caddy,
            "archive_survived_restart": True,
            "health_environment": api_health.get("environment"),
            "openapi_docs": docs_status,
            "openapi_json": openapi_status,
            "web_docs": web_docs_status,
            "config_api_base_scheme": str(config.get("apiBaseUrl", "")).split(":", 1)[0],
            "loads_repo_dotenv": not production_grade,
            "expected_environment": expected_env,
        }
    finally:
        for proc in (web, api):
            _terminate(proc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Local DFIP start/restart rehearsal. Does not provision DigitalOcean."
    )
    parser.add_argument(
        "--production-grade",
        action="store_true",
        help=(
            "Start with DFIP_ENV=production, JWT, HTTPS origin strings, OpenAPI off, "
            "and no child .env load. Not a Droplet and not TLS."
        ),
    )
    args = parser.parse_args(argv)
    work = Path(tempfile.mkdtemp(prefix="dfip-p13g-"))
    archive = work / "source-storage"
    archive.mkdir()
    try:
        if args.production_grade:
            env = _production_grade_env(archive)
            payload = _run_services(env, production_grade=True)
        else:
            env = _test_env(archive)
            payload = _run_services(env, production_grade=False)
            payload["systemd"] = (
                "deployment artifact generated; host-level execution pending "
                "P13G production rehearsal."
            )
            payload["caddy"] = payload["systemd"]
            payload["digitalocean"] = False
        print(json.dumps(payload))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__, "detail": str(exc)}))
        return 1
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
