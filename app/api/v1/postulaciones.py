from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.dependencies import get_athlete, get_club_admin, get_postulacion_service
from app.core.security import Actor
from app.schemas.postulacion import (
    CandidatoFilters,
    CandidatoPage,
    CandidatoRead,
    PostulacionCreate,
    PostulacionEstadoUpdate,
    PostulacionFilters,
    PostulacionPage,
    PostulacionRead,
)
from app.services.postulacion_service import PostulacionService

# Postulaciones. HU-33: only athletes (roles player/usuario) apply and follow
# their own applications. HU-34: only an administrator of the club that owns
# the convocatoria lists its candidates and decides on them.
router = APIRouter(prefix="/api/v1/clubs", tags=["postulaciones"])


@router.get(
    "/announcements/applications/me",
    response_model=PostulacionPage,
    summary="Mis postulaciones",
    description="Historial de postulaciones del deportista, la más reciente primero, con el "
    "estado de cada una (PENDIENTE, PRESELECCIONADO, ACEPTADO, RECHAZADO). Filtro opcional: estado.",
)
def my_postulaciones(
    filters: Annotated[PostulacionFilters, Query()],
    actor: Actor = Depends(get_athlete),
    service: PostulacionService = Depends(get_postulacion_service),
) -> PostulacionPage:
    return service.my_postulaciones(actor, filters)


@router.post(
    "/announcements/{convocatoria_id}/applications",
    response_model=PostulacionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Postular a una convocatoria",
    description="Adjunta el perfil deportivo público del deportista (desde Ms_Users), y un "
    "mensaje y antecedentes opcionales. La postulación queda PENDIENTE.",
)
def apply(
    convocatoria_id: UUID,
    payload: PostulacionCreate,
    actor: Actor = Depends(get_athlete),
    service: PostulacionService = Depends(get_postulacion_service),
) -> PostulacionRead:
    return service.apply(actor, convocatoria_id, payload)


@router.get(
    "/announcements/{convocatoria_id}/applications",
    response_model=CandidatoPage,
    summary="Candidatos de una convocatoria",
    description="Solo un administrador del club. Perfil deportivo adjunto, antecedentes y "
    "fecha de postulación, la más antigua primero. Filtro opcional: estado (PENDIENTE, "
    "PRESELECCIONADO, ACEPTADO, RECHAZADO). Incluye el conteo por estado.",
)
def list_candidatos(
    convocatoria_id: UUID,
    filters: Annotated[CandidatoFilters, Query()],
    actor: Actor = Depends(get_club_admin),
    service: PostulacionService = Depends(get_postulacion_service),
) -> CandidatoPage:
    return service.list_candidatos(actor, convocatoria_id, filters)


@router.patch(
    "/announcements/applications/{postulacion_id}",
    response_model=CandidatoRead,
    summary="Cambiar el estado de un candidato",
    description="Solo un administrador del club. Estados: PRESELECCIONADO, ACEPTADO, "
    "RECHAZADO, con comentario opcional para el deportista. Si el estado cambia, se avisa al "
    "deportista (bandeja y push vía Ms_gateway); repetir el mismo estado no genera aviso.",
)
def review_candidato(
    postulacion_id: UUID,
    payload: PostulacionEstadoUpdate,
    actor: Actor = Depends(get_club_admin),
    service: PostulacionService = Depends(get_postulacion_service),
) -> CandidatoRead:
    return service.review(actor, postulacion_id, payload)
