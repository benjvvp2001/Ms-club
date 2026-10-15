from __future__ import annotations

from typing import Protocol

import httpx
from fastapi import HTTPException, status
from pydantic import ValidationError

from app.core.security import Actor
from app.schemas.postulacion import PerfilDeportivo


class AthleteProfiles(Protocol):
    """Source of the athlete's public sports profile (Ms_Users)."""

    def athlete_profile(self, actor: Actor) -> PerfilDeportivo: ...


class UsersClient:
    """HTTP access to the athlete's own public card in Ms_Users, with their token.

    Never opens the Users database. Ms_Users only serves cards of active athletes
    with a verified email, so this also confirms the account may apply.
    """

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def athlete_profile(self, actor: Actor) -> PerfilDeportivo:
        try:
            response = self._client.get(
                f"/api/v1/users/athletes/{actor.id}",
                headers={"Authorization": f"Bearer {actor.token}"},
            )
        except httpx.RequestError as error:
            raise _unavailable() from error
        if response.status_code == status.HTTP_401_UNAUTHORIZED:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid or expired access token",
            )
        if response.status_code in (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Tu perfil deportivo no está disponible: verifica tu correo en SportMatch.",
            )
        if response.status_code != status.HTTP_200_OK:
            raise _unavailable()
        try:
            card = response.json()
            return PerfilDeportivo.model_validate(
                {name: card.get(name) for name in PerfilDeportivo.model_fields}
            )
        except (ValueError, AttributeError, ValidationError) as error:
            raise _unavailable() from error


def _unavailable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="No se pudo obtener tu perfil deportivo. Inténtalo de nuevo.",
    )
