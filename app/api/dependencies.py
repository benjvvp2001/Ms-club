from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.security import Actor, authenticate
from app.repositories.base import ClubRepository
from app.services.club_service import ClubService
from app.services.convocatorias import ConvocatoriasProvider

bearer_scheme = HTTPBearer(auto_error=False)


def get_repository(request: Request) -> ClubRepository:
    return request.app.state.repository


def get_convocatorias(request: Request) -> ConvocatoriasProvider:
    return request.app.state.convocatorias


def get_club_service(
    repository: ClubRepository = Depends(get_repository),
    convocatorias: ConvocatoriasProvider = Depends(get_convocatorias),
) -> ClubService:
    return ClubService(repository, convocatorias)


def get_current_actor(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> Actor:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication required",
        )
    return authenticate(credentials.credentials)


def get_club_admin(actor: Actor = Depends(get_current_actor)) -> Actor:
    """Guard of every club management route: a valid token with the club_admin
    role. Ownership of the specific club is checked by ClubService."""
    if not actor.is_club_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo un representante de club puede administrar clubes.",
        )
    return actor
