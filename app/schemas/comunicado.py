from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field

from app.schemas.common import PAGE_SIZE_MAX, APIModel
from app.schemas.convocatoria import ClubRef

Audiencia = Literal["TODOS", "POSTULANTES", "MIEMBROS"]
Prioridad = Literal["NORMAL", "URGENTE"]

# Who receives a notice, from the estado of their postulaciones to the club's
# convocatorias. There is no follow/subscription table yet, so TODOS is the
# club's whole community: everyone who is applying or was accepted.
#   POSTULANTES: applications still in play.
#   MIEMBROS:    athletes accepted (confirmed) in a convocatoria.
ESTADOS_POR_AUDIENCIA: dict[str, tuple[str, ...]] = {
    "POSTULANTES": ("PENDIENTE", "PRESELECCIONADO"),
    "MIEMBROS": ("ACEPTADO",),
    "TODOS": ("PENDIENTE", "PRESELECCIONADO", "ACEPTADO"),
}


class ComunicadoCreate(APIModel):
    """Notice a club publishes for its athletes (HU-38)."""

    titulo: str = Field(min_length=3, max_length=120)
    contenido: str = Field(min_length=5, max_length=2000)
    audiencia: Audiencia
    prioridad: Prioridad = "NORMAL"

    def insert_values(self) -> dict[str, Any]:
        return self.model_dump()


class ComunicadoFilters(APIModel):
    """Filters of the athlete's feed."""

    club_id: UUID | None = None
    prioridad: Prioridad | None = None
    page: int = Field(default=1, ge=1, le=10_000)
    page_size: int = Field(default=20, ge=1, le=PAGE_SIZE_MAX)


class ClubComunicadoFilters(APIModel):
    """Filters of the notices a club has published."""

    audiencia: Audiencia | None = None
    prioridad: Prioridad | None = None
    page: int = Field(default=1, ge=1, le=10_000)
    page_size: int = Field(default=20, ge=1, le=PAGE_SIZE_MAX)


class ComunicadoRead(APIModel):
    """A notice as the athlete reads it in their feed."""

    id: UUID
    club: ClubRef
    titulo: str
    contenido: str
    audiencia: Audiencia
    prioridad: Prioridad
    fecha_creacion: datetime


class ComunicadoClubRead(ComunicadoRead):
    """A notice as the club sees it: also how many athletes it reached."""

    destinatarios: int


class ComunicadoPage(APIModel):
    items: list[ComunicadoRead]
    total: int
    page: int
    page_size: int


class ComunicadoClubPage(APIModel):
    items: list[ComunicadoClubRead]
    total: int
    page: int
    page_size: int
