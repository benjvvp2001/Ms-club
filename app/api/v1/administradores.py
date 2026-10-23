from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Response, status

from app.api.dependencies import get_administrador_service, get_club_admin
from app.core.security import Actor
from app.schemas.administrador import (
    AdminRead,
    InvitacionAceptar,
    InvitacionCreada,
    InvitacionRead,
)
from app.schemas.club import ClubManaged
from app.services.administrador_service import AdministradorService

# Several administrators per club (sprint clubes y chats, sección A). Every route
# requires a club_admin token; the service checks the account administers the club.
# The fixed path /admins/invitations/accept is registered before the club routes.
router = APIRouter(prefix="/api/v1/clubs", tags=["administradores"])


@router.post(
    "/admins/invitations/accept",
    response_model=ClubManaged,
    summary="Unirme a un club con un código de invitación",
    description="Solo una cuenta club_admin que aún no administra un club. 404 con el mismo "
    "mensaje si el código no existe, venció, ya se usó o fue revocado.",
)
def accept_invitation(
    payload: InvitacionAceptar,
    actor: Actor = Depends(get_club_admin),
    service: AdministradorService = Depends(get_administrador_service),
) -> ClubManaged:
    return service.accept_invitacion(actor, payload.codigo)


@router.get(
    "/{club_id}/admins",
    response_model=list[AdminRead],
    summary="Administradores del club",
)
def list_admins(
    club_id: UUID,
    actor: Actor = Depends(get_club_admin),
    service: AdministradorService = Depends(get_administrador_service),
) -> list[AdminRead]:
    return service.list_admins(actor, club_id)


@router.post(
    "/{club_id}/admins/invitations",
    response_model=InvitacionCreada,
    status_code=status.HTTP_201_CREATED,
    summary="Invitar a un administrador",
    description="Genera un código de un solo uso que vence en 72 horas. El código solo se "
    "muestra en esta respuesta. 409 si el club llegó al máximo de administradores, contando "
    "las invitaciones vigentes.",
)
def create_invitation(
    club_id: UUID,
    actor: Actor = Depends(get_club_admin),
    service: AdministradorService = Depends(get_administrador_service),
) -> InvitacionCreada:
    return service.create_invitacion(actor, club_id)


@router.get(
    "/{club_id}/admins/invitations",
    response_model=list[InvitacionRead],
    summary="Invitaciones vigentes del club (sin el código)",
)
def list_invitations(
    club_id: UUID,
    actor: Actor = Depends(get_club_admin),
    service: AdministradorService = Depends(get_administrador_service),
) -> list[InvitacionRead]:
    return service.list_invitaciones(actor, club_id)


@router.delete(
    "/{club_id}/admins/invitations/{invitacion_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Revocar una invitación vigente",
)
def revoke_invitation(
    club_id: UUID,
    invitacion_id: UUID,
    actor: Actor = Depends(get_club_admin),
    service: AdministradorService = Depends(get_administrador_service),
) -> Response:
    service.revoke_invitacion(actor, club_id, invitacion_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/{club_id}/admins/{usuario_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Quitar a un administrador o salir del club",
    description="El principal quita a otro administrador; un administrador sale del club "
    "con su propio usuario_id. El principal no puede ser quitado ni salir.",
)
def remove_admin(
    club_id: UUID,
    usuario_id: UUID,
    actor: Actor = Depends(get_club_admin),
    service: AdministradorService = Depends(get_administrador_service),
) -> Response:
    service.remove_admin(actor, club_id, usuario_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
