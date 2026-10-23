from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status

from app.core.security import Actor
from app.repositories.base import (
    AccesoHilo,
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
from app.services.authorization import club_not_found, not_club_admin, require_managed_club
from app.services.users_client import ATHLETE_NAME_FALLBACK, AccountNames, account_name


class ConsultaService:
    """Consultas between an athlete and a club (HU-51).

    One thread per (club, athlete). On the club side it is a shared inbox: any
    administrator reads and answers, and the athlete sees the answers signed by
    the club, never by the person. The notifications never carry the text.

    Reading messages and marking them read are what the app polls (sección F):
    each is one repository call (one connection, one statement) and never
    calls Ms_Users.
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
        # hilo_id is None until the athlete writes for the first time.
        found, hilo_id, mensajes = self._repository.mensajes_deportista(
            club_id, actor.id,
            after_id=filters.after_id, before_id=filters.before_id, limit=filters.limit,
        )
        if not found:
            raise club_not_found()
        return MensajesPage(hilo_id=hilo_id, mensajes=[_mensaje(m, "deportista") for m in mensajes])

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
        if not self._repository.mark_read_deportista(club_id, actor.id, payload.hasta_id):
            raise club_not_found()

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
        if not actor.is_club_admin:
            require_managed_club(self._repository, actor, club_id)  # raises 404 or 403
        acceso, mensajes = self._repository.mensajes_club(
            club_id, actor.id, hilo_id,
            after_id=filters.after_id, before_id=filters.before_id, limit=filters.limit,
        )
        _check_acceso(acceso)
        return MensajesPage(hilo_id=hilo_id, mensajes=[_mensaje(m, "club") for m in mensajes])

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
        if not actor.is_club_admin:
            require_managed_club(self._repository, actor, club_id)  # raises 404 or 403
        _check_acceso(
            self._repository.mark_read_club(club_id, actor.id, hilo_id, payload.hasta_id)
        )

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
            raise _thread_not_found()
        return hilo


def _thread_not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Consulta no encontrada.")


def _check_acceso(acceso: AccesoHilo) -> None:
    """Same answers as require_managed_club followed by _club_thread."""
    if acceso == "sin_club":
        raise club_not_found()
    if acceso == "no_admin":
        raise not_club_admin()
    if acceso == "sin_hilo":
        raise _thread_not_found()


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
