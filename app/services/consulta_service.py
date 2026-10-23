from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status

from app.core.security import Actor
from app.repositories.base import (
    ClubRepository,
    OutboxMessage,
    StoredClub,
    StoredHilo,
    StoredMensaje,
)
from app.schemas.consulta import (
    HiloClub,
    HiloDeportista,
    Lado,
    LeidoUpdate,
    Mensaje,
    MensajeCreate,
    MensajeFilters,
    MensajesPage,
)
from app.services.authorization import club_not_found, require_managed_club
from app.services.users_client import ATHLETE_NAME_FALLBACK, AccountNames, account_name


class ConsultaService:
    """Consultas between an athlete and a club (HU-51).

    One thread per (club, athlete). On the club side it is a shared inbox: any
    administrator reads and answers, and the athlete sees the answers signed by
    the club, never by the person. The notifications never carry the text.
    """

    def __init__(self, repository: ClubRepository, names: AccountNames | None = None) -> None:
        self._repository = repository
        # Ms_Users, for the athlete's name snapshot when the thread is created.
        self._names = names

    # -- athlete side ------------------------------------------------------------------

    def my_threads(self, actor: Actor) -> list[HiloDeportista]:
        return [
            HiloDeportista(
                hilo_id=hilo.id,
                club_id=hilo.club_id,
                club_nombre=hilo.club_nombre,
                club_logo=hilo.club_logo,
                ultimo=_mensaje(hilo.ultimo, "deportista") if hilo.ultimo else None,
                no_leidos=hilo.no_leidos_deportista,
            )
            for hilo in self._repository.list_hilos_deportista(actor.id)
        ]

    def my_messages(self, actor: Actor, club_id: UUID, filters: MensajeFilters) -> MensajesPage:
        club = self._active_club(club_id)
        hilo = self._repository.get_hilo_deportista(club.id, actor.id)
        if hilo is None:
            return MensajesPage(hilo_id=None, mensajes=[])
        return self._page(hilo, "deportista", filters)

    def send_as_athlete(self, actor: Actor, club_id: UUID, payload: MensajeCreate) -> Mensaje:
        club = self._active_club(club_id)
        hilo = self._repository.get_hilo_deportista(club.id, actor.id)
        if hilo is None:
            nombre = account_name(self._names, actor, ATHLETE_NAME_FALLBACK)
            hilo = self._repository.ensure_hilo(club.id, actor.id, nombre)
        mensaje, _ = self._repository.add_mensaje(
            hilo.id, actor.id, "deportista", payload.texto, payload.client_message_id,
            _aviso_al_club(hilo),
        )
        return _mensaje(mensaje, "deportista")

    def mark_read_as_athlete(self, actor: Actor, club_id: UUID, payload: LeidoUpdate) -> None:
        club = self._active_club(club_id)
        hilo = self._repository.get_hilo_deportista(club.id, actor.id)
        if hilo is not None:
            self._repository.mark_read(hilo.id, "deportista", payload.hasta_id)

    # -- club side (administrators) ------------------------------------------------------

    def club_threads(self, actor: Actor, club_id: UUID) -> list[HiloClub]:
        club = require_managed_club(self._repository, actor, club_id)
        return [
            HiloClub(
                hilo_id=hilo.id,
                deportista_id=hilo.deportista_id,
                deportista_nombre=hilo.deportista_nombre,
                ultimo=_mensaje(hilo.ultimo, "club") if hilo.ultimo else None,
                no_leidos=hilo.no_leidos_club,
            )
            for hilo in self._repository.list_hilos_club(club.id)
        ]

    def club_messages(
        self, actor: Actor, club_id: UUID, hilo_id: UUID, filters: MensajeFilters
    ) -> MensajesPage:
        club = require_managed_club(self._repository, actor, club_id)
        return self._page(self._club_thread(club, hilo_id), "club", filters)

    def send_as_club(
        self, actor: Actor, club_id: UUID, hilo_id: UUID, payload: MensajeCreate
    ) -> Mensaje:
        club = require_managed_club(self._repository, actor, club_id)
        if not club.is_active:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="El club está inactivo: no puede responder consultas.",
            )
        hilo = self._club_thread(club, hilo_id)
        mensaje, _ = self._repository.add_mensaje(
            hilo.id, actor.id, "club", payload.texto, payload.client_message_id,
            _aviso_al_deportista(club, hilo),
        )
        return _mensaje(mensaje, "club")

    def mark_read_as_club(
        self, actor: Actor, club_id: UUID, hilo_id: UUID, payload: LeidoUpdate
    ) -> None:
        club = require_managed_club(self._repository, actor, club_id)
        hilo = self._club_thread(club, hilo_id)
        self._repository.mark_read(hilo.id, "club", payload.hasta_id)

    # -- internals ---------------------------------------------------------------------

    def _active_club(self, club_id: UUID) -> StoredClub:
        """Athletes only write to active clubs; an inactive one is a 404."""
        club = self._repository.get_club(club_id)
        if club is None:
            raise club_not_found()
        return club

    def _club_thread(self, club: StoredClub, hilo_id: UUID) -> StoredHilo:
        """A thread of that club; another club's thread is a 404, never a 403."""
        hilo = self._repository.get_hilo(hilo_id)
        if hilo is None or hilo.club_id != club.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Consulta no encontrada.")
        return hilo

    def _page(self, hilo: StoredHilo, lado: Lado, filters: MensajeFilters) -> MensajesPage:
        mensajes = self._repository.list_mensajes(
            hilo.id, after_id=filters.after_id, before_id=filters.before_id, limit=filters.limit
        )
        return MensajesPage(hilo_id=hilo.id, mensajes=[_mensaje(m, lado) for m in mensajes])


def _mensaje(mensaje: StoredMensaje, lado: Lado) -> Mensaje:
    """propio from the reader's side: on the club side, any administrator's message."""
    return Mensaje(
        id=mensaje.id,
        de=mensaje.de,
        texto=mensaje.texto,
        enviado_en=mensaje.enviado_en,
        propio=mensaje.de == lado,
    )


# The alert may show on a locked screen: it never carries the message text.


def _aviso_al_club(hilo: StoredHilo) -> OutboxMessage:
    return OutboxMessage(
        kind="consulta",
        title="Nueva consulta",
        body=f"{hilo.deportista_nombre} escribió a {hilo.club_nombre}.",
        data={"screen": "consulta_club", "clubId": str(hilo.club_id), "hiloId": str(hilo.id)},
    )


def _aviso_al_deportista(club: StoredClub, hilo: StoredHilo) -> OutboxMessage:
    return OutboxMessage(
        kind="consulta",
        title=f"Respuesta de {club.nombre}",
        body=f"{club.nombre} respondió tu consulta.",
        data={"screen": "consulta", "clubId": str(club.id), "hiloId": str(hilo.id)},
    )
