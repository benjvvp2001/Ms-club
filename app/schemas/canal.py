from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.schemas.common import APIModel
from app.schemas.consulta import Lado

# Messages, read marks and paging are the same as in consultas (MensajeCreate,
# LeidoUpdate, MensajeFilters).
NOMBRE_CANAL_MAX = 60
MAX_CANALES = 20
MAX_MIEMBROS = 500


def _nombre(value: str) -> str:
    # str_strip_whitespace already trimmed it; inner runs of spaces become one.
    nombre = " ".join(value.split())
    if not nombre:
        raise ValueError("El nombre del canal no puede estar vacío.")
    return nombre


class CanalCreate(APIModel):
    nombre: str = Field(min_length=1, max_length=NOMBRE_CANAL_MAX)
    miembros_escriben: bool = False

    @field_validator("nombre")
    @classmethod
    def nombre_valido(cls, value: str) -> str:
        return _nombre(value)


class CanalUpdate(APIModel):
    nombre: str | None = Field(default=None, min_length=1, max_length=NOMBRE_CANAL_MAX)
    miembros_escriben: bool | None = None

    @field_validator("nombre")
    @classmethod
    def nombre_valido(cls, value: str | None) -> str | None:
        return None if value is None else _nombre(value)

    @model_validator(mode="after")
    def algo_que_cambiar(self) -> CanalUpdate:
        if self.nombre is None and self.miembros_escriben is None:
            raise ValueError("Indica nombre o miembros_escriben.")
        return self

    def changes(self) -> dict[str, object]:
        return self.model_dump(exclude_none=True)


class SilencioUpdate(APIModel):
    silenciado: bool


class DecisionInactividad(APIModel):
    decision: Literal["eliminar", "mantener"]


class MensajeCanal(APIModel):
    """propio: on the club side, any administrator's message; on the athlete's,
    their own. autor_nombre is the club's name when de = club."""

    id: int
    de: Lado
    autor_nombre: str
    texto: str
    enviado_en: datetime
    propio: bool


class CanalClub(APIModel):
    id: UUID
    club_id: UUID
    nombre: str
    miembros_escriben: bool
    miembros: int
    ultimo: MensajeCanal | None
    no_leidos: int
    aviso_inactividad_en: datetime | None
    mantener_hasta: datetime | None
    fecha_creacion: datetime


class CanalDeportista(APIModel):
    id: UUID
    club_id: UUID
    club_nombre: str
    club_logo: str | None
    nombre: str
    miembros_escriben: bool
    ultimo: MensajeCanal | None
    no_leidos: int
    silenciado: bool


class MiembroCanal(APIModel):
    usuario_id: UUID
    nombre: str
    agregado_en: datetime | None


class MensajesCanalClub(APIModel):
    canal: CanalClub
    mensajes: list[MensajeCanal]


class MensajesCanalDeportista(APIModel):
    canal: CanalDeportista
    mensajes: list[MensajeCanal]

