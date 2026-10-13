from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.v1.clubs import router
from app.core.config import get_database_settings, get_settings
from app.database.session import check_connection, create_pool
from app.repositories.base import ClubRepository
from app.repositories.postgres_club_repository import PostgresClubRepository
from app.services.convocatorias import ConvocatoriasProvider, NoConvocatorias


def create_app(
    repository: ClubRepository | None = None,
    convocatorias: ConvocatoriasProvider | None = None,
) -> FastAPI:
    """Create the service without routes outside the approved clubs prefix.

    With no repository given, the service checks the PostgreSQL connection
    (DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME) and opens the pool on
    server startup, not at import time; it refuses to start if that fails. Tests
    inject a MockClubRepository instead so they don't need a live database.
    """
    owns_pool = repository is None
    pool_holder: dict[str, object] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if owns_pool:
            get_settings()
            settings = get_database_settings()
            check_connection(settings)
            pool = create_pool(settings.conninfo())
            pool_holder["pool"] = pool
            app.state.repository = PostgresClubRepository(pool)
        try:
            yield
        finally:
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
    app.state.convocatorias = convocatorias or NoConvocatorias()

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_: Request, error: RequestValidationError) -> JSONResponse:
        # Invalid search or path parameters are a client error: 400, not 422.
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": jsonable_encoder(error.errors())},
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

    app.include_router(router)

    return app


app = create_app()
