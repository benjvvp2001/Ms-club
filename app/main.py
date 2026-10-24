from __future__ import annotations

import math
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.v1.administradores import router as administradores_router
from app.api.v1.canales import router as canales_router
from app.api.v1.clubs import router as clubs_router
from app.api.v1.comunicados import router as comunicados_router
from app.api.v1.consultas import router as consultas_router
from app.api.v1.convocatorias import router as convocatorias_router
from app.api.v1.pagos import router as pagos_router
from app.api.v1.postulaciones import router as postulaciones_router
from app.core.config import (
    WebpaySettings,
    get_club_max_admins,
    get_database_settings,
    get_settings,
    get_users_service_settings,
    get_webpay_settings,
)
from app.database.session import check_connection, create_pool
from app.repositories.base import ClubRepository
from app.repositories.postgres_club_repository import PostgresClubRepository
from app.services import canal_inactividad
from app.services.users_client import AthleteProfiles, UsersClient
from app.services.webpay import WebpayGateway, build_webpay


def _finite_or_text(value: float):
    """A number such as 1e999 reaches validation as infinity, which JSON cannot carry: the error
    message that echoes the received value would fail to serialize and answer 500 instead of 422."""
    return value if math.isfinite(value) else str(value)


def create_app(
    repository: ClubRepository | None = None,
    users: AthleteProfiles | None = None,
    webpay: WebpayGateway | None = None,
    webpay_settings: WebpaySettings | None = None,
    inactividad: bool | None = None,
) -> FastAPI:
    """Create the service without routes outside the approved clubs prefix.

    With no repository given, the service checks the PostgreSQL connection
    (DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME) and opens the pool on
    server startup, not at import time; it refuses to start if that fails. Tests
    inject a MockClubRepository instead so they don't need a live database, and a
    fake users source so applying never calls Ms_Users. The same goes for the
    Webpay gateway (WEBPAY_* variables): tests inject a MockWebpay.

    The hourly check of inactive channels (G4) runs in a daemon thread while
    the server is up: by default only with the real database and unless
    CANALES_INACTIVIDAD_ENABLED is false; inactividad forces it on or off.
    """
    owns_pool = repository is None
    owns_users = users is None
    owns_webpay = webpay is None
    pool_holder: dict[str, object] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        get_club_max_admins()  # a wrong CLUB_MAX_ADMINS stops the start, not a request
        if owns_pool:
            get_settings()
            settings = get_database_settings()
            check_connection(settings)
            pool = create_pool(settings.conninfo())
            pool_holder["pool"] = pool
            app.state.repository = PostgresClubRepository(pool)
        http_client = None
        if owns_users:
            users_settings = get_users_service_settings()
            http_client = httpx.Client(
                base_url=users_settings.url,
                timeout=users_settings.timeout_seconds,
                trust_env=False,
            )
            app.state.users = UsersClient(http_client)
        if owns_webpay:
            app.state.webpay_settings = get_webpay_settings()
            app.state.webpay = build_webpay(app.state.webpay_settings)
        worker = None
        run_worker = (
            inactividad if inactividad is not None else owns_pool and canal_inactividad.enabled()
        )
        if run_worker:
            worker = canal_inactividad.InactividadWorker(app.state.repository)
            worker.start()
        try:
            yield
        finally:
            if worker is not None:
                worker.stop()
            if owns_webpay and getattr(app.state, "webpay", None) is not None:
                app.state.webpay.close()
            if http_client is not None:
                http_client.close()
            pool = pool_holder.get("pool")
            if pool is not None:
                pool.close()  # type: ignore[attr-defined]

    app = FastAPI(
        title="SportMatch clubs service",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    if repository is not None:
        app.state.repository = repository
    if users is not None:
        app.state.users = users
    if webpay is not None:
        app.state.webpay = webpay
    if webpay_settings is not None:
        app.state.webpay_settings = webpay_settings

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_: Request, error: RequestValidationError) -> JSONResponse:
        # Invalid search or path parameters are a client error: 400, not 422.
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": jsonable_encoder(error.errors(), custom_encoder={float: _finite_or_text})},
        )

    @app.get("/api/v1/clubs/health/ready", tags=["health"])
    def ready() -> dict[str, str]:
        pool = pool_holder.get("pool")
        if pool is not None:
            try:
                with pool.connection(timeout=2) as conn:  # type: ignore[attr-defined]
                    conn.execute("SELECT 1 FROM club LIMIT 1")
            except Exception as error:
                raise HTTPException(status_code=503, detail="clubs database unavailable") from error
        return {"status": "ready", "service": "ms_club"}

    # Convocatorias, postulaciones, comunicados, consultas, canales and administradores
    # first: their fixed paths (/announcements, /notices/me, /consultas/me, /canales/me) must not be
    # read as the {club_id} of the clubs routes.
    app.include_router(postulaciones_router)
    app.include_router(comunicados_router)
    app.include_router(consultas_router)
    app.include_router(canales_router)
    app.include_router(administradores_router)
    app.include_router(pagos_router)
    app.include_router(convocatorias_router)
    app.include_router(clubs_router)

    return app


app = create_app()
