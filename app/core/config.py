from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

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


# Public test credentials of Transbank's integration environment (published in
# its developer documentation and bundled with its SDKs). They charge nothing.
_INTEGRATION_COMMERCE_CODE = "597055555532"
_INTEGRATION_API_KEY = "579B532A7440BB0C9079DED94D31EA1615BACEB56610332264630D42D0A36B1C"
_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*://\S+$", re.IGNORECASE)


@dataclass(frozen=True)
class WebpaySettings:
    # True: no call to Transbank; payments are simulated with a page served by
    # this service. Never allowed in PRODUCTION.
    mock_mode: bool
    environment: str  # INTEGRATION | PRODUCTION
    commerce_code: str | None
    api_key: str | None = field(default=None, repr=False)
    # Base URL of this service as the user's browser reaches it (through the
    # gateway). Transbank sends the browser back to {public_url}/api/v1/payments/webpay/return.
    public_url: str = ""
    # Where that return endpoint finally sends the user: a page of the web app
    # or a deep link of the mobile app. It receives the token as query params.
    frontend_return_url: str = ""
    timeout_seconds: float = 15.0

    @property
    def return_url(self) -> str:
        return f"{self.public_url}/api/v1/payments/webpay/return"


def _truthy(name: str) -> bool:
    return os.getenv(name, "false").strip().lower() in {"1", "true", "yes", "on"}


def get_webpay_settings() -> WebpaySettings:
    """Read the Webpay Plus configuration. WEBPAY_MOCK_MODE=true simulates the
    gateway for development; the default is false so a missing variable never
    turns payments into free approvals."""
    mock_mode = _truthy("WEBPAY_MOCK_MODE")
    environment = os.getenv("WEBPAY_ENVIRONMENT", "INTEGRATION").strip().upper()
    if environment not in {"INTEGRATION", "PRODUCTION"}:
        raise RuntimeError("WEBPAY_ENVIRONMENT must be INTEGRATION or PRODUCTION")
    if mock_mode and environment == "PRODUCTION":
        raise RuntimeError("WEBPAY_MOCK_MODE cannot be true with WEBPAY_ENVIRONMENT=PRODUCTION")

    commerce_code = os.getenv("WEBPAY_COMMERCE_CODE", "").strip() or None
    api_key = os.getenv("WEBPAY_API_KEY", "").strip() or None
    if not mock_mode:
        if environment == "PRODUCTION":
            if not commerce_code or not api_key:
                raise RuntimeError(
                    "WEBPAY_COMMERCE_CODE and WEBPAY_API_KEY must be configured for PRODUCTION"
                )
        else:
            commerce_code = commerce_code or _INTEGRATION_COMMERCE_CODE
            api_key = api_key or _INTEGRATION_API_KEY

    public_url = os.getenv("WEBPAY_PUBLIC_URL", "").strip().rstrip("/")
    if not public_url.startswith(("http://", "https://")):
        raise RuntimeError("WEBPAY_PUBLIC_URL must be the http(s) URL of this service")
    if environment == "PRODUCTION" and not public_url.startswith("https://"):
        raise RuntimeError("WEBPAY_PUBLIC_URL must be https in PRODUCTION")

    frontend_return_url = os.getenv("WEBPAY_FRONTEND_RETURN_URL", "").strip()
    if not _SCHEME.match(frontend_return_url) or frontend_return_url.lower().startswith(
        ("javascript:", "data:")
    ):
        raise RuntimeError(
            "WEBPAY_FRONTEND_RETURN_URL must be a page or deep link such as "
            "https://app.example/pago/resultado or sportmatch://pago"
        )

    raw_timeout = os.getenv("WEBPAY_TIMEOUT_SECONDS", "15")
    try:
        timeout = float(raw_timeout)
    except ValueError as error:
        raise RuntimeError("WEBPAY_TIMEOUT_SECONDS must be a number") from error
    if timeout <= 0:
        raise RuntimeError("WEBPAY_TIMEOUT_SECONDS must be greater than zero")

    return WebpaySettings(
        mock_mode=mock_mode,
        environment=environment,
        commerce_code=commerce_code,
        api_key=api_key,
        public_url=public_url,
        frontend_return_url=frontend_return_url,
        timeout_seconds=timeout,
    )
