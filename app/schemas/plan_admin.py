"""HU-56: gestion de planes y convenios por el administrador de la plataforma."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.schemas.common import PAGE_SIZE_MAX, APIModel, blank_is_none
from app.schemas.pago import PlanCategoria, PlanRead, PlanTipo

# Webpay Plus cobra montos enteros en pesos chilenos (CLP no tiene decimales): el precio es un entero.
# Un tope razonable evita errores de digitacion (10 millones de pesos).
PRECIO_MAX = 10_000_000
DURACION_MIN, DURACION_MAX = 1, 366
NOMBRE_MIN, NOMBRE_MAX = 3, 60
DESCRIPCION_MAX = 255
BENEFICIOS_MAX, BENEFICIO_MIN, BENEFICIO_MAX = 10, 3, 120
INSTITUCION_MIN, INSTITUCION_MAX = 3, 80
MOTIVO_MIN, MOTIVO_MAX = 3, 300

_LETRAS = "A-Za-zÁÉÍÓÚÜÑáéíóúüñ"
NOMBRE_PATTERN = rf"^[{_LETRAS}0-9][{_LETRAS}0-9 .,'&()/:+%-]*$"
# NUL y demas caracteres de control, y sustitutos Unicode sueltos (no se pueden guardar en UTF-8).
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\ud800-\udfff]")
# El motivo viaja a la auditoria de Ms_Users, que rechaza correos, RUT y credenciales: se rechazan aqui para
# responder 400 y no fallar despues.
_EMAIL = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")
_RUT = re.compile(r"\b\d{1,2}\.?\d{3}\.?\d{3}-[\dkK]\b")
_JWT = re.compile(r"\beyJ[\w-]{8,}\.[\w-]{8,}")

Nombre = Annotated[str, Field(min_length=NOMBRE_MIN, max_length=NOMBRE_MAX, pattern=NOMBRE_PATTERN)]
Precio = Annotated[int, Field(strict=True, ge=0, le=PRECIO_MAX)]
Duracion = Annotated[int, Field(strict=True, ge=DURACION_MIN, le=DURACION_MAX)]
Institucion = Annotated[str, Field(min_length=INSTITUCION_MIN, max_length=INSTITUCION_MAX, pattern=NOMBRE_PATTERN)]
Beneficio = Annotated[str, Field(min_length=BENEFICIO_MIN, max_length=BENEFICIO_MAX)]
Motivo = Annotated[str, Field(min_length=MOTIVO_MIN, max_length=MOTIVO_MAX)]


def _clean(value: str, field: str) -> str:
    if _CONTROL.search(value):
        raise ValueError(f"{field} contiene caracteres no permitidos.")
    return value


class _PlanTextos(APIModel):
    @field_validator("descripcion", "motivo", check_fields=False)
    @classmethod
    def textos_sin_control(cls, value: str | None, info) -> str | None:
        if value is None:
            return None
        _clean(value, info.field_name)
        if info.field_name == "motivo" and (_EMAIL.search(value) or _RUT.search(value) or _JWT.search(value)):
            raise ValueError("motivo no puede incluir correos, RUT ni credenciales.")
        return value

    @field_validator("nombre", "institucion", check_fields=False)
    @classmethod
    def nombre_sin_control(cls, value: str | None, info) -> str | None:
        return None if value is None else _clean(value, info.field_name)

    @field_validator("beneficios", check_fields=False)
    @classmethod
    def beneficios_validos(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        vistos: set[str] = set()
        for beneficio in value:
            _clean(beneficio, "beneficios")
            clave = " ".join(beneficio.split()).casefold()
            if clave in vistos:
                raise ValueError("beneficios no puede repetir un beneficio.")
            vistos.add(clave)
        return value


class PlanCreate(_PlanTextos):
    nombre: Nombre
    tipo: PlanTipo
    categoria: PlanCategoria = "PLAN"
    descripcion: Annotated[str | None, Field(max_length=DESCRIPCION_MAX)] = None
    precio: Precio
    # null = sin vencimiento, solo si es gratis.
    duracion_dias: Duracion | None
    beneficios: Annotated[list[Beneficio], Field(max_length=BENEFICIOS_MAX)] = Field(default_factory=list)
    # Solo un convenio tiene institucion (con quien se firmo).
    institucion: Institucion | None = None
    motivo: Motivo | None = None

    @field_validator("descripcion", "motivo", "institucion", mode="before")
    @classmethod
    def vacio_es_nulo(cls, value: object) -> object:
        return blank_is_none(value)

    @model_validator(mode="after")
    def institucion_solo_en_convenios(self) -> "PlanCreate":
        if self.institucion is not None and self.categoria != "CONVENIO":
            raise ValueError("Solo un convenio tiene institución.")
        return self

    @model_validator(mode="after")
    def sin_vencimiento_solo_gratis(self) -> "PlanCreate":
        if self.duracion_dias is None and self.precio > 0:
            raise ValueError("Un plan de pago necesita una duración (de 1 a 366 días).")
        return self


class PlanUpdate(_PlanTextos):
    """Solo cambia lo que se envia. El tipo (quien lo compra) y la categoria no se editan."""

    nombre: Nombre | None = None
    descripcion: Annotated[str | None, Field(max_length=DESCRIPCION_MAX)] = None
    precio: Precio | None = None
    # null = sin vencimiento (solo si el precio resultante es 0: lo valida el servicio con el precio actual).
    duracion_dias: Duracion | None = None
    beneficios: Annotated[list[Beneficio], Field(max_length=BENEFICIOS_MAX)] | None = None
    # Solo en un convenio; null la borra.
    institucion: Institucion | None = None
    motivo: Motivo | None = None

    @field_validator("descripcion", "motivo", "institucion", mode="before")
    @classmethod
    def vacio_es_nulo(cls, value: object) -> object:
        return blank_is_none(value)

    @model_validator(mode="after")
    def algo_que_cambiar(self) -> "PlanUpdate":
        enviados = self.model_fields_set - {"motivo"}
        if not enviados:
            raise ValueError("Envía al menos un campo para cambiar.")
        # Estos campos no admiten null; descripcion si (la borra).
        if any(getattr(self, campo) is None for campo in enviados & {"nombre", "precio", "beneficios"}):
            raise ValueError("nombre, precio y beneficios no pueden ser null.")
        return self


class PlanDesactivar(_PlanTextos):
    motivo: Motivo | None = None

    @field_validator("motivo", mode="before")
    @classmethod
    def vacio_es_nulo(cls, value: object) -> object:
        return blank_is_none(value)


class PlanReactivar(_PlanTextos):
    motivo: Motivo | None = None

    @field_validator("motivo", mode="before")
    @classmethod
    def vacio_es_nulo(cls, value: object) -> object:
        return blank_is_none(value)


class PlanAdminRead(PlanRead):
    is_active: bool
    # El plan base no se puede desactivar. Cuando y por que se retiro del catalogo (null mientras esta activo).
    es_base: bool = False
    fecha_desactivacion: datetime | None = None
    motivo_desactivacion: str | None = None
    # Suscripciones cuya vigencia (fecha_fin) aun no termina y cuyo ultimo pago fue de este plan.
    suscripciones_vigentes: int
    fecha_creacion: datetime | None


class PlanAdminPage(APIModel):
    items: list[PlanAdminRead]
    pagina: int
    limit: int
    total: int


class PlanSuscripcionRead(APIModel):
    """Una suscripcion de un plan. `suscriptor` es el nombre corto ("Ana T.") o el del club; null si la cuenta ya no
    existe o su nombre no esta disponible. Nunca incluye correo ni RUT."""

    id: UUID
    suscriptor: str | None
    tipo_suscriptor: Literal["USUARIO", "CLUB"]
    estado: Literal["ACTIVE", "EXPIRED"]
    # El nombre del plan y el precio que pago al contratar: un cambio posterior en el plan no los altera.
    plan_nombre: str
    precio_pagado: int
    fecha_inicio: datetime
    fecha_fin: datetime


class PlanSuscripcionPage(APIModel):
    items: list[PlanSuscripcionRead]
    pagina: int
    limit: int
    total: int


EstadoPlanFiltro = Literal["activo", "inactivo", "todos"]
EstadoSuscripcionFiltro = Literal["vigente", "vencida", "todas"]
LIMIT_DEFAULT = 20
LIMIT_MAX = PAGE_SIZE_MAX
SUSCRIPCIONES_LIMIT_MAX = 100
