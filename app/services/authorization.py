from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status

from app.core.security import Actor
from app.repositories.base import ClubRepository, StoredAdmin, StoredClub


def club_not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Club no encontrado.")


def require_club_admin(
    repository: ClubRepository, actor: Actor, club_id: UUID
) -> tuple[StoredClub, StoredAdmin]:
    """The club and the actor's row in club_administrador. Any administrator
    (PRINCIPAL or ADMIN) manages the club; club.usuario_id is only who created it.

    The route guard (get_club_admin) already demands the club_admin role; it
    is checked again here so no route can skip it. The club's existence is
    public (directory), so another club's account gets a clear 403.
    """
    club = repository.get_club(club_id, include_inactive=True)
    if club is None:
        raise club_not_found()
    if actor.is_club_admin:
        admin = repository.get_admin(club.id, actor.id)
        if admin is not None:
            return club, admin
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No administras este club.")


def require_managed_club(repository: ClubRepository, actor: Actor, club_id: UUID) -> StoredClub:
    """The club an administrator manages (profile, sports, convocatorias,
    postulaciones, comunicados, consultas, invitations)."""
    return require_club_admin(repository, actor, club_id)[0]
