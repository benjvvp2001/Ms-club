from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.schemas.common import PAGE_SIZE_MAX, APIModel, blank_is_none
from app.schemas.convocatoria import ConvocatoriaRead

# Values without accents, like every other enum value of the API.
# PRESELECCIONADO replaced HU-33's EN_REVISION (migration 004).
EstadoPostulacion = Literal["PENDIENTE", "PRESELECCIONADO", "ACEPTADO", "RECHAZADO"]
# States the club can set when reviewing; PENDIENTE is only the initial one.
EstadoRevision = Literal["PRESELECCIONADO", "ACEPTADO", "RECHAZADO"]
ANTECEDENTES_MAX = 10
ANIO_MINIMO = 1950


class Antecedente(APIModel):
    """One entry of the athlete's sports background."""

    institucion: str = Field(min_length=2, max_length=100)  # club, team, school...
    deporte: str = Field(min_length=2, max_length=50)
    anio_inicio: int = Field(ge=ANIO_MINIMO)
    anio_fin: int | None = Field(default=None, ge=ANIO_MINIMO)  # None: still there
    logros: str | None = Field(default=None, max_length=300)

    @field_validator("logros", mode="before")
    @classmethod
    def optional_text(cls, value: object) -> object:
        return blank_is_none(value)

    @model_validator(mode="after")
    def periodo_valido(self) -> Antecedente:
        this_year = date.today().year
        if self.anio_inicio > this_year or (self.anio_fin is not None and self.anio_fin > this_year):
            raise ValueError("Los años de un antecedente no pueden ser futuros.")
        if self.anio_fin is not None and self.anio_fin < self.anio_inicio:
            raise ValueError("anio_fin no puede ser anterior a anio_inicio.")
        return self


class PostulacionCreate(APIModel):
    """Athlete's application to a convocatoria (HU-33). The public sports
    profile from Ms_Users is attached by the service; the athlete adds an
    optional presentation and background."""

    mensaje: str | None = Field(default=None, max_length=1000)
    antecedentes: list[Antecedente] = Field(default_factory=list, max_length=ANTECEDENTES_MAX)

    @field_validator("mensaje", mode="before")
    @classmethod
    def optional_text(cls, value: object) -> object:
        return blank_is_none(value)

    def insert_values(self) -> dict[str, Any]:
        return {
            "mensaje": self.mensaje,
            "antecedentes": [entry.model_dump() for entry in self.antecedentes],
        }


class PostulacionFilters(APIModel):
    estado: EstadoPostulacion | None = None
    page: int = Field(default=1, ge=1, le=10_000)
    page_size: int = Field(default=20, ge=1, le=PAGE_SIZE_MAX)


class PostulacionEstadoUpdate(APIModel):
    """The club's decision on a candidate (HU-34)."""

    estado: EstadoRevision
    # Feedback the athlete reads in their applications. Left out: unchanged;
    # null or blank: removed.
    comentario: str | None = Field(default=None, max_length=1000)

    @field_validator("comentario", mode="before")
    @classmethod
    def optional_text(cls, value: object) -> object:
        return blank_is_none(value)


class CandidatoFilters(APIModel):
    estado: EstadoPostulacion | None = None
    page: int = Field(default=1, ge=1, le=10_000)
    page_size: int = Field(default=20, ge=1, le=PAGE_SIZE_MAX)


class DeporteDeclarado(APIModel):
    deporte_codigo: str
    nivel: int


class PerfilDeportivo(APIModel):
    """Snapshot of the athlete's public card in Ms_Users when applying."""

    nombre: str
    apellido_inicial: str
    edad: int | None
    foto_perfil: str | None
    biografia: str | None
    deportes: list[DeporteDeclarado]


class PostulacionRead(APIModel):
    id: UUID
    estado: EstadoPostulacion
    convocatoria: ConvocatoriaRead
    mensaje: str | None
    antecedentes: list[Antecedente]
    perfil_deportivo: PerfilDeportivo
    # Feedback from the club when it reviewed the application (HU-34).
    comentario_club: str | None
    fecha_creacion: datetime
    fecha_actualizacion: datetime


class CandidatoRead(APIModel):
    """A candidate as the club sees it (HU-34)."""

    id: UUID  # the postulación
    usuario_id: UUID  # the athlete's Ms_Users account
    estado: EstadoPostulacion
    perfil_deportivo: PerfilDeportivo
    mensaje: str | None
    antecedentes: list[Antecedente]
    comentario_club: str | None
    fecha_postulacion: datetime
    fecha_actualizacion: datetime


class CandidatoPage(APIModel):
    convocatoria: ConvocatoriaRead
    items: list[CandidatoRead]
    total: int
    page: int
    page_size: int
    # Candidates of the whole convocatoria per estado, whatever the filter.
    conteo_por_estado: dict[str, int]


class PostulacionPage(APIModel):
    items: list[PostulacionRead]
    total: int
    page: int
    page_size: int
