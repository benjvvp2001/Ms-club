from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Response, status

from app.api.dependencies import get_administrador_service, get_club_admin
from app.core.security import Actor
from app.schemas.administrador import AdminRead, AsignacionCreate, AsignacionRead
from app.services.administrador_service import AdministradorService

# Several administrators per club (sprint clubes y chats, secciones A y E). Every
# route requires a club_admin token; the service checks the account administers
# the club and, to assign or revoke, that it is its PRINCIPAL. An assigned email
# becomes an ADMIN on GET /clubs/me (see ClubService.my_club).
router = APIRouter(prefix="/api/v1/clubs", tags=["administradores"])


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


@router.get(
    "/{club_id}/admins/assignments",
    response_model=list[AsignacionRead],
    summary="Correos asignados pendientes",
    description="Asignaciones que aún nadie activó, la más reciente primero.",
)
def list_assignments(
    club_id: UUID,
    actor: Actor = Depends(get_club_admin),
    service: AdministradorService = Depends(get_administrador_service),
) -> list[AsignacionRead]:
    return service.list_asignaciones(actor, club_id)


@router.post(
    "/{club_id}/admins/assignments",
    response_model=AsignacionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Asignar un correo como administrador",
    description="Solo el administrador principal. La asignación queda pendiente hasta que una "
    "cuenta de club con ese correo verificado entra a la app; la respuesta nunca indica si "
    "esa cuenta existe. 409 si el correo ya está pendiente en este club, es el propio o no "
    "queda cupo (administradores más asignaciones pendientes).",
)
def create_assignment(
    club_id: UUID,
    payload: AsignacionCreate,
    actor: Actor = Depends(get_club_admin),
    service: AdministradorService = Depends(get_administrador_service),
) -> AsignacionRead:
    return service.create_asignacion(actor, club_id, payload.email)


@router.delete(
    "/{club_id}/admins/assignments/{asignacion_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Revocar una asignación pendiente",
    description="Solo el administrador principal. 404 si no existe o ya no está pendiente.",
)
def revoke_assignment(
    club_id: UUID,
    asignacion_id: UUID,
    actor: Actor = Depends(get_club_admin),
    service: AdministradorService = Depends(get_administrador_service),
) -> Response:
    service.revoke_asignacion(actor, club_id, asignacion_id)
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
