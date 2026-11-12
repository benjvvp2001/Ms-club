"""The CURRENT profile photo of several accounts, asked to Ms_Users on the internal network.

A postulación keeps a copy of the athlete's card from the day they applied, but the photo file is deleted by Ms_Users when
the person replaces or removes it, so the copy ends up pointing at a 404. The club therefore never reads the copy: it
asks here, once per list, for the photos that exist now. Same pattern as Ms_Users' internal names endpoint: the shared
secret goes in X-Internal-Service-Token; a user's token is never used and nothing else about the person travels.
"""
from __future__ import annotations

import logging
import re
from typing import Protocol
from uuid import UUID

import httpx

logger = logging.getLogger(__name__)

PHOTOS_PATH = "/internal/v1/accounts/photos"
BATCH_MAX = 100  # what Ms_Users accepts per request
# What Ms_Users generates; anything else in the answer is ignored.
_PHOTO_URL = re.compile(r"^/api/v1/users/photos/[0-9a-f]{32}\.(?:jpg|png)$")


class AccountPhotos(Protocol):
    def photos(self, ids: list[UUID]) -> dict[UUID, str]:
        """{account id: photo path} for the accounts that have a photo now. Raises if Ms_Users cannot answer."""
        ...


class InternalAccountPhotos:
    def __init__(self, client: httpx.Client, service_token: str | None) -> None:
        self._client = client
        self._token = service_token

    def photos(self, ids: list[UUID]) -> dict[UUID, str]:
        unique = list(dict.fromkeys(ids))
        if not unique:
            return {}
        if not self._token:
            logger.error("INTERNAL_SERVICE_TOKEN is not configured: candidate photos are not available")
            raise RuntimeError("service token not configured")
        found: dict[UUID, str] = {}
        for start in range(0, len(unique), BATCH_MAX):
            batch = unique[start : start + BATCH_MAX]
            response = self._client.post(
                PHOTOS_PATH,
                json={"ids": [str(item) for item in batch]},
                headers={"X-Internal-Service-Token": self._token},
            )
            if response.status_code != 200:
                raise RuntimeError(f"Ms_Users answered {response.status_code}")
            for key, value in dict(response.json().get("photos") or {}).items():
                try:
                    account = UUID(key)
                except ValueError:
                    continue
                if account in batch and isinstance(value, str) and _PHOTO_URL.fullmatch(value):
                    found[account] = value
        return found
