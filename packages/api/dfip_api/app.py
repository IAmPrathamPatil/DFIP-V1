"""FastAPI application factory for the P5 API plus P7 publication routes."""

from __future__ import annotations

import atexit
import logging
import os
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

from dfip_config.catalog import CatalogStore, InMemoryCatalogStore
from dfip_config.settings import BOOTSTRAP_TOKEN_HEADER, Settings, load_settings
from dfip_core.ingest.ports import IngestStore
from dfip_core.ingest.store import InMemoryIngestStore
from dfip_core.transform.ports import FactStore
from dfip_core.transform.store import InMemoryFactStore
from dfip_db.analytics_repository import PostgresAnalyticsRepository
from dfip_db.catalog_store import PostgresCatalogStore
from dfip_db.connection import (
    ALLOWED_SSL_MODES,
    DatabaseUnavailableError,
    close_pool,
    create_pool,
    dsn_is_local_host,
    dsn_sslmode,
)
from dfip_db.fact_store import PostgresFactStore
from dfip_db.ingest_store import PostgresIngestStore
from dfip_db.publication_store import PostgresPublicationStore
from dfip_db.read_repository import PostgresReadRepository
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from dfip_api.analytics_routes import analytics_router
from dfip_api.ask_llm import build_ask_llm
from dfip_api.auth import validate_auth_settings
from dfip_api.auth_routes import auth_public_router, auth_session_router
from dfip_api.catalog_routes import catalog_router
from dfip_api.catalog_service import CatalogService
from dfip_api.client_directory import (
    ClientDirectory,
    InMemoryClientDirectory,
    PostgresClientDirectory,
    seed_from_identity,
)
from dfip_api.client_routes import client_router
from dfip_api.errors import (
    PERSISTENCE_UNAVAILABLE,
    ApiError,
    AuthConfigurationError,
    PersistenceConfigurationError,
    api_error_handler,
    error_response,
    http_exception_handler,
    safe_route,
    unexpected_error_handler,
    validation_error_handler,
)
from dfip_api.excel_grant import InMemoryExcelGrantStore, PostgresExcelGrantStore
from dfip_api.identity_store import (
    IdentityStore,
    InMemoryIdentityStore,
    PostgresIdentityStore,
)
from dfip_api.limits import AttemptLimiter, JsonBodyLimitMiddleware
from dfip_api.ports import PublicationStore, ReadRepository
from dfip_api.publication_routes import publication_router
from dfip_api.publication_service import PublicationService
from dfip_api.publication_store import InMemoryPublicationStore
from dfip_api.qa_store import InMemoryQaFindingStore, QaFindingStore
from dfip_api.recovery import fail_abandoned_processing_runs
from dfip_api.repository import InMemoryReadRepository
from dfip_api.routes import ops_router, router
from dfip_api.saved_analysis import InMemorySavedAnalysisStore, PostgresSavedAnalysisStore
from dfip_api.schemas import ErrorResponse, HealthDatabase, HealthResponse
from dfip_api.service import ReadService
from dfip_api.source_storage import SourceObjectStore, build_source_object_store
from dfip_api.upload_routes import upload_router
from dfip_api.upload_service import UploadService

log = logging.getLogger(__name__)


def validate_persistence_settings(settings: Settings) -> None:
    """Production-grade environments must have DATABASE_URL and remote TLS."""
    if not settings.is_production_grade:
        return
    dsn = settings.database_url.strip()
    if not dsn:
        raise PersistenceConfigurationError("Production requires DATABASE_URL.")
    if dsn_is_local_host(dsn):
        return
    mode = dsn_sslmode(dsn)
    if mode not in ALLOWED_SSL_MODES:
        raise PersistenceConfigurationError(
            "Production DATABASE_URL must use TLS for remote database hosts."
        )


def validate_storage_settings(settings: Settings) -> None:
    """Production-grade environments must use a local filesystem source archive."""
    if not settings.is_production_grade:
        return
    endpoint = settings.dfip_storage_endpoint.strip()
    if not endpoint:
        raise PersistenceConfigurationError("Production requires DFIP_STORAGE_ENDPOINT.")
    lowered = endpoint.lower()
    if lowered.startswith("http://") or lowered.startswith("https://"):
        raise PersistenceConfigurationError(
            "Production DFIP_STORAGE_ENDPOINT must be a private local directory."
        )


def validate_origin_settings(settings: Settings) -> None:
    """Production-grade environments require HTTPS web and API origins."""
    if not settings.is_production_grade:
        return
    origin = settings.dfip_web_origin.strip()
    api_base = settings.dfip_api_base_url.strip()
    if not origin or not origin.lower().startswith("https://"):
        raise AuthConfigurationError("Production requires HTTPS DFIP_WEB_ORIGIN.")
    if not api_base or not api_base.lower().startswith("https://"):
        raise AuthConfigurationError("Production requires HTTPS DFIP_API_BASE_URL.")


async def database_unavailable_handler(
    request: Request, exc: DatabaseUnavailableError
) -> JSONResponse:
    log.error(
        "persistence-unavailable type=%s route=%s",
        type(exc).__name__,
        safe_route(request),
    )
    return error_response(503, PERSISTENCE_UNAVAILABLE, "Persistence is unavailable.")


def create_app(
    settings: Settings | None = None,
    ingest_store: IngestStore | None = None,
    fact_store: FactStore | None = None,
    publication_store: PublicationStore | None = None,
    repository: ReadRepository | None = None,
    catalog_store: CatalogStore | None = None,
    qa_store: QaFindingStore | None = None,
    source_store: SourceObjectStore | None = None,
    identity_store: IdentityStore | None = None,
    client_directory: ClientDirectory | None = None,
) -> FastAPI:
    """Build the API. Empty DATABASE_URL keeps the V1 in-memory stores.

    Injected stores always win so existing tests stay on memory even when a
    placeholder DATABASE_URL is present on settings. The pool is lazy and
    health never probes PostgreSQL.
    """
    resolved_settings = settings if settings is not None else load_settings()
    validate_auth_settings(resolved_settings)
    validate_persistence_settings(resolved_settings)
    validate_storage_settings(resolved_settings)
    validate_origin_settings(resolved_settings)

    injected = any(
        item is not None
        for item in (ingest_store, fact_store, publication_store, repository, catalog_store)
    )
    db_pool = None
    if injected:
        resolved_ingest = ingest_store if ingest_store is not None else InMemoryIngestStore()
        resolved_facts = fact_store if fact_store is not None else InMemoryFactStore()
        resolved_publications = (
            publication_store if publication_store is not None else InMemoryPublicationStore()
        )
        resolved_catalog = catalog_store if catalog_store is not None else InMemoryCatalogStore()
        resolved_qa = qa_store if qa_store is not None else InMemoryQaFindingStore()
        if repository is not None:
            resolved_repository = repository
        else:
            resolved_repository = InMemoryReadRepository(
                resolved_ingest,  # type: ignore[arg-type]
                resolved_facts,  # type: ignore[arg-type]
            )
    elif resolved_settings.database_url.strip():
        db_pool = create_pool(resolved_settings.database_url)
        resolved_ingest = PostgresIngestStore(db_pool)
        resolved_facts = PostgresFactStore(db_pool)
        resolved_publications = PostgresPublicationStore(
            db_pool,
            use_history_serving_table=resolved_settings.dfip_history_serving_table,
        )
        resolved_repository = PostgresReadRepository(db_pool)
        resolved_catalog = PostgresCatalogStore(db_pool)
        resolved_qa = qa_store if qa_store is not None else PostgresAnalyticsRepository(db_pool)
    else:
        resolved_ingest = InMemoryIngestStore()
        resolved_facts = InMemoryFactStore()
        resolved_publications = InMemoryPublicationStore()
        resolved_repository = InMemoryReadRepository(resolved_ingest, resolved_facts)
        resolved_catalog = InMemoryCatalogStore()
        resolved_qa = qa_store if qa_store is not None else InMemoryQaFindingStore()

    service = ReadService(resolved_repository)
    worker_count = max(1, min(int(resolved_settings.dfip_worker_concurrency or 1), 4))
    upload_executor = ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="dfip-upload")
    atexit.register(upload_executor.shutdown, wait=False, cancel_futures=True)
    resolved_source = (
        source_store if source_store is not None else build_source_object_store(resolved_settings)
    )

    if identity_store is not None:
        resolved_identity = identity_store
    elif db_pool is not None:
        resolved_identity = PostgresIdentityStore(db_pool)
    else:
        resolved_identity = InMemoryIdentityStore()

    if db_pool is not None:
        resolved_excel_grants = PostgresExcelGrantStore(db_pool)
        resolved_saved_analyses = PostgresSavedAnalysisStore(db_pool)
    else:
        resolved_excel_grants = InMemoryExcelGrantStore()
        resolved_saved_analyses = InMemorySavedAnalysisStore()

    if client_directory is not None:
        resolved_clients = client_directory
    elif db_pool is not None:
        resolved_clients = PostgresClientDirectory(db_pool)
    else:
        resolved_clients = InMemoryClientDirectory()
    if isinstance(resolved_identity, InMemoryIdentityStore) and isinstance(
        resolved_clients, InMemoryClientDirectory
    ):
        seed_from_identity(resolved_clients, resolved_identity)

    upload_service = UploadService(
        resolved_ingest,
        resolved_facts,
        resolved_settings,
        resolved_catalog,
        resolved_qa,
        executor=upload_executor,
        source_store=resolved_source,
        client_directory=resolved_clients,
        publication_store=resolved_publications,
    )
    catalog_service = CatalogService(
        resolved_catalog, resolved_settings, client_directory=resolved_clients
    )

    if db_pool is not None:
        fail_abandoned_processing_runs(resolved_ingest)

    publication_service = PublicationService(
        resolved_ingest,
        resolved_facts,
        resolved_publications,
        resolved_clients,
    )

    prefix = resolved_settings.dfip_api_prefix.strip() or "/api/v1"
    if not prefix.startswith("/"):
        prefix = f"/{prefix}"
    prefix = prefix.rstrip("/")

    @asynccontextmanager
    async def lifespan(_application: FastAPI) -> AsyncIterator[None]:
        if db_pool is not None:
            fail_abandoned_processing_runs(resolved_ingest)
            if os.environ.get("PYTEST_CURRENT_TEST") is None:
                upload_service.resume_orphaned_work()
        yield
        upload_executor.shutdown(wait=True, cancel_futures=False)
        close_pool(db_pool)

    production_grade = resolved_settings.is_production_grade
    application = FastAPI(
        title="DFIP API",
        version="0.5.0",
        description=(
            "P5 working-set API plus P7 publication routes. Resource routes "
            f"live under {prefix} and require a Bearer credential. GET /health "
            "is public and does not probe a database. GET /facts is the "
            "unfiltered working set for admin/publisher. Persistence is "
            "in-memory unless a database connection string is configured. "
            "Authorization is application-level; PostgreSQL RLS is defense in depth."
        ),
        docs_url=None if production_grade else "/docs",
        redoc_url=None if production_grade else "/redoc",
        openapi_url=None if production_grade else "/openapi.json",
        lifespan=lifespan,
    )
    application.state.settings = resolved_settings
    application.state.ingest_store = resolved_ingest
    application.state.fact_store = resolved_facts
    application.state.publication_store = resolved_publications
    application.state.repository = resolved_repository
    application.state.service = service
    application.state.publication_service = publication_service
    application.state.upload_service = upload_service
    application.state.source_store = resolved_source
    application.state.upload_executor = upload_executor
    application.state.catalog_service = catalog_service
    application.state.catalog_store = resolved_catalog
    application.state.qa_store = resolved_qa
    application.state.db_pool = db_pool
    application.state.identity_store = resolved_identity
    application.state.excel_grant_store = resolved_excel_grants
    application.state.saved_analysis_store = resolved_saved_analyses
    application.state.client_directory = resolved_clients
    application.state.attempt_limiter = AttemptLimiter()
    application.state.ask_llm = build_ask_llm(resolved_settings)

    application.add_middleware(
        JsonBodyLimitMiddleware,
        max_bytes=resolved_settings.dfip_json_max_body_bytes,
    )
    origin = resolved_settings.dfip_web_origin.strip()
    if origin:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=[origin],
            allow_credentials=False,
            allow_methods=["GET", "HEAD", "OPTIONS", "POST", "DELETE"],
            allow_headers=["Authorization", "Content-Type", BOOTSTRAP_TOKEN_HEADER],
            expose_headers=["Content-Disposition"],
        )

    application.add_exception_handler(ApiError, api_error_handler)
    application.add_exception_handler(DatabaseUnavailableError, database_unavailable_handler)
    application.add_exception_handler(RequestValidationError, validation_error_handler)
    application.add_exception_handler(StarletteHTTPException, http_exception_handler)
    application.add_exception_handler(Exception, unexpected_error_handler)

    @application.get(
        "/health",
        response_model=HealthResponse,
        responses={500: {"model": ErrorResponse}},
        summary="Application health",
        description=(
            "Public liveness probe. `database.configured` is true only when "
            "a database connection string is configured. `database.status` is "
            "always `not_checked` because health does not open a database connection."
        ),
        tags=["Health"],
    )
    async def health() -> HealthResponse:
        configured = bool(resolved_settings.database_url.strip())
        return HealthResponse(
            status="ok",
            application="dfip-api",
            environment=resolved_settings.dfip_env,
            database=HealthDatabase(configured=configured, status="not_checked"),
        )

    application.include_router(auth_public_router, prefix=prefix)
    application.include_router(auth_session_router, prefix=prefix)
    application.include_router(ops_router, prefix=prefix)
    application.include_router(router, prefix=prefix)
    application.include_router(client_router, prefix=prefix)
    application.include_router(publication_router, prefix=prefix)
    application.include_router(analytics_router, prefix=prefix)
    application.include_router(upload_router, prefix=prefix)
    application.include_router(catalog_router, prefix=prefix)
    return application


def __getattr__(name: str):
    """Build the default ASGI app on first access (uvicorn ``dfip_api.app:app``).

    Importing ``create_app`` for tests must not require the operator ``.env``.
    Starting the process still validates ``dev_token`` + ``DATABASE_URL``.
    """
    if name == "app":
        return create_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
