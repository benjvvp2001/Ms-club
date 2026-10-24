from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.responses import JSONResponse

from app.api.dependencies import get_canal_athlete, get_canal_service, get_club_admin
from app.core.security import Actor
from app.schemas.canal import (
    CanalClub,
    CanalCreate,
    CanalDeportista,
    CanalUpdate,
    DecisionInactividad,
    MensajeCanal,
    MensajesCanalClub,
    MensajesCanalDeportista,
    MiembroCanal,
    SilencioUpdate,
)
from app.schemas.consulta import LeidoUpdate, MensajeCreate, MensajeFilters
from app.services.canal_service import CanalService

# Channels of a club (G4). The club's routes take a club_admin token of one of
# its administrators (another club's channel is a 404); the athlete's, an
# athlete token of a member. Fixed paths (/canales/me) go first.
router = APIRouter(prefix="/api/v1/clubs", tags=["canales"])

_NO_CONTENT = {"status_code": status.HTTP_204_NO_CONTENT, "response_class": Response}


# -- athlete (members) --------------------------------------------------------------------


@router.get("/canales/me", response_model=list[CanalDeportista], summary="Mis canales de clubes")
def my_channels(
    actor: Actor = Depends(get_canal_athlete),
    service: CanalService = Depends(get_canal_service),
) -> list[CanalDeportista]:
    return service.my_channels(actor)


@router.get(
    "/canales/{canal_id}/messages",
    response_model=MensajesCanalDeportista,
    summary="Mensajes de un canal (miembro)",
)
def my_channel_messages(
    canal_id: UUID,
    filters: Annotated[MensajeFilters, Query()],
    actor: Actor = Depends(get_canal_athlete),
    service: CanalService = Depends(get_canal_service),
) -> MensajesCanalDeportista:
    return service.my_messages(actor, canal_id, filters)


@router.post(
    "/canales/{canal_id}/messages",
    response_model=MensajeCanal,
    status_code=status.HTTP_201_CREATED,
    summary="Escribir en un canal (miembro)",
    description="403 si en el canal solo escribe el club.",
)
def member_send(
    canal_id: UUID,
    payload: MensajeCreate,
    actor: Actor = Depends(get_canal_athlete),
    service: CanalService = Depends(get_canal_service),
) -> MensajeCanal:
    return service.send_as_member(actor, canal_id, payload)


@router.post("/canales/{canal_id}/read", summary="Marcar un canal como leído (miembro)", **_NO_CONTENT)
def member_read(
    canal_id: UUID,
    payload: LeidoUpdate,
    actor: Actor = Depends(get_canal_athlete),
    service: CanalService = Depends(get_canal_service),
) -> Response:
    service.mark_read_as_member(actor, canal_id, payload)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/canales/{canal_id}/mute", summary="Silenciar o reactivar los avisos de un canal", **_NO_CONTENT)
def member_mute(
    canal_id: UUID,
    payload: SilencioUpdate,
    actor: Actor = Depends(get_canal_athlete),
    service: CanalService = Depends(get_canal_service),
) -> Response:
    service.mute(actor, canal_id, payload.silenciado)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# -- club (administrators) ----------------------------------------------------------------


@router.get("/{club_id}/canales", response_model=list[CanalClub], summary="Canales del club")
def club_channels(
    club_id: UUID,
    actor: Actor = Depends(get_club_admin),
    service: CanalService = Depends(get_canal_service),
) -> list[CanalClub]:
    return service.list_club(actor, club_id)


@router.post(
    "/{club_id}/canales",
    response_model=CanalClub,
    status_code=status.HTTP_201_CREATED,
    summary="Crear un canal",
    description="Entran los deportistas con alguna postulación ACEPTADO. 409 si ya hay 20.",
)
def create_channel(
    club_id: UUID,
    payload: CanalCreate,
    actor: Actor = Depends(get_club_admin),
    service: CanalService = Depends(get_canal_service),
) -> CanalClub:
    return service.create(actor, club_id, payload)


@router.patch("/{club_id}/canales/{canal_id}", response_model=CanalClub, summary="Editar un canal")
def update_channel(
    club_id: UUID,
    canal_id: UUID,
    payload: CanalUpdate,
    actor: Actor = Depends(get_club_admin),
    service: CanalService = Depends(get_canal_service),
) -> CanalClub:
    return service.update(actor, club_id, canal_id, payload)


@router.delete("/{club_id}/canales/{canal_id}", summary="Eliminar un canal", **_NO_CONTENT)
def delete_channel(
    club_id: UUID,
    canal_id: UUID,
    actor: Actor = Depends(get_club_admin),
    service: CanalService = Depends(get_canal_service),
) -> Response:
    service.delete(actor, club_id, canal_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/{club_id}/canales/{canal_id}/miembros", response_model=list[MiembroCanal], summary="Miembros de un canal"
)
def channel_members(
    club_id: UUID,
    canal_id: UUID,
    actor: Actor = Depends(get_club_admin),
    service: CanalService = Depends(get_canal_service),
) -> list[MiembroCanal]:
    return service.members(actor, club_id, canal_id)


@router.get(
    "/{club_id}/canales/{canal_id}/candidatos",
    response_model=list[MiembroCanal],
    summary="Postulantes activos que aún no están en el canal",
)
def channel_candidates(
    club_id: UUID,
    canal_id: UUID,
    actor: Actor = Depends(get_club_admin),
    service: CanalService = Depends(get_canal_service),
) -> list[MiembroCanal]:
    return service.candidates(actor, club_id, canal_id)


@router.put(
    "/{club_id}/canales/{canal_id}/miembros/{usuario_id}",
    summary="Agregar un postulante al canal",
    description="Idempotente. 404 si no es postulante activo; 409 si el canal ya tiene 500.",
    **_NO_CONTENT,
)
def add_member(
    club_id: UUID,
    canal_id: UUID,
    usuario_id: UUID,
    actor: Actor = Depends(get_club_admin),
    service: CanalService = Depends(get_canal_service),
) -> Response:
    service.add_member(actor, club_id, canal_id, usuario_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/{club_id}/canales/{canal_id}/miembros/{usuario_id}", summary="Sacar a alguien del canal", **_NO_CONTENT
)
def remove_member(
    club_id: UUID,
    canal_id: UUID,
    usuario_id: UUID,
    actor: Actor = Depends(get_club_admin),
    service: CanalService = Depends(get_canal_service),
) -> Response:
    service.remove_member(actor, club_id, canal_id, usuario_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/{club_id}/canales/{canal_id}/messages",
    response_model=MensajesCanalClub,
    summary="Mensajes de un canal (administradores)",
)
def club_channel_messages(
    club_id: UUID,
    canal_id: UUID,
    filters: Annotated[MensajeFilters, Query()],
    actor: Actor = Depends(get_club_admin),
    service: CanalService = Depends(get_canal_service),
) -> MensajesCanalClub:
    return service.club_messages(actor, club_id, canal_id, filters)


@router.post(
    "/{club_id}/canales/{canal_id}/messages",
    response_model=MensajeCanal,
    status_code=status.HTTP_201_CREATED,
    summary="Escribir en un canal como club",
)
def club_send(
    club_id: UUID,
    canal_id: UUID,
    payload: MensajeCreate,
    actor: Actor = Depends(get_club_admin),
    service: CanalService = Depends(get_canal_service),
) -> MensajeCanal:
    return service.send_as_club(actor, club_id, canal_id, payload)


@router.post("/{club_id}/canales/{canal_id}/read", summary="Marcar un canal como leído (club)", **_NO_CONTENT)
def club_read(
    club_id: UUID,
    canal_id: UUID,
    payload: LeidoUpdate,
    actor: Actor = Depends(get_club_admin),
    service: CanalService = Depends(get_canal_service),
) -> Response:
    service.mark_read_as_club(actor, club_id, canal_id, payload)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{club_id}/canales/{canal_id}/inactividad",
    response_model=CanalClub,
    summary="Responder al aviso de inactividad",
    description="eliminar → 204; mantener → 200 con el canal. 409 sin aviso pendiente.",
    responses={204: {"description": "Canal eliminado"}},
)
def decide_inactivity(
    club_id: UUID,
    canal_id: UUID,
    payload: DecisionInactividad,
    actor: Actor = Depends(get_club_admin),
    service: CanalService = Depends(get_canal_service),
) -> Response:
    canal = service.decide_inactivity(actor, club_id, canal_id, payload)
    if canal is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return JSONResponse(content=canal.model_dump(mode="json"))
