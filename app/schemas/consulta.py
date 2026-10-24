from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.schemas.common import APIModel

TEXTO_MAX = 1000
LIMIT_MAX = 100
LIMIT_DEFAULT = 50

Lado = Literal["deportista", "club"]


class MensajeCreate(APIModel):
    """A message of a consulta. client_message_id is the UUID the app generates:
    a retry with the same one returns the message already sent."""

    texto: str = Field(min_length=1, max_length=TEXTO_MAX)
    client_message_id: UUID

    @field_validator("texto")
    @classmethod
    def not_only_spaces(cls, value: str) -> str:
        # str_strip_whitespace already trimmed it; this catches what is left empty.
        if not value.strip():
            raise ValueError("El mensaje no puede estar vacío.")
        return value


class LeidoUpdate(APIModel):
    """Everything up to hasta_id (inclusive) has been read."""

    hasta_id: int = Field(ge=0, le=2**63 - 1)


class MensajeFilters(APIModel):
    """after_id: the first messages after it (to fetch new ones); before_id: the
    last ones before it (to load history); neither: the latest ones."""

    after_id: int | None = Field(default=None, ge=0, le=2**63 - 1)
    before_id: int | None = Field(default=None, ge=1, le=2**63 - 1)
    limit: int = Field(default=LIMIT_DEFAULT, ge=1, le=LIMIT_MAX)

    @model_validator(mode="after")
    def coherent_range(self) -> MensajeFilters:
        if self.after_id is not None and self.before_id is not None and self.after_id >= self.before_id:
            raise ValueError("after_id debe ser menor que before_id.")
        return self


class Mensaje(APIModel):
    """propio: written by whoever is reading (on the club side, by any administrator)."""

    id: int
    de: Lado
    texto: str
    enviado_en: datetime
    propio: bool


class MensajesPage(APIModel):
    hilo_id: UUID | None
    mensajes: list[Mensaje]


class HiloDeportista(APIModel):
    hilo_id: UUID
    club_id: UUID
    club_nombre: str
    club_logo: str | None
    ultimo: Mensaje | None
    no_leidos: int


class HiloClub(APIModel):
    hilo_id: UUID
    deportista_id: UUID
    deportista_nombre: str
    ultimo: Mensaje | None
    no_leidos: int


class ConsultaIniciada(APIModel):
    """Answer of the club writing first to a postulante (G3)."""

    hilo_id: UUID
    mensaje: Mensaje
