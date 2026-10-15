from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status

from app.core.security import Actor
from app.repositories.base import ClubRepository, StoredClub


def club_not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Club no encontrado.")


def require_managed_club(repository: ClubRepository, actor: Actor, club_id: UUID) -> StoredClub:
    """The club only its representative manages (HU-31/HU-32).

    The route guard (get_club_admin) already demands the club_admin role; it
    is checked again here so no route can skip it.
    """
    club = repository.get_club(club_id, include_inactive=True)
    if club is None:
        raise club_not_found()
    if actor.is_club_admin and club.usuario_id == actor.id:
        return club
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No administras este club.")
