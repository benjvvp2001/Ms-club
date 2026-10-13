from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from app.repositories.base import ClubSport, StoredClub
from app.schemas.club import ClubFilters, fold_name


class MockClubRepository:
    """In-memory ClubRepository for tests and local runs without PostgreSQL."""

    def __init__(
        self, clubs: list[StoredClub] | None = None, sports: list[ClubSport] | None = None
    ) -> None:
        self._clubs: dict[UUID, StoredClub] = {}
        self._sports: dict[int, ClubSport] = {sport.id: sport for sport in sports or []}
        for club in clubs or []:
            self.add_club(club)

    def add_club(self, club: StoredClub) -> StoredClub:
        self._clubs[club.id] = club
        for sport in club.deportes:
            self._sports.setdefault(sport.id, sport)
        return club

    # -- directory ----------------------------------------------------------

    def search_clubs(self, filters: ClubFilters) -> tuple[list[StoredClub], int]:
        matches = [
            club for club in self._clubs.values() if club.is_active and _matches(club, filters)
        ]
        matches.sort(key=lambda club: (club.nombre.lower(), str(club.id)))
        start = (filters.page - 1) * filters.page_size
        page = matches[start : start + filters.page_size]
        return [_public(club) for club in page], len(matches)

    def get_club(self, club_id: UUID, *, include_inactive: bool = False) -> StoredClub | None:
        club = self._clubs.get(club_id)
        if club is None or not (club.is_active or include_inactive):
            return None
        return _public(club)

    def get_club_by_owner(self, usuario_id: UUID) -> StoredClub | None:
        for club in self._clubs.values():
            if club.usuario_id == usuario_id:
                return _public(club)
        return None

    # -- club administration (HU-31) ------------------------------------------

    def create_club(
        self, usuario_id: UUID, values: dict[str, Any], deporte_ids: list[int] | None = None
    ) -> StoredClub | None:
        if self.get_club_by_owner(usuario_id) is not None:
            return None
        now = datetime.now(timezone.utc)
        club = StoredClub(
            id=uuid4(), usuario_id=usuario_id, fecha_creacion=now, fecha_actualizacion=now,
            **values,
        )
        self.add_club(club)
        if deporte_ids:
            self._replace_sports(club, deporte_ids)
        return _public(club)

    def update_club(
        self, club_id: UUID, changes: dict[str, Any], deporte_ids: list[int] | None = None
    ) -> StoredClub:
        club = self._clubs[club_id]
        for column, value in changes.items():
            setattr(club, column, value)
        if deporte_ids is not None:
            self._replace_sports(club, deporte_ids)
        club.fecha_actualizacion = datetime.now(timezone.utc)
        return _public(club)

    def list_sports(self) -> list[ClubSport]:
        return sorted(
            (sport for sport in self._sports.values() if sport.is_active),
            key=lambda sport: (sport.nombre.lower(), sport.id),
        )

    def get_sport(self, deporte_id: int) -> ClubSport | None:
        sport = self._sports.get(deporte_id)
        return sport if sport is not None and sport.is_active else None

    def add_sport(self, club_id: UUID, deporte_id: int) -> None:
        club = self._clubs[club_id]
        if all(sport.id != deporte_id for sport in club.deportes):
            club.deportes.append(self._sports[deporte_id])

    def _replace_sports(self, club: StoredClub, deporte_ids: list[int]) -> None:
        kept_inactive = [sport for sport in club.deportes if not sport.is_active]
        club.deportes = kept_inactive + [self._sports[deporte_id] for deporte_id in deporte_ids]

    def remove_sport(self, club_id: UUID, deporte_id: int) -> None:
        club = self._clubs[club_id]
        club.deportes = [sport for sport in club.deportes if sport.id != deporte_id]


def _public(club: StoredClub) -> StoredClub:
    return replace(
        club,
        redes_sociales=dict(club.redes_sociales),
        deportes=sorted(
            (d for d in club.deportes if d.is_active), key=lambda d: (d.nombre.lower(), d.id)
        ),
    )


def _matches(club: StoredClub, filters: ClubFilters) -> bool:
    deportes = [d for d in club.deportes if d.is_active]
    if filters.nombre and filters.nombre.lower() not in club.nombre.lower():
        return False
    if filters.zona and not any(
        filters.zona.lower() in (place or "").lower()
        for place in (club.direccion, club.comuna, club.provincia)
    ):
        return False
    if filters.deporte_id and not any(d.id == filters.deporte_id for d in deportes):
        return False
    if filters.deporte and not any(
        fold_name(d.nombre) == fold_name(filters.deporte) for d in deportes
    ):
        return False
    return True
