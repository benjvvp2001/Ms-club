from __future__ import annotations

import os
from dataclasses import dataclass

from psycopg.conninfo import make_conninfo


@dataclass(frozen=True)
class DatabaseSettings:
    host: str
    port: int
    user: str
    password: str
    name: str

    def conninfo(self) -> str:
        """psycopg connection string; quotes the password, whatever it contains."""
        return make_conninfo(
            host=self.host,
            port=self.port,
            user=self.user,
            password=self.password,
            dbname=self.name,
            application_name="ms_club",
        )


@dataclass(frozen=True)
class Settings:
    jwt_secret: str
    jwt_issuer: str
    jwt_audience: str


def get_settings() -> Settings:
    """JWT validation settings; they must match the ones Ms_Users signs tokens with."""
    jwt_secret = os.getenv("JWT_SECRET")
    if not jwt_secret:
        raise RuntimeError("JWT_SECRET must be configured before starting the service")
    if len(jwt_secret.encode("utf-8")) < 32:
        raise RuntimeError("JWT_SECRET must contain at least 32 bytes")
    return Settings(
        jwt_secret=jwt_secret,
        jwt_issuer=os.getenv("JWT_ISSUER", "sportmatch-auth"),
        jwt_audience=os.getenv("JWT_AUDIENCE", "sportmatch-mobile"),
    )


@dataclass(frozen=True)
class UsersServiceSettings:
    url: str
    timeout_seconds: float


def get_users_service_settings() -> UsersServiceSettings:
    """Where Ms_Users answers; used to attach the athlete's sports profile (HU-33)."""
    raw_timeout = os.getenv("USERS_TIMEOUT_SECONDS", "10")
    try:
        timeout = float(raw_timeout)
    except ValueError as error:
        raise RuntimeError("USERS_TIMEOUT_SECONDS must be a number") from error
    if timeout <= 0:
        raise RuntimeError("USERS_TIMEOUT_SECONDS must be greater than zero")
    url = os.getenv("USERS_SERVICE_URL", "http://127.0.0.1:8001").strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        raise RuntimeError("USERS_SERVICE_URL must be an http(s) URL")
    return UsersServiceSettings(url=url, timeout_seconds=timeout)


CLUB_MAX_ADMINS_DEFAULT = 10


def get_club_max_admins() -> int:
    """How many administrators a club may have, counting pending assignments.
    The product asks for at least 3."""
    raw = os.getenv("CLUB_MAX_ADMINS", str(CLUB_MAX_ADMINS_DEFAULT)).strip()
    try:
        value = int(raw or CLUB_MAX_ADMINS_DEFAULT)
    except ValueError as error:
        raise RuntimeError("CLUB_MAX_ADMINS must be an integer") from error
    if not 3 <= value <= 100:
        raise RuntimeError("CLUB_MAX_ADMINS must be between 3 and 100")
    return value


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} must be configured before starting the service")
    return value


def get_database_settings() -> DatabaseSettings:
    """Read the PostgreSQL connection from DB_* variables without exposing secrets in source."""
    raw_port = os.getenv("DB_PORT", "5432")
    try:
        port = int(raw_port)
    except ValueError as error:
        raise RuntimeError("DB_PORT must be an integer") from error
    if not 0 < port < 65536:
        raise RuntimeError("DB_PORT must be between 1 and 65535")

    return DatabaseSettings(
        host=os.getenv("DB_HOST", "localhost").strip() or "localhost",
        port=port,
        user=_required("DB_USER"),
        password=_required("DB_PASSWORD"),
        name=os.getenv("DB_NAME", "sportmatch_clubs").strip() or "sportmatch_clubs",
    )


def get_database_url() -> str:
    return get_database_settings().conninfo()
