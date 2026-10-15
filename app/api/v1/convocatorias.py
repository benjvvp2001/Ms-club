from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.dependencies import get_club_admin, get_convocatoria_service
from app.core.security import Actor
from app.schemas.convocatoria import (
    ConvocatoriaCreate,
    ConvocatoriaFilters,
    ConvocatoriaPage,
    ConvocatoriaRead,
)
from app.services.convocatoria_service import ConvocatoriaService

# Convocatorias section (HU-32). Reading is public, like the clubs directory;
# publishing and closing require a club_admin token and only on the club the
# account owns.
router = APIRouter(prefix="/api/v1/clubs", tags=["convocatorias"])


@router.get(
    "/announcements",
    response_model=ConvocatoriaPage,
    summary="Listar convocatorias",
    description="Por defecto solo las ABIERTAS, la más próxima primero. Filtros opcionales y "
    "combinables: deporte, deporte_id, categoria, edad, club_id, fecha_desde, fecha_hasta "
    "(días en hora de Chile) y estado (ABIERTA, CERRADA, FINALIZADA).",
)
def list_convocatorias(
    filters: Annotated[ConvocatoriaFilters, Query()],
    service: ConvocatoriaService = Depends(get_convocatoria_service),
) -> ConvocatoriaPage:
    return service.list_convocatorias(filters)


@router.get(
    "/announcements/{convocatoria_id}",
    response_model=ConvocatoriaRead,
    summary="Detalle de una convocatoria",
)
def convocatoria_detail(
    convocatoria_id: UUID,
    service: ConvocatoriaService = Depends(get_convocatoria_service),
) -> ConvocatoriaRead:
    return service.get_convocatoria(convocatoria_id)


@router.post(
    "/{club_id}/announcements",
    response_model=ConvocatoriaRead,
    status_code=status.HTTP_201_CREATED,
    summary="Publicar una convocatoria del club",
    description="Solo el representante dueño del club. La fecha debe ser futura y los cupos "
    "mayores que cero; el deporte debe estar entre los que ofrece el club.",
)
def publish_convocatoria(
    club_id: UUID,
    payload: ConvocatoriaCreate,
    actor: Actor = Depends(get_club_admin),
    service: ConvocatoriaService = Depends(get_convocatoria_service),
) -> ConvocatoriaRead:
    return service.publish(actor, club_id, payload)


@router.post(
    "/{club_id}/announcements/{convocatoria_id}/close",
    response_model=ConvocatoriaRead,
    summary="Cerrar una convocatoria antes de su fecha",
    description="Pasa a CERRADA. Idempotente; una convocatoria FINALIZADA responde 409.",
)
def close_convocatoria(
    club_id: UUID,
    convocatoria_id: UUID,
    actor: Actor = Depends(get_club_admin),
    service: ConvocatoriaService = Depends(get_convocatoria_service),
) -> ConvocatoriaRead:
    return service.close(actor, club_id, convocatoria_id)
