from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import HTTPException, status

from app.core.security import Actor
from app.repositories.base import (
    CanalEliminado,
    ClubRepository,
    OutboxMessage,
    StoredCanal,
    StoredClub,
    StoredMensajeCanal,
    StoredMiembroCanal,
)
from app.schemas.canal import (
    MAX_CANALES,
    MAX_MIEMBROS,
    CanalClub,
    CanalCreate,
    CanalDeportista,
    CanalUpdate,
    DecisionInactividad,
    MensajeCanal,
    MensajesCanalClub,
    MensajesCanalDeportista,
    MiembroCanal,
)
from app.schemas.consulta import Lado, LeidoUpdate, MensajeCreate, MensajeFilters
from app.services.authorization import club_not_found

# A kept channel goes this long before the next inactivity notice (G4).
MANTENER = timedelta(days=30)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CanalService:
    """Channels of a club (G4): announcements to groups of its athletes.

    Administrators manage them and write signed as the club; members write
    only when miembros_escriben. Another club's channel, or a channel the
    athlete is not a member of, is a 404. Notices never carry the text.
    Reading messages and marking them read are one repository call each and
    never call Ms_Users (sección F).
    """

    def __init__(
        self, repository: ClubRepository, clock: Callable[[], datetime] = _utcnow
    ) -> None:
        self._repository = repository
        self._clock = clock

    # -- club side (administrators) ------------------------------------------------------

    def list_club(self, actor: Actor, club_id: UUID) -> list[CanalClub]:
        club = self._club(actor, club_id)
        return [_canal_club(c) for c in self._repository.list_canales_club(club.id)]

    def create(self, actor: Actor, club_id: UUID, payload: CanalCreate) -> CanalClub:
        club = self._club(actor, club_id, writing=True)
        result, canal = self._repository.create_canal(
            club.id, payload.nombre, payload.miembros_escriben,
            max_canales=MAX_CANALES, max_miembros=MAX_MIEMBROS,
        )
        if result == "sin_cupo" or canal is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"El club ya tiene {MAX_CANALES} canales.",
            )
        return _canal_club(canal)

    def update(self, actor: Actor, club_id: UUID, canal_id: UUID, payload: CanalUpdate) -> CanalClub:
        club = self._club(actor, club_id)
        canal = self._repository.update_canal(club.id, canal_id, payload.changes())
        if canal is None:
            raise canal_not_found()
        return _canal_club(canal)

    def delete(self, actor: Actor, club_id: UUID, canal_id: UUID) -> None:
        club = self._club(actor, club_id)
        if not self._repository.delete_canal(club.id, canal_id):
            raise canal_not_found()

    def members(self, actor: Actor, club_id: UUID, canal_id: UUID) -> list[MiembroCanal]:
        club = self._club(actor, club_id)
        return _miembros(self._repository.list_miembros_canal(club.id, canal_id))

    def candidates(self, actor: Actor, club_id: UUID, canal_id: UUID) -> list[MiembroCanal]:
        club = self._club(actor, club_id)
        return _miembros(self._repository.list_candidatos_canal(club.id, canal_id))

    def add_member(self, actor: Actor, club_id: UUID, canal_id: UUID, usuario_id: UUID) -> None:
        club = self._club(actor, club_id)
        result = self._repository.add_miembro_canal(
            club.id, canal_id, usuario_id, max_miembros=MAX_MIEMBROS
        )
        if result == "sin_canal":
            raise canal_not_found()
        if result == "no_postulante":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Postulante no encontrado."
            )
        if result == "sin_cupo":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"El canal ya tiene {MAX_MIEMBROS} miembros.",
            )

    def remove_member(self, actor: Actor, club_id: UUID, canal_id: UUID, usuario_id: UUID) -> None:
        club = self._club(actor, club_id)
        if not self._repository.remove_miembro_canal(club.id, canal_id, usuario_id):
            raise canal_not_found()

    def club_messages(
        self, actor: Actor, club_id: UUID, canal_id: UUID, filters: MensajeFilters
    ) -> MensajesCanalClub:
        canal, mensajes = self._repository.mensajes_canal_club(
            club_id, actor.id, canal_id,
            after_id=filters.after_id, before_id=filters.before_id, limit=filters.limit,
        )
        if canal is None or not actor.is_club_admin:
            raise canal_not_found()
        return MensajesCanalClub(
            canal=_canal_club(canal), mensajes=[_mensaje(m, "club", actor.id) for m in mensajes]
        )

    def send_as_club(
        self, actor: Actor, club_id: UUID, canal_id: UUID, payload: MensajeCreate
    ) -> MensajeCanal:
        club = self._club(actor, club_id, writing=True)
        canal = self._repository.get_canal_club(club.id, canal_id)
        if canal is None:
            raise canal_not_found()
        mensaje = self._send(canal, actor.id, "club", None, payload)
        return _mensaje(mensaje, "club", actor.id)

    def mark_read_as_club(
        self, actor: Actor, club_id: UUID, canal_id: UUID, payload: LeidoUpdate
    ) -> None:
        if not actor.is_club_admin or not self._repository.mark_read_canal_club(
            club_id, actor.id, canal_id, payload.hasta_id
        ):
            raise canal_not_found()

    def decide_inactivity(
        self, actor: Actor, club_id: UUID, canal_id: UUID, payload: DecisionInactividad
    ) -> CanalClub | None:
        """None when the channel was deleted (204); the kept channel otherwise."""
        club = self._club(actor, club_id)
        result = self._repository.decidir_inactividad(
            club.id, canal_id, payload.decision, self._clock() + MANTENER
        )
        if result == "sin_canal":
            raise canal_not_found()
        if result == "sin_aviso":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="El canal no tiene un aviso de inactividad pendiente.",
            )
        if result == "eliminado":
            return None
        canal = self._repository.get_canal_club(club.id, canal_id)
        if canal is None:
            raise canal_not_found()
        return _canal_club(canal)

    # -- athlete side (members) ------------------------------------------------------------

    def my_channels(self, actor: Actor) -> list[CanalDeportista]:
        return [
            _canal_deportista(c, actor.id)
            for c in self._repository.list_canales_deportista(actor.id)
        ]

    def my_messages(
        self, actor: Actor, canal_id: UUID, filters: MensajeFilters
    ) -> MensajesCanalDeportista:
        canal, mensajes = self._repository.mensajes_canal_deportista(
            canal_id, actor.id,
            after_id=filters.after_id, before_id=filters.before_id, limit=filters.limit,
        )
        if canal is None:
            raise canal_not_found()
        return MensajesCanalDeportista(
            canal=_canal_deportista(canal, actor.id),
            mensajes=[_mensaje(m, "deportista", actor.id) for m in mensajes],
        )

    def send_as_member(self, actor: Actor, canal_id: UUID, payload: MensajeCreate) -> MensajeCanal:
        canal = self._repository.get_canal_deportista(canal_id, actor.id)
        if canal is None:
            raise canal_not_found()
        if not canal.miembros_escriben:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="En este canal solo escribe el club.",
            )
        miembros = self._repository.list_miembros_canal(canal.club_id, canal.id) or []
        nombre = next((m.nombre for m in miembros if m.usuario_id == actor.id), None)
        if nombre is None:  # removed meanwhile
            raise canal_not_found()
        mensaje = self._send(canal, actor.id, "deportista", nombre, payload)
        return _mensaje(mensaje, "deportista", actor.id)

    def mark_read_as_member(self, actor: Actor, canal_id: UUID, payload: LeidoUpdate) -> None:
        if not self._repository.mark_read_canal_deportista(canal_id, actor.id, payload.hasta_id):
            raise canal_not_found()

    def mute(self, actor: Actor, canal_id: UUID, silenciado: bool) -> None:
        if not self._repository.set_silencio_canal(canal_id, actor.id, silenciado):
            raise canal_not_found()

    # -- internals ---------------------------------------------------------------------

    def _club(self, actor: Actor, club_id: UUID, *, writing: bool = False) -> StoredClub:
        """The club the actor administers; anything else is a 404 (G4). An
        inactive club's administrators read but do not create nor write."""
        club = self._repository.get_club(club_id, include_inactive=True)
        if (
            club is None or not actor.is_club_admin
            or self._repository.get_admin(club.id, actor.id) is None
        ):
            raise club_not_found()
        if writing and not club.is_active:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="El club está inactivo: no puede usar sus canales.",
            )
        return club

    def _send(
        self, canal: StoredCanal, autor_id: UUID, de: str, autor_nombre: str | None,
        payload: MensajeCreate,
    ) -> StoredMensajeCanal:
        try:
            mensaje, _ = self._repository.add_mensaje_canal(
                canal.id, autor_id, de, autor_nombre, payload.texto, payload.client_message_id,
                _aviso_mensaje(canal),
            )
        except CanalEliminado as error:
            raise canal_not_found() from error
        return mensaje


def canal_not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Canal no encontrado.")


def _miembros(miembros: list[StoredMiembroCanal] | None) -> list[MiembroCanal]:
    if miembros is None:
        raise canal_not_found()
    return [
        MiembroCanal(usuario_id=m.usuario_id, nombre=m.nombre, agregado_en=m.agregado_en)
        for m in miembros
    ]


def _mensaje(mensaje: StoredMensajeCanal, lado: Lado, lector: UUID) -> MensajeCanal:
    """propio: on the club side any administrator's message; on the athlete's, their own."""
    propio = mensaje.de == "club" if lado == "club" else mensaje.autor_id == lector
    return MensajeCanal(
        id=mensaje.id, de=mensaje.de, autor_nombre=mensaje.autor_nombre, texto=mensaje.texto,
        enviado_en=mensaje.enviado_en, propio=propio,
    )


def _canal_club(canal: StoredCanal) -> CanalClub:
    return CanalClub(
        id=canal.id, club_id=canal.club_id, nombre=canal.nombre,
        miembros_escriben=canal.miembros_escriben, miembros=canal.miembros,
        ultimo=_mensaje(canal.ultimo, "club", canal.club_id) if canal.ultimo else None,
        no_leidos=canal.no_leidos, aviso_inactividad_en=canal.aviso_inactividad_en,
        mantener_hasta=canal.mantener_hasta, fecha_creacion=canal.fecha_creacion,
    )


def _canal_deportista(canal: StoredCanal, lector: UUID) -> CanalDeportista:
    return CanalDeportista(
        id=canal.id, club_id=canal.club_id, club_nombre=canal.club_nombre,
        club_logo=canal.club_logo, nombre=canal.nombre,
        miembros_escriben=canal.miembros_escriben,
        ultimo=_mensaje(canal.ultimo, "deportista", lector) if canal.ultimo else None,
        no_leidos=canal.no_leidos, silenciado=canal.silenciado,
    )


# The notice may show on a locked screen: it never carries the message text.


def _aviso_mensaje(canal: StoredCanal) -> OutboxMessage:
    return OutboxMessage(
        kind="canal",
        title=canal.club_nombre,
        body=f"Nuevo mensaje en «{canal.nombre}».",
        data={"screen": "canal", "clubId": str(canal.club_id), "canalId": str(canal.id)},
    )
