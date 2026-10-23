from __future__ import annotations

import hashlib
import secrets
from uuid import UUID

from fastapi import HTTPException, status

from app.core.security import Actor
from app.repositories.base import ClubRepository, StoredAdmin, StoredInvitacion
from app.schemas.administrador import AdminRead, InvitacionCreada, InvitacionRead
from app.schemas.club import ClubManaged
from app.services.authorization import require_club_admin
from app.services.club_service import to_managed
from app.services.users_client import ADMIN_NAME_FALLBACK, AccountNames, account_name

# Upper case letters and digits without the ambiguous ones (0/O, 1/I/L), so a
# code read aloud or copied by hand still works. 31^10 ≈ 8 * 10^14 codes.
CODIGO_ALFABETO = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODIGO_LARGO = 10
INVITACION_HORAS = 72


def generar_codigo() -> str:
    return "".join(secrets.choice(CODIGO_ALFABETO) for _ in range(CODIGO_LARGO))


def hash_codigo(codigo: str) -> str:
    """SHA-256 of the normalized code: the only thing stored. Spaces and dashes
    are ignored and letters compared in upper case."""
    normalizado = "".join(codigo.split()).replace("-", "").upper()
    return hashlib.sha256(normalizado.encode("utf-8")).hexdigest()


class AdministradorService:
    """Several administrators per club (sprint clubes y chats, sección A).

    Any administrator lists admins and manages invitations. Only the PRINCIPAL
    removes another administrator; an ADMIN may leave on their own; the
    PRINCIPAL can neither be removed nor leave (handing it over is pending).
    """

    def __init__(
        self, repository: ClubRepository, names: AccountNames | None, max_admins: int
    ) -> None:
        self._repository = repository
        self._names = names
        self._max_admins = max_admins

    def list_admins(self, actor: Actor, club_id: UUID) -> list[AdminRead]:
        club, _ = require_club_admin(self._repository, actor, club_id)
        return [_admin_read(admin) for admin in self._repository.list_admins(club.id)]

    def remove_admin(self, actor: Actor, club_id: UUID, usuario_id: UUID) -> None:
        club, yo = require_club_admin(self._repository, actor, club_id)
        if usuario_id == actor.id:
            if yo.rol == "PRINCIPAL":
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="El administrador principal no puede salir del club.",
                )
        elif yo.rol != "PRINCIPAL":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Solo el administrador principal puede quitar administradores.",
            )
        objetivo = self._repository.get_admin(club.id, usuario_id)
        if objetivo is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Administrador no encontrado."
            )
        if objetivo.rol == "PRINCIPAL":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="El administrador principal no se puede quitar.",
            )
        # Removed in between by someone else: the result is the same.
        self._repository.remove_admin(club.id, usuario_id)

    def create_invitacion(self, actor: Actor, club_id: UUID) -> InvitacionCreada:
        club, _ = require_club_admin(self._repository, actor, club_id)
        codigo = generar_codigo()
        invitacion = self._repository.create_invitacion(
            club.id, actor.id, hash_codigo(codigo), INVITACION_HORAS, self._max_admins
        )
        if invitacion is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"El club ya tiene el máximo de {self._max_admins} administradores "
                "(contando las invitaciones vigentes).",
            )
        return InvitacionCreada(id=invitacion.id, codigo=codigo, expira_en=invitacion.expira_en)

    def list_invitaciones(self, actor: Actor, club_id: UUID) -> list[InvitacionRead]:
        club, _ = require_club_admin(self._repository, actor, club_id)
        return [_invitacion_read(i) for i in self._repository.list_invitaciones(club.id)]

    def revoke_invitacion(self, actor: Actor, club_id: UUID, invitacion_id: UUID) -> None:
        club, _ = require_club_admin(self._repository, actor, club_id)
        if not self._repository.revoke_invitacion(club.id, invitacion_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Invitación no encontrada."
            )

    def accept_invitacion(self, actor: Actor, codigo: str) -> ClubManaged:
        if self._repository.get_club_by_admin(actor.id) is not None:
            raise _already_admin()
        nombre = account_name(self._names, actor, ADMIN_NAME_FALLBACK)
        result, club = self._repository.accept_invitacion(
            hash_codigo(codigo), actor.id, nombre, self._max_admins
        )
        if result == "no_encontrada":
            # Same answer for an unknown, expired, used or revoked code.
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Código de invitación inválido o vencido.",
            )
        if result == "ya_administra":
            raise _already_admin()
        if result == "sin_cupo":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="El club ya tiene el máximo de administradores.",
            )
        assert club is not None
        return to_managed(club)


def _already_admin() -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Ya administras un club.")


def _admin_read(admin: StoredAdmin) -> AdminRead:
    return AdminRead(
        usuario_id=admin.usuario_id, nombre=admin.nombre, rol=admin.rol, fecha_alta=admin.fecha_alta
    )


def _invitacion_read(invitacion: StoredInvitacion) -> InvitacionRead:
    return InvitacionRead(
        id=invitacion.id,
        creada_por=invitacion.creada_por,
        fecha_creacion=invitacion.fecha_creacion,
        expira_en=invitacion.expira_en,
    )
