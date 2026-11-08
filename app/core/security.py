from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

import jwt
from fastapi import HTTPException, status
from jwt import InvalidTokenError

from app.core.config import get_settings

JWT_ALGORITHM = "HS256"

# Roles issued by Ms_Users (see Ms_Users/db/README.md). "usuario" is the athlete
# role of the original SportMatch database, same as Ms_Users' ATHLETE_ROLES.
CLUB_ADMIN_ROLE = "club_admin"
ATHLETE_ROLES = frozenset({"player", "usuario"})


@dataclass(frozen=True)
class Actor:
    """The account behind a valid access token issued by Ms_Users."""

    id: UUID
    roles: frozenset[str]
    # Kept to call Ms_Users on the athlete's behalf (their own public card).
    token: str = field(default="", repr=False)
    # Verified email (claim "email", lower case). Older tokens do not carry it.
    email: str | None = field(default=None, repr=False)

    @property
    def is_club_admin(self) -> bool:
        return CLUB_ADMIN_ROLE in self.roles

    @property
    def is_athlete(self) -> bool:
        return bool(ATHLETE_ROLES & self.roles)


def authenticate(token: str) -> Actor:
    """Validate an access token the same way Ms_Users does and read its roles."""
    settings = get_settings()
    try:
        payload = jwt.decode(
            token, settings.jwt_secret, algorithms=[JWT_ALGORITHM],
            issuer=settings.jwt_issuer, audience=settings.jwt_audience,
            options={"require": ["sub", "iss", "aud", "iat", "exp", "token_type"]},
        )
        if payload["token_type"] != "access":
            raise InvalidTokenError("Only access tokens are accepted")
        roles = payload.get("roles", [])
        if not isinstance(roles, list) or not all(isinstance(role, str) for role in roles):
            raise InvalidTokenError("roles must be a list of strings")
        return Actor(
            id=UUID(str(payload["sub"])), roles=frozenset(roles), token=token,
            email=_email_claim(payload.get("email")),
        )
    except (InvalidTokenError, KeyError, ValueError) as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or expired access token",
        ) from error


# RFC 5321: an address is at most 254 characters.
EMAIL_MAX_LENGTH = 254


def _email_claim(value: object) -> str | None:
    """The optional email claim, normalized. Anything unexpected is ignored
    (None) instead of rejecting a token that is otherwise valid."""
    if not isinstance(value, str):
        return None
    email = value.strip().lower()
    if (
        not email
        or len(email) > EMAIL_MAX_LENGTH
        or "@" not in email
        or any(char.isspace() for char in email)
    ):
        return None
    return email
