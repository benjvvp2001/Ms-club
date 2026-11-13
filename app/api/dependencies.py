from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import get_club_max_admins
from app.core.security import Actor, authenticate
from app.repositories.base import ClubRepository
from app.services.account_photos import AccountPhotos
from app.services.administrador_service import AdministradorService
from app.services.canal_service import CanalService
from app.services.club_service import ClubService
from app.services.comunicado_service import ComunicadoService
from app.services.consulta_service import ConsultaService
from app.services.convocatoria_service import ConvocatoriaService
from app.services.postulacion_service import PostulacionService
from app.services.entitlements_client import EntitlementsClient
from app.services.users_client import AccountNames, AthleteProfiles

bearer_scheme = HTTPBearer(auto_error=False)


def get_repository(request: Request) -> ClubRepository:
    return request.app.state.repository


def get_account_names(request: Request) -> AccountNames | None:
    """Ms_Users for display names. Optional: without it (or if it fails) a
    neutral name is used, so it never blocks a request."""
    users = getattr(request.app.state, "users", None)
    return users if callable(getattr(users, "display_name", None)) else None


def get_entitlements(request: Request) -> EntitlementsClient | None:
    """HU-54: ms-pago, for plan limits and featured clubs. None = no limits (fail-open)."""
    return getattr(request.app.state, "entitlements", None)


def get_club_service(
    repository: ClubRepository = Depends(get_repository),
    names: AccountNames | None = Depends(get_account_names),
    entitlements: EntitlementsClient | None = Depends(get_entitlements),
) -> ClubService:
    return ClubService(repository, names, get_club_max_admins(), entitlements)


def get_administrador_service(
    repository: ClubRepository = Depends(get_repository),
) -> AdministradorService:
    return AdministradorService(repository, get_club_max_admins())


def get_consulta_service(
    repository: ClubRepository = Depends(get_repository),
    names: AccountNames | None = Depends(get_account_names),
) -> ConsultaService:
    return ConsultaService(repository, names)


def get_canal_service(
    repository: ClubRepository = Depends(get_repository),
    entitlements: EntitlementsClient | None = Depends(get_entitlements),
) -> CanalService:
    return CanalService(repository, entitlements=entitlements)


def get_convocatoria_service(
    repository: ClubRepository = Depends(get_repository),
    entitlements: EntitlementsClient | None = Depends(get_entitlements),
) -> ConvocatoriaService:
    return ConvocatoriaService(repository, entitlements)


def get_comunicado_service(
    repository: ClubRepository = Depends(get_repository),
    entitlements: EntitlementsClient | None = Depends(get_entitlements),
) -> ComunicadoService:
    return ComunicadoService(repository, entitlements)


def get_users(request: Request) -> AthleteProfiles:
    return request.app.state.users


def get_photos(request: Request) -> AccountPhotos | None:
    """Ms_Users' current photos. Optional: without it the candidates simply have no photo."""
    return getattr(request.app.state, "photos", None)


def get_postulacion_service(
    repository: ClubRepository = Depends(get_repository),
    users: AthleteProfiles = Depends(get_users),
    photos: AccountPhotos | None = Depends(get_photos),
    entitlements: EntitlementsClient | None = Depends(get_entitlements),
) -> PostulacionService:
    return PostulacionService(repository, users, photos, entitlements)


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
