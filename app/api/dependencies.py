from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import WebpaySettings
from app.core.security import Actor, authenticate
from app.repositories.base import ClubRepository
from app.services.club_service import ClubService
from app.services.comunicado_service import ComunicadoService
from app.services.convocatoria_service import ConvocatoriaService
from app.services.pago_service import PagoService
from app.services.postulacion_service import PostulacionService
from app.services.users_client import AthleteProfiles
from app.services.webpay import MockWebpay, WebpayGateway

bearer_scheme = HTTPBearer(auto_error=False)


def get_repository(request: Request) -> ClubRepository:
    return request.app.state.repository


def get_club_service(repository: ClubRepository = Depends(get_repository)) -> ClubService:
    return ClubService(repository)


def get_convocatoria_service(
    repository: ClubRepository = Depends(get_repository),
) -> ConvocatoriaService:
    return ConvocatoriaService(repository)


def get_comunicado_service(
    repository: ClubRepository = Depends(get_repository),
) -> ComunicadoService:
    return ComunicadoService(repository)


def _payments_unavailable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Los pagos no están disponibles."
    )


def get_webpay_settings_dep(request: Request) -> WebpaySettings:
    settings = getattr(request.app.state, "webpay_settings", None)
    if settings is None:
        raise _payments_unavailable()
    return settings


def get_pago_service(
    request: Request,
    repository: ClubRepository = Depends(get_repository),
    settings: WebpaySettings = Depends(get_webpay_settings_dep),
) -> PagoService:
    gateway: WebpayGateway | None = getattr(request.app.state, "webpay", None)
    if gateway is None:
        raise _payments_unavailable()
    return PagoService(repository, gateway, settings)


def get_mock_webpay(request: Request) -> MockWebpay:
    """The simulated gateway; the mock pages do not exist when it is off."""
    gateway = getattr(request.app.state, "webpay", None)
    if not isinstance(gateway, MockWebpay):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    return gateway


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
