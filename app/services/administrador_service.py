from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status

from app.core.security import Actor
from app.repositories.base import ClubRepository, StoredAdmin, StoredAsignacion
from app.schemas.administrador import AdminRead, AsignacionRead, normalizar_email
from app.services.authorization import require_club_admin


class AdministradorService:
    """Several administrators per club (sprint clubes y chats, secciones A y E).

    Any administrator lists the admins and the pending assignments. Only the
    PRINCIPAL assigns emails, revokes pending assignments and removes another
    administrator; an ADMIN may leave on their own; the PRINCIPAL can neither
    be removed nor leave (handing it over is pending).

    An assignment stays pending until a club_admin account with that verified
    email opens GET /clubs/me (ClubService.my_club). The answers never say
    whether an account with that email exists.
    """

    def __init__(self, repository: ClubRepository, max_admins: int) -> None:
        self._repository = repository
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

    # -- asignaciones por correo (sección E) ------------------------------------------

    def create_asignacion(self, actor: Actor, club_id: UUID, email: str) -> AsignacionRead:
        club, yo = require_club_admin(self._repository, actor, club_id)
        if yo.rol != "PRINCIPAL":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Solo el administrador principal puede asignar administradores.",
            )
        normalizado = normalizar_email(email)
        if normalizado is None:
            raise HTTPException(
                status_code=422,  # the contract asks 422 (other input errors here are 400)
                detail="Correo inválido: escribe un correo como nombre@dominio.cl.",
            )
        email = normalizado
        if actor.email is not None and email == actor.email:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Ese es tu propio correo: ya administras este club.",
            )
        result, asignacion = self._repository.create_asignacion(
            club.id, email, actor.id, self._max_admins
        )
        if result == "duplicada":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Ese correo ya tiene una asignación pendiente en este club.",
            )
        if result == "ya_administra":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Ese correo ya administra este club.",
            )
        if result == "sin_cupo":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"El club ya tiene el máximo de {self._max_admins} administradores "
                "(contando las asignaciones pendientes).",
            )
        assert asignacion is not None
        return _asignacion_read(asignacion)

    def list_asignaciones(self, actor: Actor, club_id: UUID) -> list[AsignacionRead]:
        club, _ = require_club_admin(self._repository, actor, club_id)
        return [_asignacion_read(a) for a in self._repository.list_asignaciones(club.id)]

    def revoke_asignacion(self, actor: Actor, club_id: UUID, asignacion_id: UUID) -> None:
        club, yo = require_club_admin(self._repository, actor, club_id)
        if yo.rol != "PRINCIPAL":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Solo el administrador principal puede revocar asignaciones.",
            )
        if not self._repository.revoke_asignacion(club.id, asignacion_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Asignación no encontrada o ya no está pendiente.",
            )


def _admin_read(admin: StoredAdmin) -> AdminRead:
    return AdminRead(
        usuario_id=admin.usuario_id, nombre=admin.nombre, rol=admin.rol, fecha_alta=admin.fecha_alta
    )


def _asignacion_read(asignacion: StoredAsignacion) -> AsignacionRead:
    return AsignacionRead(
        id=asignacion.id,
        email=asignacion.email,
        asignada_por=asignacion.asignada_por,
        fecha_creacion=asignacion.fecha_creacion,
    )
