"""P6 static origin server. Serves the SPA; does not proxy or reimplement the API."""

from __future__ import annotations

from pathlib import Path

from dfip_api.errors import AuthConfigurationError
from dfip_config.settings import Settings, load_settings
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from starlette.responses import Response

from dfip_web.paths import static_root
from dfip_web.roles import ADMIN_ROLES


def _require_production_https_origins(settings: Settings) -> None:
    if not settings.is_production_grade:
        return
    origin = settings.dfip_web_origin.strip()
    api_base = settings.dfip_api_base_url.strip()
    if not origin.lower().startswith("https://"):
        raise AuthConfigurationError("Production requires HTTPS DFIP_WEB_ORIGIN.")
    if not api_base.lower().startswith("https://"):
        raise AuthConfigurationError("Production requires HTTPS DFIP_API_BASE_URL.")


def create_web_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings if settings is not None else load_settings()
    _require_production_https_origins(resolved)
    assets = static_root().resolve()
    if not (assets / "index.html").is_file():
        raise FileNotFoundError(f"P6 static SPA is missing: {assets / 'index.html'}")

    api_base = resolved.dfip_api_base_url.strip().rstrip("/") or "http://127.0.0.1:8000"
    prefix = resolved.dfip_api_prefix.strip() or "/api/v1"
    if not prefix.startswith("/"):
        prefix = f"/{prefix}"
    prefix = prefix.rstrip("/")

    application = FastAPI(
        title="DFIP Web",
        version="0.6.0",
        description=(
            "P6 Admin/Publisher and Client SPA. The browser calls the P5 API at "
            f"{api_base}{prefix}. This process does not open a database and is "
            "not an identity provider."
        ),
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    application.state.settings = resolved
    application.state.static_root = assets

    @application.get("/config.json", include_in_schema=False)
    def web_config() -> JSONResponse:
        return JSONResponse(
            {
                "apiBaseUrl": api_base,
                "apiPrefix": prefix,
                "adminRoles": sorted(ADMIN_ROLES),
                "uploadMaxBytes": int(resolved.dfip_upload_max_bytes),
                "uploadMaxFiles": int(resolved.dfip_upload_max_files),
                "uploadMaxTotalBytes": int(resolved.dfip_upload_max_total_bytes),
            }
        )

    @application.get("/health", include_in_schema=False)
    def web_health() -> JSONResponse:
        return JSONResponse({"status": "ok", "application": "dfip-web"})

    @application.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return _html(assets / "index.html")

    @application.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> Response:
        safe = _safe_static_file(assets, path)
        if safe is not None:
            return FileResponse(safe, headers={"Cache-Control": "no-store"})
        return _html(assets / "index.html")

    return application


def _html(path: Path) -> FileResponse:
    return FileResponse(
        path,
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


def _safe_static_file(root: Path, path: str) -> Path | None:
    if not path or path.endswith("/"):
        return None
    candidate = (root / path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    if candidate.is_file():
        return candidate
    return None


app = create_web_app()
