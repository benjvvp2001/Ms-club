"""Short-lived browser bridge: a signed destination, never an arbitrary redirect."""
from datetime import datetime, timedelta, timezone

import jwt

from app.core.config import get_settings

AUDIENCE = "sportmatch-webpay-checkout"


def sign_checkout(token: str, url: str) -> str:
    return jwt.encode(
        {"token_ws": token, "url": url, "aud": AUDIENCE,
         "exp": datetime.now(timezone.utc) + timedelta(minutes=15)},
        get_settings().jwt_secret, algorithm="HS256",
    )


def read_checkout(ticket: str) -> dict:
    return jwt.decode(
        ticket, get_settings().jwt_secret, algorithms=["HS256"], audience=AUDIENCE,
        options={"require": ["exp", "aud", "token_ws", "url"]},
    )
