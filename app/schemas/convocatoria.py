from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, StringConstraints, field_validator, model_validator

from app.schemas.common import PAGE_SIZE_MAX, PLACE_PATTERN, APIModel, Deporte, blank_is_none

# ABIERTA: accepts applicants. CERRADA: closed by the club before the date.
# FINALIZADA: the tryout date already passed. Computed when reading, never stored.
EstadoConvocatoria = Literal["ABIERTA", "CERRADA", "FINALIZADA"]
FECHA_PRUEBA_MAX_DIAS = 365
EDAD_MIN = 4
EDAD_MAX = 99

Requisito = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=200)]


class ConvocatoriaCreate(APIModel):
    """Tryout announcement published by a club (HU-32)."""

    # Sport of the catalog (GET /api/v1/clubs/deportes); the club must offer it.
    deporte_id: int = Field(ge=1)
    # Free label shown to athletes: "Sub-17", "Adulto", "+35", "14 a 18 años".
    categoria_edad: str = Field(min_length=2, max_length=40)
    # Optional numeric range, used by the "edad" filter.
    edad_minima: int | None = Field(default=None, ge=EDAD_MIN, le=EDAD_MAX)
    edad_maxima: int | None = Field(default=None, ge=EDAD_MIN, le=EDAD_MAX)
    # Date and time of the tryout, with its UTC offset (e.g. 2026-11-07T10:00:00-03:00).
    fecha_prueba: AwareDatetime
    requisitos: list[Requisito] = Field(min_length=1, max_length=15)
    cupos: int = Field(ge=1, le=1000)
    direccion: str = Field(min_length=5, max_length=255)
    comuna: str | None = Field(default=None, min_length=2, max_length=80, pattern=PLACE_PATTERN)
    titulo: str | None = Field(default=None, min_length=3, max_length=120)
    descripcion: str | None = Field(default=None, max_length=2000)

    @field_validator("comuna", "titulo", "descripcion", mode="before")
    @classmethod
    def optional_text(cls, value: object) -> object:
        return blank_is_none(value)

    @field_validator("fecha_prueba")
    @classmethod
    def fecha_futura(cls, value: datetime) -> datetime:
        now = datetime.now(timezone.utc)
        if value <= now:
            raise ValueError("La fecha de la prueba debe ser futura.")
        if value > now + timedelta(days=FECHA_PRUEBA_MAX_DIAS):
            raise ValueError(
                f"La fecha de la prueba no puede ser a más de {FECHA_PRUEBA_MAX_DIAS} días."
            )
        return value

    @model_validator(mode="after")
    def rango_de_edad(self) -> ConvocatoriaCreate:
        if (
            self.edad_minima is not None
            and self.edad_maxima is not None
            and self.edad_minima > self.edad_maxima
        ):
            raise ValueError("edad_minima no puede superar a edad_maxima.")
        return self

    def insert_values(self) -> dict[str, Any]:
        """Column values to insert; fields left out keep the table defaults."""
        return self.model_dump(exclude_none=True)


class ConvocatoriaFilters(APIModel):
    """Optional filters of the convocatorias section; all the given ones must match."""

    deporte: str | None = Field(default=None, min_length=2, max_length=50)
    deporte_id: int | None = Field(default=None, ge=1)
    # Partial match on categoria_edad ("sub" finds "Sub-17").
    categoria: str | None = Field(default=None, min_length=2, max_length=40)
    # Athlete's age: keeps calls whose numeric range admits it (or has no range).
    edad: int | None = Field(default=None, ge=EDAD_MIN, le=EDAD_MAX)
    club_id: UUID | None = None
    # Days in Chile's time zone (America/Santiago), both inclusive.
    fecha_desde: date | None = None
    fecha_hasta: date | None = None
    estado: EstadoConvocatoria = "ABIERTA"
    page: int = Field(default=1, ge=1, le=10_000)
    page_size: int = Field(default=20, ge=1, le=PAGE_SIZE_MAX)

    @model_validator(mode="after")
    def rango_de_fechas(self) -> ConvocatoriaFilters:
        if self.fecha_desde and self.fecha_hasta and self.fecha_desde > self.fecha_hasta:
            raise ValueError("fecha_desde no puede ser posterior a fecha_hasta.")
        return self


class ClubRef(APIModel):
    id: UUID
    nombre: str
    logo: str | None


class ConvocatoriaRead(APIModel):
    id: UUID
    club: ClubRef
    deporte: Deporte
    titulo: str | None
    descripcion: str | None
    categoria_edad: str
    edad_minima: int | None
    edad_maxima: int | None
    fecha_prueba: datetime
    requisitos: list[str]
    cupos: int
    # cupos minus the postulaciones not RECHAZADO (HU-33).
    cupos_disponibles: int
    direccion: str
    comuna: str | None
    estado: EstadoConvocatoria
    fecha_creacion: datetime | None


class ConvocatoriaPage(APIModel):
    items: list[ConvocatoriaRead]
    total: int
    page: int
    page_size: int
