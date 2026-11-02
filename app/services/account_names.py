"""HU-56: nombres cortos de las cuentas de los suscriptores, pedidos a Ms_Users por la red interna.

Los deportistas viven en la base de Ms_Users: ms-club solo guarda su id. El nombre es una comodidad para el panel
de administracion: si Ms_Users no responde, la lista sale igual y el nombre queda vacio (nunca bloquea)."""
from __future__ import annotations

import logging
from typing import Protocol
from uuid import UUID

import httpx

logger = logging.getLogger(__name__)

NAMES_PATH = "/internal/v1/accounts/names"
MIN_TOKEN_BYTES = 32


class AccountDirectory(Protocol):
    def names(self, ids: list[UUID]) -> dict[UUID, str]: ...


class InternalAccountsClient:
    """POST /internal/v1/accounts/names with the service-to-service secret (X-Internal-Service-Token)."""

    def __init__(self, client: httpx.Client, token: str | None) -> None:
        self._client = client
        self._token = token if token and len(token.encode("utf-8")) >= MIN_TOKEN_BYTES else None

    def names(self, ids: list[UUID]) -> dict[UUID, str]:
        if not ids:
            return {}
        if self._token is None:
            logger.error("INTERNAL_SERVICE_TOKEN is not configured: subscriber names are not available")
            return {}
        try:
            response = self._client.post(NAMES_PATH, json={"ids": [str(i) for i in ids]},
                                         headers={"X-Internal-Service-Token": self._token})
            if response.status_code != 200:
                logger.error("Ms_Users refused the names request: HTTP %s", response.status_code)
                return {}
            data = response.json().get("names", {})
            return {UUID(key): value for key, value in data.items() if isinstance(value, str) and value.strip()}
        except (httpx.HTTPError, ValueError, AttributeError) as error:
            logger.error("Ms_Users did not give the subscriber names: %s", type(error).__name__)
            return {}
