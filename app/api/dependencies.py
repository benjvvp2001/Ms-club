from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import get_club_max_admins
from app.core.security import Actor, authenticate
from app.repositories.base import ClubRepository
from app.services.administrador_service import AdministradorService
from app.services.canal_service import CanalService
from app.services.club_service import ClubService
from app.services.comunicado_service import ComunicadoService
from app.services.consulta_service import ConsultaService
from app.services.convocatoria_service import ConvocatoriaService
from app.services.postulacion_service import PostulacionService
from app.services.users_client import AccountNames, AthleteProfiles

bearer_scheme = HTTPBearer(auto_error=False)


def get_repository(request: Request) -> ClubRepository:
    return request.app.state.repository


def get_display_names(request: Request) -> AccountNames | None:
    """Ms_Users for display names. Optional: without it (or if it fails) a
    neutral name is used, so it never blocks a request."""
    users = getattr(request.app.state, "users", None)
    return users if callable(getattr(users, "display_name", None)) else None


def get_club_service(
    repository: ClubRepository = Depends(get_repository),
    names: AccountNames | None = Depends(get_display_names),
) -> ClubService:
    return ClubService(repository, names, get_club_max_admins())


def get_administrador_service(
    repository: ClubRepository = Depends(get_repository),
) -> AdministradorService:
    return AdministradorService(repository, get_club_max_admins())


def get_consulta_service(
    repository: ClubRepository = Depends(get_repository),
    names: AccountNames | None = Depends(get_display_names),
) -> ConsultaService:
    return ConsultaService(repository, names)


def get_canal_service(repository: ClubRepository = Depends(get_repository)) -> CanalService:
    return CanalService(repository)


def get_convocatoria_service(
    repository: ClubRepository = Depends(get_repository),
) -> ConvocatoriaService:
    return ConvocatoriaService(repository)


def get_comunicado_service(
    repository: ClubRepository = Depends(get_repository),
) -> ComunicadoService:
    return ComunicadoService(repository)


def get_users(request: Request) -> AthleteProfiles:
    return request.app.state.users


def get_postulacion_service(
    repository: ClubRepository = Depends(get_repository),
    users: AthleteProfiles = Depends(get_users),
) -> PostulacionService:
    return PostulacionService(repository, users)


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


def get_athlete(actor: Actor = Depends(get_current_actor)) -> Actor:
    """Guard of the postulación routes: a valid token with an athlete role."""
    if not actor.is_athlete:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo los deportistas pueden postular a convocatorias.",
        )
    return actor


def get_consulta_athlete(actor: Actor = Depends(get_current_actor)) -> Actor:
    """Guard of the athlete's consulta routes: a valid token with an athlete role."""
    if not actor.is_athlete:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo los deportistas pueden escribir consultas a un club.",
        )
    return actor


def get_canal_athlete(actor: Actor = Depends(get_current_actor)) -> Actor:
    """Guard of the athlete's channel routes: a valid token with an athlete role."""
    if not actor.is_athlete:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo los deportistas pueden usar los canales de un club como miembros.",
        )
    return actor
