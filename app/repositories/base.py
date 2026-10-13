from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from app.schemas.club import ClubFilters


@dataclass(frozen=True)
class ClubSport:
    """A row of deporte linked to the club through club_deporte."""

    id: int
    nombre: str
    descripcion: str | None = None
    is_active: bool = True


@dataclass
class StoredClub:
    """A row of the club table (existing columns plus 001_club_profile_admin)."""

    id: UUID
    nombre: str
    usuario_id: UUID | None = None
    descripcion: str | None = None
    historia: str | None = None
    email_contacto: str | None = None
    telefono: str | None = None
    sitio_web: str | None = None
    direccion: str | None = None
    comuna: str | None = None
    provincia: str | None = None
    latitud: float | None = None
    longitud: float | None = None
    logo: str | None = None
    portada: str | None = None
    redes_sociales: dict[str, str] = field(default_factory=dict)
    estado_verificacion: str = "pendiente"
    is_active: bool = True
    fecha_creacion: datetime | None = None
    fecha_actualizacion: datetime | None = None
    deportes: list[ClubSport] = field(default_factory=list)


class ClubRepository(Protocol):
    """Shape shared by the mock repository and the PostgreSQL adapter.

    Clubs always come with their active sports only.
    """

    def search_clubs(self, filters: ClubFilters) -> tuple[list[StoredClub], int]:
        """One page of active clubs ordered by name, plus the total that matches."""
        ...

    def get_club(self, club_id: UUID, *, include_inactive: bool = False) -> StoredClub | None: ...

    def get_club_by_owner(self, usuario_id: UUID) -> StoredClub | None:
        """The club managed by that account, active or not."""
        ...

    def create_club(
        self, usuario_id: UUID, values: dict[str, Any], deporte_ids: list[int] | None = None
    ) -> StoredClub | None:
        """Insert the club with its sports; None when that account already
        manages one. The check and the inserts are atomic."""
        ...

    def update_club(
        self, club_id: UUID, changes: dict[str, Any], deporte_ids: list[int] | None = None
    ) -> StoredClub:
        """Apply the column values, replace the active sports when deporte_ids
        is given, and stamp fecha_actualizacion, all in one transaction."""
        ...

    def list_sports(self) -> list[ClubSport]:
        """Active sports of the catalog, ordered by name."""
        ...

    def get_sport(self, deporte_id: int) -> ClubSport | None:
        """An active sport of the catalog."""
        ...

    def add_sport(self, club_id: UUID, deporte_id: int) -> None:
        """Idempotent: adding a sport twice keeps one link."""
        ...

    def remove_sport(self, club_id: UUID, deporte_id: int) -> None:
        """Idempotent: removing a sport the club does not have is not an error."""
        ...
