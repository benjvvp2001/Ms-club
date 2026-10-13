from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import jwt
from fastapi import HTTPException, status
from jwt import InvalidTokenError

from app.core.config import get_settings

JWT_ALGORITHM = "HS256"

# Role issued by Ms_Users to club representatives (see Ms_Users/db/README.md).
CLUB_ADMIN_ROLE = "club_admin"


@dataclass(frozen=True)
class Actor:
    """The account behind a valid access token issued by Ms_Users."""

    id: UUID
    roles: frozenset[str]

    @property
    def is_club_admin(self) -> bool:
        return CLUB_ADMIN_ROLE in self.roles


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
        return Actor(id=UUID(str(payload["sub"])), roles=frozenset(roles))
    except (InvalidTokenError, KeyError, ValueError) as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or expired access token",
        ) from error
