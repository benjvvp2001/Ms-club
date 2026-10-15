from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from pydantic import EmailStr, Field, field_validator, model_validator

from app.schemas.common import PAGE_SIZE_MAX, PLACE_PATTERN, APIModel, Deporte, blank_is_none
from app.schemas.convocatoria import ConvocatoriaRead

DESCRIPCION_CORTA_MAX = 160
PHONE_PATTERN = r"^\+?[0-9][0-9 ()-]{6,19}$"
HTTPS_URL_PATTERN = r"^https://\S+$"
HTTP_URL_PATTERN = r"^https?://\S+$"
EMAIL_MAX_LENGTH = 100
ESTADOS_VERIFICACION = ("pendiente", "verificado", "rechazado")
DEPORTES_MAX = 30


class ClubFilters(APIModel):
    """Optional filters of the directory; all the given ones must match."""

    nombre: str | None = Field(default=None, min_length=2, max_length=100)
    deporte: str | None = Field(default=None, min_length=2, max_length=50)
    deporte_id: int | None = Field(default=None, ge=1)
    zona: str | None = Field(default=None, min_length=2, max_length=100)
    page: int = Field(default=1, ge=1, le=10_000)
    page_size: int = Field(default=20, ge=1, le=PAGE_SIZE_MAX)


# -- write DTOs (HU-31) -----------------------------------------------------


class RedesSociales(APIModel):
    """Public social links of the club. Only https links."""

    instagram: str | None = Field(default=None, max_length=300, pattern=HTTPS_URL_PATTERN)
    facebook: str | None = Field(default=None, max_length=300, pattern=HTTPS_URL_PATTERN)
    x: str | None = Field(default=None, max_length=300, pattern=HTTPS_URL_PATTERN)
    tiktok: str | None = Field(default=None, max_length=300, pattern=HTTPS_URL_PATTERN)
    youtube: str | None = Field(default=None, max_length=300, pattern=HTTPS_URL_PATTERN)


class _ClubFields(APIModel):
    """Editable profile fields shared by creation and edition. Limits follow
    the club table columns."""

    descripcion: str | None = Field(default=None, max_length=500)
    historia: str | None = Field(default=None, max_length=3000)
    direccion: str | None = Field(default=None, max_length=255)
    comuna: str | None = Field(default=None, min_length=2, max_length=80, pattern=PLACE_PATTERN)
    provincia: str | None = Field(default=None, min_length=2, max_length=80, pattern=PLACE_PATTERN)
    latitud: float | None = Field(default=None, ge=-90, le=90)
    longitud: float | None = Field(default=None, ge=-180, le=180)
    telefono: str | None = Field(default=None, max_length=20, pattern=PHONE_PATTERN)
    email_contacto: EmailStr | None = None
    sitio_web: str | None = Field(default=None, max_length=300, pattern=HTTP_URL_PATTERN)
    logo: str | None = Field(default=None, max_length=255, pattern=HTTPS_URL_PATTERN)
    portada: str | None = Field(default=None, max_length=255, pattern=HTTPS_URL_PATTERN)
    redes_sociales: RedesSociales | None = None
    # Sports the club offers, by id of the deporte catalog (GET /api/v1/clubs/deportes).
    deporte_ids: list[Annotated[int, Field(ge=1)]] | None = Field(default=None, max_length=DEPORTES_MAX)

    @field_validator(
        "descripcion", "historia", "direccion", "comuna", "provincia", "telefono",
        "email_contacto", "sitio_web", "logo", "portada", mode="before",
    )
    @classmethod
    def optional_text(cls, value: object) -> object:
        return blank_is_none(value)

    @field_validator("deporte_ids")
    @classmethod
    def unique_deportes(cls, value: list[int] | None) -> list[int] | None:
        if value is not None and len(set(value)) != len(value):
            raise ValueError("deporte_ids no puede repetir deportes.")
        return value

    @field_validator("email_contacto")
    @classmethod
    def email_length(cls, value: str | None) -> str | None:
        if value is not None and len(value) > EMAIL_MAX_LENGTH:
            raise ValueError(f"El correo no puede superar {EMAIL_MAX_LENGTH} caracteres.")
        return value


class ClubCreate(_ClubFields):
    nombre: str = Field(min_length=2, max_length=100)

    @model_validator(mode="after")
    def coordenadas_completas(self) -> ClubCreate:
        if (self.latitud is None) != (self.longitud is None):
            raise ValueError("latitud y longitud se envían juntas.")
        return self

    def insert_values(self) -> dict[str, Any]:
        """Column values to insert; fields left out keep the table defaults."""
        data = self.model_dump(exclude_none=True, exclude={"deporte_ids"})
        if self.redes_sociales is not None:
            data["redes_sociales"] = self.redes_sociales.model_dump(exclude_none=True)
        return data


class ClubUpdate(_ClubFields):
    """Partial edition: only the fields sent change; null clears an optional
    field. redes_sociales and deporte_ids are replaced as a whole."""

    nombre: str | None = Field(default=None, min_length=2, max_length=100)

    @model_validator(mode="after")
    def something_to_change(self) -> ClubUpdate:
        if not self.model_fields_set:
            raise ValueError("Envía al menos un campo para actualizar.")
        if "nombre" in self.model_fields_set and self.nombre is None:
            raise ValueError("El nombre del club no se puede borrar.")
        if "deporte_ids" in self.model_fields_set and self.deporte_ids is None:
            raise ValueError("Para quitar todos los deportes envía deporte_ids: [].")
        return self

    def changes(self) -> dict[str, Any]:
        """Column values to update (deporte_ids is not a club column)."""
        data = self.model_dump(include=self.model_fields_set - {"deporte_ids"})
        if "redes_sociales" in data:
            redes = self.redes_sociales
            data["redes_sociales"] = redes.model_dump(exclude_none=True) if redes else {}
        return data


# -- responses ----------------------------------------------------------------


class Ubicacion(APIModel):
    direccion: str | None
    comuna: str | None
    provincia: str | None
    latitud: float | None
    longitud: float | None


class ClubSummary(APIModel):
    """Directory card: no contact data."""

    id: UUID
    nombre: str
    descripcion_corta: str | None
    logo: str | None
    direccion: str | None
    comuna: str | None
    verificado: bool
    deportes: list[Deporte]


class ClubPage(APIModel):
    items: list[ClubSummary]
    total: int
    page: int
    page_size: int


class _ClubPublicData(APIModel):
    """The managing account (club.usuario_id) is never exposed."""

    id: UUID
    nombre: str
    descripcion: str | None
    historia: str | None
    logo: str | None
    portada: str | None
    ubicacion: Ubicacion
    email_contacto: str | None
    telefono: str | None
    sitio_web: str | None
    redes_sociales: dict[str, str]
    verificado: bool
    deportes: list[Deporte]


class ClubProfile(_ClubPublicData):
    """Public profile of a club."""

    convocatorias_abiertas: list[ConvocatoriaRead]


class ClubManaged(_ClubPublicData):
    """What the club's representative sees and edits."""

    is_active: bool
    estado_verificacion: str
    fecha_creacion: datetime | None
    fecha_actualizacion: datetime | None
