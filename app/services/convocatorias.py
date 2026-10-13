from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.schemas.club import ConvocatoriaResumen


class ConvocatoriasProvider(Protocol):
    """Source of a club's open calls. Convocatorias are owned by another
    service; a client for it implements this contract and is passed to
    create_app()."""

    def open_for_club(self, club_id: UUID) -> list[ConvocatoriaResumen]: ...


class NoConvocatorias:
    """Default until the convocatorias service exposes calls by club."""

    def open_for_club(self, club_id: UUID) -> list[ConvocatoriaResumen]:
        return []
