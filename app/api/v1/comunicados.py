from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.dependencies import get_athlete, get_club_admin, get_comunicado_service
from app.core.security import Actor
from app.schemas.comunicado import (
    ClubComunicadoFilters,
    ComunicadoClubPage,
    ComunicadoClubRead,
    ComunicadoCreate,
    ComunicadoFilters,
    ComunicadoPage,
)
from app.services.comunicado_service import ComunicadoService

# Notices from clubs (HU-38). Publishing and listing a club's notices require
# the club_admin who owns the club; the feed is for athletes (player/usuario).
# "/announcements" already belongs to the convocatorias (HU-32), so notices live
# under "/notices".
router = APIRouter(prefix="/api/v1/clubs", tags=["comunicados"])


@router.get(
    "/notices/me",
    response_model=ComunicadoPage,
    summary="Mi bandeja de comunicados",
    description="Comunicados de clubes dirigidos al deportista, el más reciente primero. "
    "Filtros opcionales: club_id y prioridad (NORMAL, URGENTE).",
)
def my_feed(
    filters: Annotated[ComunicadoFilters, Query()],
    actor: Actor = Depends(get_athlete),
    service: ComunicadoService = Depends(get_comunicado_service),
) -> ComunicadoPage:
    return service.my_feed(actor, filters)


@router.post(
    "/{club_id}/notices",
    response_model=ComunicadoClubRead,
    status_code=status.HTTP_201_CREATED,
    summary="Publicar un comunicado del club",
    description="Solo el representante dueño del club. Audiencia: POSTULANTES (postulaciones "
    "PENDIENTE o PRESELECCIONADO), MIEMBROS (ACEPTADO) o TODOS (ambos grupos). Cada destinatario "
    "recibe una notificación en su bandeja; la respuesta indica a cuántos llegó.",
)
def publish_comunicado(
    club_id: UUID,
    payload: ComunicadoCreate,
    actor: Actor = Depends(get_club_admin),
    service: ComunicadoService = Depends(get_comunicado_service),
) -> ComunicadoClubRead:
    return service.publish(actor, club_id, payload)


@router.get(
    "/{club_id}/notices",
    response_model=ComunicadoClubPage,
    summary="Comunicados publicados por el club",
    description="Solo el representante dueño del club. Filtros opcionales: audiencia y prioridad.",
)
def club_comunicados(
    club_id: UUID,
    filters: Annotated[ClubComunicadoFilters, Query()],
    actor: Actor = Depends(get_club_admin),
    service: ComunicadoService = Depends(get_comunicado_service),
) -> ComunicadoClubPage:
    return service.list_for_club(actor, club_id, filters)
