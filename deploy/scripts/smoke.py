#!/usr/bin/env python3
"""P13G smoke checks against a running DFIP origin.

Default: loopback API and web. For public HTTPS, set DFIP_SMOKE_BASE_URL
to https://dfip.example.com (operator domain).

Does not print secrets. Does not use hosted/live/August data.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

API = os.environ.get("DFIP_SMOKE_API_URL", "http://127.0.0.1:8000").rstrip("/")
WEB = os.environ.get("DFIP_SMOKE_WEB_URL", "http://127.0.0.1:3000").rstrip("/")
PUBLIC = os.environ.get("DFIP_SMOKE_BASE_URL", "").rstrip("/")


def _get(url: str) -> tuple[int, bytes]:
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def main() -> int:
    failures: list[str] = []
    health_url = f"{PUBLIC or API}/health"
    status, body = _get(health_url)
    if status != 200:
        failures.append(f"/health HTTP {status}")
    else:
        payload = json.loads(body.decode("utf-8"))
        if payload.get("status") != "ok":
            failures.append("/health status is not ok")
        if payload.get("application") not in {"dfip-api", "dfip-web"}:
            failures.append("/health application unexpected")
        if PUBLIC and payload.get("application") != "dfip-api":
            failures.append("public /health must be dfip-api liveness")

    config_base = PUBLIC or WEB
    status, body = _get(f"{config_base}/config.json")
    if status != 200:
        failures.append(f"/config.json HTTP {status}")
    else:
        payload = json.loads(body.decode("utf-8"))
        keys = set(payload)
        if keys != {"apiBaseUrl", "apiPrefix", "adminRoles"}:
            failures.append("/config.json keys are not the public set")
        if any("secret" in str(value).lower() for value in payload.values()):
            failures.append("/config.json looks like it contains a secret")

    if failures:
        print(json.dumps({"ok": False, "failures": failures}))
        return 1
    print(json.dumps({"ok": True, "health": health_url, "config": f"{config_base}/config.json"}))
    print("Manual remainder: login, companies, upload, process, QA, publish,")
    print("current/historical reports, deactivate, historical recovery, reactivate,")
    print("backup verify, purge disposable company, Company B untouched.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
