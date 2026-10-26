from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.dependencies import get_club_admin, get_consulta_athlete, get_consulta_service
from app.core.security import Actor
from app.schemas.consulta import (
    ConsultaIniciada,
    HiloClub,
    HiloDeportista,
    LeidoUpdate,
    Mensaje,
    MensajeCreate,
    MensajeFilters,
    MensajesPage,
)
from app.services.consulta_service import ConsultaService

# Consultas between an athlete and a club (HU-51). The athlete's routes take an
# athlete token (player/usuario); the club's, a club_admin token of one of its
# administrators. Fixed paths (/consultas/me) go before /{hilo_id}.
router = APIRouter(prefix="/api/v1/clubs", tags=["consultas"])


# -- athlete -----------------------------------------------------------------------------


@router.get(
    "/consultas/me",
    response_model=list[HiloDeportista],
    summary="Mis consultas a clubes",
    description="Una conversación por club, la del último mensaje primero.",
)
def my_threads(
    actor: Actor = Depends(get_consulta_athlete),
    service: ConsultaService = Depends(get_consulta_service),
) -> list[HiloDeportista]:
    return service.my_threads(actor)


@router.get(
    "/{club_id}/consultas/me/messages",
    response_model=MensajesPage,
    summary="Mensajes de mi consulta con un club",
    description="hilo_id es null si aún no hay conversación. Orden ascendente; after_id trae "
    "los siguientes, before_id los anteriores, limit hasta 100.",
)
def my_messages(
    club_id: UUID,
    filters: Annotated[MensajeFilters, Query()],
    actor: Actor = Depends(get_consulta_athlete),
    service: ConsultaService = Depends(get_consulta_service),
) -> MensajesPage:
    return service.my_messages(actor, club_id, filters)


@router.post(
    "/{club_id}/consultas/me/messages",
    response_model=Mensaje,
    status_code=status.HTTP_201_CREATED,
    summary="Escribir al club",
    description="Crea la conversación si no existe. Repetir el mismo client_message_id "
    "devuelve el mismo mensaje. 404 si el club no existe o está inactivo. 403 si no es "
    "postulante activo, o si el club desactivó el chat de una convocatoria a la que postuló "
    "(solo puede leer).",
)
def send_message(
    club_id: UUID,
    payload: MensajeCreate,
    actor: Actor = Depends(get_consulta_athlete),
    service: ConsultaService = Depends(get_consulta_service),
) -> Mensaje:
    return service.send_as_athlete(actor, club_id, payload)


@router.post(
    "/{club_id}/consultas/me/read",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Marcar como leída mi consulta hasta un mensaje",
)
def mark_read(
    club_id: UUID,
    payload: LeidoUpdate,
    actor: Actor = Depends(get_consulta_athlete),
    service: ConsultaService = Depends(get_consulta_service),
) -> Response:
    service.mark_read_as_athlete(actor, club_id, payload)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# -- club (administrators) ----------------------------------------------------------------


@router.get(
    "/{club_id}/consultas",
    response_model=list[HiloClub],
    summary="Bandeja de consultas del club",
    description="Compartida por todos los administradores, la del último mensaje primero.",
)
def club_threads(
    club_id: UUID,
    actor: Actor = Depends(get_club_admin),
    service: ConsultaService = Depends(get_consulta_service),
) -> list[HiloClub]:
    return service.club_threads(actor, club_id)


@router.post(
    "/{club_id}/consultas/postulantes/{usuario_id}/messages",
    response_model=ConsultaIniciada,
    status_code=status.HTTP_201_CREATED,
    summary="Escribirle primero a un postulante",
    description="Crea la conversación si no existe; idempotente por client_message_id. "
    "404 si la persona no es postulante activo del club.",
)
def club_start(
    club_id: UUID,
    usuario_id: UUID,
    payload: MensajeCreate,
    actor: Actor = Depends(get_club_admin),
    service: ConsultaService = Depends(get_consulta_service),
) -> ConsultaIniciada:
    return service.start_with_postulante(actor, club_id, usuario_id, payload)


@router.get(
    "/{club_id}/consultas/{hilo_id}/messages",
    response_model=MensajesPage,
    summary="Mensajes de una consulta",
)
def club_messages(
    club_id: UUID,
    hilo_id: UUID,
    filters: Annotated[MensajeFilters, Query()],
    actor: Actor = Depends(get_club_admin),
    service: ConsultaService = Depends(get_consulta_service),
) -> MensajesPage:
    return service.club_messages(actor, club_id, hilo_id, filters)


@router.post(
    "/{club_id}/consultas/{hilo_id}/messages",
    response_model=Mensaje,
    status_code=status.HTTP_201_CREATED,
    summary="Responder una consulta como club",
    description="El deportista ve la respuesta firmada por el club. Idempotente por client_message_id.",
)
def club_reply(
    club_id: UUID,
    hilo_id: UUID,
    payload: MensajeCreate,
    actor: Actor = Depends(get_club_admin),
    service: ConsultaService = Depends(get_consulta_service),
) -> Mensaje:
    return service.send_as_club(actor, club_id, hilo_id, payload)


@router.post(
    "/{club_id}/consultas/{hilo_id}/read",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Marcar como leída una consulta hasta un mensaje",
)
def club_mark_read(
    club_id: UUID,
    hilo_id: UUID,
    payload: LeidoUpdate,
    actor: Actor = Depends(get_club_admin),
    service: ConsultaService = Depends(get_consulta_service),
) -> Response:
    service.mark_read_as_club(actor, club_id, hilo_id, payload)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
