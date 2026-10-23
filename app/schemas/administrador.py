from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import EmailStr, Field, TypeAdapter, ValidationError

from app.core.security import EMAIL_MAX_LENGTH
from app.schemas.club import RolAdmin
from app.schemas.common import APIModel


class AdminRead(APIModel):
    """An administrator of the club, as the other administrators see them."""

    usuario_id: UUID
    nombre: str
    rol: RolAdmin
    fecha_alta: datetime | None


class AsignacionCreate(APIModel):
    """The email of the person who will administer the club. Its format is
    checked by normalizar_email in the service: the contract asks 422 for an
    invalid email (other input errors of this service are 400)."""

    email: str = Field(min_length=1, max_length=320)


_EMAIL = TypeAdapter(EmailStr)


def normalizar_email(value: str) -> str | None:
    """The email in lower case, the same way Ms_Users writes it in the token's
    email claim; None when it is not a valid address (email-validator, no DNS
    lookup) or is longer than 254 characters."""
    try:
        email = _EMAIL.validate_python(value.strip())
    except ValidationError:
        return None
    email = email.strip().lower()
    return email if len(email) <= EMAIL_MAX_LENGTH else None


class AsignacionRead(APIModel):
    """A pending assignment. It never says whether an account with that email exists."""

    id: UUID
    email: str
    asignada_por: UUID
    fecha_creacion: datetime
