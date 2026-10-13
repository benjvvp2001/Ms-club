from __future__ import annotations

import logging

import psycopg
from psycopg_pool import ConnectionPool

from app.core.config import DatabaseSettings

logger = logging.getLogger("uvicorn.error")


def check_connection(settings: DatabaseSettings) -> None:
    """Fail fast with the real cause (wrong password, missing database...)
    instead of the pool's generic timeout, and confirm the target database."""
    target = f"{settings.user}@{settings.host}:{settings.port}/{settings.name}"
    try:
        with psycopg.connect(settings.conninfo(), connect_timeout=5) as conn:
            database, server_version = conn.execute(
                "SELECT current_database(), current_setting('server_version')"
            ).fetchone()
    except psycopg.Error as error:
        raise RuntimeError(f"Could not connect to PostgreSQL {target}: {error}") from error
    if database != settings.name:
        raise RuntimeError(f"Connected to database {database!r}, expected {settings.name!r}")
    logger.info("Connected to PostgreSQL %s (server %s)", target, server_version)


def create_pool(conninfo: str) -> ConnectionPool:
    pool = ConnectionPool(
        conninfo=conninfo,
        min_size=1,
        max_size=10,
        open=False,
    )
    pool.open(wait=True)
    return pool
