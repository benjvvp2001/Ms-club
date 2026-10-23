from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field

from app.schemas.club import RolAdmin
from app.schemas.common import APIModel


class AdminRead(APIModel):
    """An administrator of the club, as the other administrators see them."""

    usuario_id: UUID
    nombre: str
    rol: RolAdmin
    fecha_alta: datetime | None


class InvitacionCreada(APIModel):
    """The code is shown only in this response: only its hash is stored."""

    id: UUID
    codigo: str
    expira_en: datetime


class InvitacionRead(APIModel):
    """A pending invitation, without its code."""

    id: UUID
    creada_por: UUID
    fecha_creacion: datetime
    expira_en: datetime


class InvitacionAceptar(APIModel):
    # Spaces and dashes are ignored and letters are read in upper case, so a
    # code typed as "abcd-efgh-23" still matches.
    codigo: str = Field(min_length=1, max_length=40)
