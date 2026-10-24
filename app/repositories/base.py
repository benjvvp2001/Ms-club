from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol
from uuid import UUID

from app.schemas.club import ClubFilters
from app.schemas.comunicado import ClubComunicadoFilters, ComunicadoFilters
from app.schemas.convocatoria import ConvocatoriaFilters
from app.schemas.postulacion import CandidatoFilters, PostulacionFilters


@dataclass(frozen=True)
class ClubSport:
    """A row of deporte linked to the club through club_deporte."""

    id: int
    nombre: str
    descripcion: str | None = None
    is_active: bool = True


@dataclass
class StoredClub:
    """A row of the club table (existing columns plus 001_club_profile_admin
    and 007's rut). usuario_id is who created it; who manages it is in
    club_administrador."""

    id: UUID
    nombre: str
    usuario_id: UUID | None = None
    rut: str | None = None
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


@dataclass(frozen=True)
class StoredAdmin:
    """A row of club_administrador (007): an account that manages a club."""

    club_id: UUID
    usuario_id: UUID
    rol: str  # PRINCIPAL | ADMIN
    nombre: str
    agregado_por: UUID | None = None
    fecha_alta: datetime | None = None


@dataclass(frozen=True)
class StoredAsignacion:
    """A row of club_admin_asignacion (009): an email the PRINCIPAL assigned to
    the club. Pending until an account with that email activates it."""

    id: UUID
    club_id: UUID
    email: str
    asignada_por: UUID
    fecha_creacion: datetime
    aceptada_por: UUID | None = None
    aceptada_en: datetime | None = None
    revocada_en: datetime | None = None


@dataclass
class StoredConvocatoria:
    """A row of convocatoria (002_convocatorias) with its club and sport.
    estado is computed by the repository at read time."""

    id: UUID
    club_id: UUID
    club_nombre: str
    club_logo: str | None
    deporte: ClubSport
    categoria_edad: str
    fecha_prueba: datetime
    requisitos: list[str]
    cupos: int
    direccion: str
    estado: str
    titulo: str | None = None
    descripcion: str | None = None
    edad_minima: int | None = None
    edad_maxima: int | None = None
    comuna: str | None = None
    cerrada_en: datetime | None = None
    fecha_creacion: datetime | None = None
    # Postulaciones that take a cupo (every estado except RECHAZADO).
    postulaciones_activas: int = 0


@dataclass
class StoredPostulacion:
    """A row of postulacion_convocatoria (003_postulaciones) with its convocatoria."""

    id: UUID
    convocatoria: StoredConvocatoria
    usuario_id: UUID
    estado: str
    perfil_deportivo: dict[str, Any]
    antecedentes: list[dict[str, Any]] = field(default_factory=list)
    mensaje: str | None = None
    comentario_club: str | None = None
    fecha_creacion: datetime | None = None
    fecha_actualizacion: datetime | None = None


@dataclass(frozen=True)
class OutboxEvent:
    """A row for clubs_api.notification_outbox, the contract the Ms_gateway
    worker reads (same as matching_api and activities_api)."""

    user_id: UUID
    kind: str
    title: str
    body: str
    data: dict[str, Any]


@dataclass(frozen=True)
class OutboxMessage:
    """Notification text shared by every recipient of a notice; the repository
    writes one clubs_api.notification_outbox row per athlete."""

    kind: str
    title: str
    body: str
    data: dict[str, Any]


@dataclass
class StoredComunicado:
    """A row of comunicado_club (005_comunicados) with its club."""

    id: UUID
    club_id: UUID
    club_nombre: str
    club_logo: str | None
    titulo: str
    contenido: str
    audiencia: str
    prioridad: str
    fecha_creacion: datetime
    # Athletes it was sent to.
    destinatarios: int = 0


@dataclass(frozen=True)
class StoredPlan:
    """A row of plan_suscripcion (006_pagos). precio is in whole Chilean pesos."""

    id: int
    nombre: str
    tipo: str  # USUARIO | CLUB
    precio: int
    duracion_dias: int
    descripcion: str | None = None
    is_active: bool = True


@dataclass
class StoredTransaccion:
    """A row of transaccion_pago with its plan."""

    id: UUID
    iniciada_por: UUID
    plan: StoredPlan
    buy_order: str
    session_id: str
    token_ws: str
    amount: int
    status: str  # PENDING | APPROVED | REJECTED | CANCELLED
    usuario_id: UUID | None = None
    club_id: UUID | None = None
    response_code: int | None = None
    authorization_code: str | None = None
    payment_type_code: str | None = None
    card_last4: str | None = None
    installments_number: int | None = None
    transaction_date: datetime | None = None
    fecha_creacion: datetime | None = None
    fecha_actualizacion: datetime | None = None


@dataclass
class StoredSuscripcion:
    """A row of suscripcion with its plan. estado is computed when read."""

    id: UUID
    plan: StoredPlan
    fecha_inicio: datetime
    fecha_fin: datetime
    estado: str  # ACTIVE | EXPIRED
    usuario_id: UUID | None = None
    club_id: UUID | None = None


@dataclass(frozen=True)
class StoredMensaje:
    """A row of consulta_mensaje (008). de: deportista | club."""

    id: int
    hilo_id: UUID
    autor_id: UUID
    de: str
    texto: str
    client_message_id: UUID
    enviado_en: datetime


@dataclass
class StoredHilo:
    """A row of consulta_hilo (008) with its club, its last message and how
    many messages each side has not read yet."""

    id: UUID
    club_id: UUID
    club_nombre: str
    club_logo: str | None
    deportista_id: UUID
    deportista_nombre: str
    leido_deportista_id: int = 0
    leido_club_id: int = 0
    fecha_creacion: datetime | None = None
    ultimo: StoredMensaje | None = None
    # Club messages the athlete has not read, and athlete messages the club has not read.
    no_leidos_deportista: int = 0
    no_leidos_club: int = 0


@dataclass(frozen=True)
class StoredMensajeCanal:
    """A row of club_canal_mensaje (010). autor_nombre is the club's name when
    de = club (read from club), the member's name snapshot otherwise."""

    id: int
    canal_id: UUID
    autor_id: UUID
    de: str
    autor_nombre: str
    texto: str
    client_message_id: UUID
    enviado_en: datetime


@dataclass
class StoredCanal:
    """A row of club_canal (010) as one reader sees it: the club (no_leidos =
    athletes' messages after the club's read mark) or a member (no_leidos =
    others' messages after their mark, silenciado = their own setting)."""

    id: UUID
    club_id: UUID
    club_nombre: str
    club_logo: str | None
    nombre: str
    miembros_escriben: bool
    fecha_creacion: datetime
    ultima_actividad: datetime
    aviso_inactividad_en: datetime | None = None
    mantener_hasta: datetime | None = None
    miembros: int = 0
    ultimo: StoredMensajeCanal | None = None
    no_leidos: int = 0
    silenciado: bool = False


@dataclass(frozen=True)
class StoredMiembroCanal:
    """A member of a channel, or a candidate (agregado_en None)."""

    usuario_id: UUID
    nombre: str
    agregado_en: datetime | None = None


@dataclass(frozen=True)
class CanalInactividad:
    """What the hourly inactivity check needs of a channel."""

    id: UUID
    club_id: UUID
    nombre: str
    ultima_actividad: datetime
    aviso_inactividad_en: datetime | None
    mantener_hasta: datetime | None


class HiloEliminado(Exception):
    """The thread was deleted (a rejected postulación) while writing to it."""


class CanalEliminado(Exception):
    """The channel was deleted while writing to it."""


# Outcome of creating a club; the checks run atomically with the inserts.
CreateClubResult = Literal["creado", "ya_administra", "rut_duplicado"]
# Outcome of assigning an email. duplicada: already pending in that club;
# ya_administra: an account that activated that email still administers the club.
AsignarResult = Literal["creada", "duplicada", "ya_administra", "sin_cupo"]
# Outcome of activating an assignment on GET /clubs/me.
ClaimResult = Literal["aceptada", "sin_asignacion", "ya_administra"]
# Who may read a club's thread: sin_club (no such club), no_admin (the account
# does not administer it), sin_hilo (not a thread of that club) or ok.
AccesoHilo = Literal["ok", "sin_club", "no_admin", "sin_hilo"]
# Who may read the athlete's side of a consulta (G3): sin_club (no such active
# club), no_postulante (no postulación other than RECHAZADO in the club) or ok.
AccesoDeportista = Literal["ok", "sin_club", "no_postulante"]
# Outcome of creating a channel; sin_cupo when the club already has the maximum.
CrearCanalResult = Literal["creado", "sin_cupo"]
# Outcome of adding a member to a channel.
AgregarMiembroResult = Literal["agregado", "ya_estaba", "sin_canal", "no_postulante", "sin_cupo"]
# Outcome of the club's answer to an inactivity notice.
DecisionInactividadResult = Literal["eliminado", "mantenido", "sin_aviso", "sin_canal"]
# Outcome of an application; every check runs atomically with the insert.
ApplyResult = Literal["creada", "no_encontrada", "no_abierta", "duplicada", "sin_cupos"]
# Outcome of a review. sin_cambios: same estado (the comment may still change).
ReviewResult = Literal["actualizada", "sin_cambios", "sin_cupos"]


class ClubRepository(Protocol):
    """Shape shared by the mock repository and the PostgreSQL adapter.

    Clubs always come with their active sports only.
    """

    def search_clubs(self, filters: ClubFilters) -> tuple[list[StoredClub], int]:
        """One page of active clubs ordered by name, plus the total that matches."""
        ...

    def get_club(self, club_id: UUID, *, include_inactive: bool = False) -> StoredClub | None: ...

    def get_club_by_admin(self, usuario_id: UUID) -> StoredClub | None:
        """The club that account administers (club_administrador), active or not."""
        ...

    def create_club(
        self,
        usuario_id: UUID,
        rut: str,
        nombre_admin: str,
        values: dict[str, Any],
        deporte_ids: list[int] | None = None,
    ) -> tuple[CreateClubResult, StoredClub | None]:
        """Insert the club (usuario_id kept as its creator) with its sports and
        the account as its PRINCIPAL administrator. ya_administra when the
        account already administers a club, rut_duplicado when another club has
        that RUT. The checks and the inserts are atomic."""
        ...

    # -- administradores y asignaciones por correo ----------------------------------

    def get_admin(self, club_id: UUID, usuario_id: UUID) -> StoredAdmin | None:
        """The account's row in club_administrador for that club."""
        ...

    def list_admins(self, club_id: UUID) -> list[StoredAdmin]:
        """The club's administrators, the PRINCIPAL first, then by fecha_alta."""
        ...

    def remove_admin(self, club_id: UUID, usuario_id: UUID) -> bool:
        """Delete an ADMIN of the club (never the PRINCIPAL); False when there was none."""
        ...

    def create_asignacion(
        self, club_id: UUID, email: str, asignada_por: UUID, max_admins: int
    ) -> tuple[AsignarResult, StoredAsignacion | None]:
        """Assign email to the club as a pending administrator. duplicada when
        it is already pending there; sin_cupo when the administrators plus the
        pending assignments already reach max_admins. The checks and the insert
        are atomic (the club's admins are locked while counting)."""
        ...

    def list_asignaciones(self, club_id: UUID) -> list[StoredAsignacion]:
        """Pending assignments of the club (not accepted nor revoked), newest first."""
        ...

    def revoke_asignacion(self, club_id: UUID, asignacion_id: UUID) -> bool:
        """Revoke a pending assignment of that club; False when there is none."""
        ...

    def has_asignacion_pendiente(self, email: str) -> bool:
        """Whether email has a pending assignment in an active club (a cheap
        check before asking Ms_Users for the name)."""
        ...

    def claim_asignacion(
        self, email: str, usuario_id: UUID, nombre: str, max_admins: int
    ) -> ClaimResult:
        """Activate the oldest pending assignment of email in an active club
        with room: add the account as ADMIN (agregado_por = asignada_por) and
        mark it accepted, atomically and only once (FOR UPDATE).
        ya_administra when the account already administers a club; then every
        assignment stays pending."""
        ...

    # -- consultas deportista <-> club (HU-51) ----------------------------------------

    def get_hilo(self, hilo_id: UUID) -> StoredHilo | None: ...

    def get_hilo_deportista(self, club_id: UUID, deportista_id: UUID) -> StoredHilo | None: ...

    def ensure_hilo(
        self, club_id: UUID, deportista_id: UUID, deportista_nombre: str
    ) -> StoredHilo | None:
        """The athlete's thread with the club, created if missing (a concurrent
        creation returns the same thread). None, and nothing created, when the
        athlete is not an active postulante of the club (checked atomically
        with a rejection, G3)."""
        ...

    def es_postulante_activo(self, club_id: UUID, usuario_id: UUID) -> bool:
        """Whether the account has a postulación other than RECHAZADO to a
        convocatoria of the club (G3)."""
        ...

    def nombre_postulante(self, club_id: UUID, usuario_id: UUID) -> str | None:
        """The name the club sees in the athlete's latest active postulación
        (perfil_deportivo: nombre and apellido_inicial); None when the account
        is not an active postulante of the club."""
        ...

    def list_hilos_deportista(self, deportista_id: UUID) -> list[StoredHilo]:
        """The athlete's threads with active clubs where they are an active
        postulante, latest message first."""
        ...

    def list_hilos_club(self, club_id: UUID) -> list[StoredHilo]:
        """The club's threads with active postulantes, latest message first."""
        ...

    def list_mensajes(
        self, hilo_id: UUID, *, after_id: int | None, before_id: int | None, limit: int
    ) -> list[StoredMensaje]:
        """Up to limit messages in ascending id: the first ones after after_id,
        otherwise the last ones before before_id (or the latest ones)."""
        ...

    def add_mensaje(
        self,
        hilo_id: UUID,
        autor_id: UUID,
        de: str,
        texto: str,
        client_message_id: UUID,
        aviso: OutboxMessage,
    ) -> tuple[StoredMensaje, bool]:
        """Insert the message, or return the one already sent with that
        client_message_id by that author (False). Only a new message writes
        aviso to the outbox, in the same transaction: to the athlete when the
        club writes, to every administrator of the club when the athlete does.
        Raises HiloEliminado when the thread no longer exists."""
        ...

    def mark_read(self, hilo_id: UUID, de: str, hasta_id: int) -> None:
        """Move that side's read mark forward to hasta_id (never back, never
        past the thread's last message). de is the side that read."""
        ...

    # Polling paths (sección F): one connection and the fewest queries each.

    def mensajes_deportista(
        self,
        club_id: UUID,
        deportista_id: UUID,
        *,
        after_id: int | None,
        before_id: int | None,
        limit: int,
    ) -> tuple[AccesoDeportista, UUID | None, list[StoredMensaje]]:
        """The athlete's page of messages with an active club where they are an
        active postulante: (acceso, thread id or None, messages as
        list_mensajes returns them; only when ok)."""
        ...

    def mensajes_club(
        self,
        club_id: UUID,
        usuario_id: UUID,
        hilo_id: UUID,
        *,
        after_id: int | None,
        before_id: int | None,
        limit: int,
    ) -> tuple[AccesoHilo, list[StoredMensaje]]:
        """A page of a club's thread read by usuario_id; messages only when ok.
        The club may be inactive (its administrators still read). A thread
        whose athlete is no longer an active postulante is sin_hilo."""
        ...

    def mark_read_deportista(
        self, club_id: UUID, deportista_id: UUID, hasta_id: int
    ) -> AccesoDeportista:
        """mark_read of the athlete's thread with an active club, if there is
        one; only applied when ok."""
        ...

    def mark_read_club(
        self, club_id: UUID, usuario_id: UUID, hilo_id: UUID, hasta_id: int
    ) -> AccesoHilo:
        """mark_read on the club's side; only applied when ok."""
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

    # -- convocatorias (HU-32) --------------------------------------------------
    # Only convocatorias of active clubs are ever returned.

    def create_convocatoria(self, club_id: UUID, values: dict[str, Any]) -> StoredConvocatoria: ...

    def get_convocatoria(
        self, convocatoria_id: UUID, *, include_inactive_clubs: bool = False
    ) -> StoredConvocatoria | None: ...

    def search_convocatorias(
        self, filters: ConvocatoriaFilters
    ) -> tuple[list[StoredConvocatoria], int]:
        """One page in the requested estado, plus the total that matches. Open
        and closed ones soonest first; finished ones most recent first."""
        ...

    def close_convocatoria(self, convocatoria_id: UUID) -> StoredConvocatoria:
        """Mark it closed now; a convocatoria already closed keeps its date."""
        ...

    # -- postulaciones (HU-33) ----------------------------------------------------

    def create_postulacion(
        self, convocatoria_id: UUID, usuario_id: UUID, values: dict[str, Any]
    ) -> tuple[ApplyResult, StoredPostulacion | None]:
        """Apply if the convocatoria is ABIERTA (and its club active), the
        athlete has not applied yet and a cupo is free. The convocatoria is
        locked while checking, so two athletes never take the last cupo."""
        ...

    def list_postulaciones(
        self, usuario_id: UUID, filters: PostulacionFilters
    ) -> tuple[list[StoredPostulacion], int]:
        """The athlete's postulaciones, newest first, including those to
        convocatorias of clubs deactivated since."""
        ...

    # -- revisión de candidatos (HU-34) -----------------------------------------------

    def list_candidatos(
        self, convocatoria_id: UUID, filters: CandidatoFilters
    ) -> tuple[list[StoredPostulacion], int, dict[str, int]]:
        """One page of the convocatoria's postulaciones, oldest first, the total
        that matches the filter, and how many there are per estado overall."""
        ...

    def get_postulacion(self, postulacion_id: UUID) -> StoredPostulacion | None:
        """Any postulación, whatever the state of its convocatoria or club."""
        ...

    def review_postulacion(
        self,
        postulacion_id: UUID,
        estado: str,
        *,
        comentario: str | None,
        set_comentario: bool,
        evento: OutboxEvent,
    ) -> tuple[ReviewResult, StoredPostulacion]:
        """Set the estado (and the comment when set_comentario) and, only if the
        estado changed, write evento to the outbox in the same transaction.
        Leaving RECHAZADO takes a cupo again: sin_cupos when none is free.
        When it becomes RECHAZADO and the athlete has no other active
        postulación in the club, their consulta thread (with its messages) and
        their channel memberships in the club are deleted in the same
        transaction (G3)."""
        ...

    # -- canales del club (G4) ----------------------------------------------------------
    # Channels of a club seen by its administrators; a channel of another club
    # is the same as a missing one (None / sin_canal).

    def list_canales_club(self, club_id: UUID) -> list[StoredCanal]:
        """The club's channels, latest activity first."""
        ...

    def get_canal_club(self, club_id: UUID, canal_id: UUID) -> StoredCanal | None: ...

    def create_canal(
        self,
        club_id: UUID,
        nombre: str,
        miembros_escriben: bool,
        *,
        max_canales: int,
        max_miembros: int,
    ) -> tuple[CrearCanalResult, StoredCanal | None]:
        """Create the channel with the club's athletes that have an ACEPTADO
        postulación as members (up to max_miembros). sin_cupo when the club
        already has max_canales; the count and the insert are atomic."""
        ...

    def update_canal(
        self, club_id: UUID, canal_id: UUID, changes: dict[str, Any]
    ) -> StoredCanal | None:
        """Apply nombre and/or miembros_escriben."""
        ...

    def delete_canal(self, club_id: UUID, canal_id: UUID) -> bool:
        """Delete the channel with its members and messages."""
        ...

    def list_miembros_canal(
        self, club_id: UUID, canal_id: UUID
    ) -> list[StoredMiembroCanal] | None:
        """The members by name; None when there is no such channel."""
        ...

    def list_candidatos_canal(
        self, club_id: UUID, canal_id: UUID
    ) -> list[StoredMiembroCanal] | None:
        """Active postulantes of the club not in the channel, by name."""
        ...

    def add_miembro_canal(
        self, club_id: UUID, canal_id: UUID, usuario_id: UUID, *, max_miembros: int
    ) -> AgregarMiembroResult:
        """Add an active postulante of the club (name from their postulación);
        idempotent. The checks and the insert are atomic."""
        ...

    def remove_miembro_canal(self, club_id: UUID, canal_id: UUID, usuario_id: UUID) -> bool:
        """Idempotent; False only when there is no such channel."""
        ...

    def add_mensaje_canal(
        self,
        canal_id: UUID,
        autor_id: UUID,
        de: str,
        autor_nombre: str | None,
        texto: str,
        client_message_id: UUID,
        aviso: OutboxMessage,
    ) -> tuple[StoredMensajeCanal, bool]:
        """Insert the message, or return the one already sent with that
        client_message_id by that author (False). A new message moves
        ultima_actividad, cancels a pending inactivity notice (and
        mantener_hasta) and writes aviso to the outbox for every member that
        did not mute the channel (except the author), in the same transaction.
        Raises CanalEliminado when the channel no longer exists."""
        ...

    def decidir_inactividad(
        self, club_id: UUID, canal_id: UUID, decision: str, mantener_hasta: datetime
    ) -> DecisionInactividadResult:
        """The club's answer to a pending notice: eliminar deletes the channel;
        mantener clears the notice and sets mantener_hasta. sin_aviso when no
        notice is pending."""
        ...

    # Polling paths of channels (sección F): one connection, one statement each.

    def mensajes_canal_club(
        self,
        club_id: UUID,
        usuario_id: UUID,
        canal_id: UUID,
        *,
        after_id: int | None,
        before_id: int | None,
        limit: int,
    ) -> tuple[StoredCanal | None, list[StoredMensajeCanal]]:
        """The channel (club view) and a page of its messages, when usuario_id
        administers the club and the channel is the club's; (None, []) otherwise."""
        ...

    def mark_read_canal_club(
        self, club_id: UUID, usuario_id: UUID, canal_id: UUID, hasta_id: int
    ) -> bool:
        """Move the club's read mark forward; False when no access."""
        ...

    def list_canales_deportista(self, usuario_id: UUID) -> list[StoredCanal]:
        """The member's channels of active clubs, latest activity first."""
        ...

    def get_canal_deportista(self, canal_id: UUID, usuario_id: UUID) -> StoredCanal | None:
        """The channel (member view) when usuario_id is a member and the club is active."""
        ...

    def mensajes_canal_deportista(
        self,
        canal_id: UUID,
        usuario_id: UUID,
        *,
        after_id: int | None,
        before_id: int | None,
        limit: int,
    ) -> tuple[StoredCanal | None, list[StoredMensajeCanal]]:
        """Like mensajes_canal_club for a member of a channel of an active club."""
        ...

    def mark_read_canal_deportista(self, canal_id: UUID, usuario_id: UUID, hasta_id: int) -> bool:
        """False when the account is not a member (or the club is inactive)."""
        ...

    def set_silencio_canal(self, canal_id: UUID, usuario_id: UUID, silenciado: bool) -> bool:
        """False when the account is not a member (or the club is inactive)."""
        ...

    # Hourly inactivity check (G4).

    def inactividad_lock(self) -> AbstractContextManager[bool]:
        """Context holding pg_try_advisory_lock: True only for the one
        instance that got it."""
        ...

    def canales_por_revisar(self, inactivo_desde: datetime) -> list[CanalInactividad]:
        """Channels with a pending notice or no activity since inactivo_desde."""
        ...

    def avisar_inactividad(
        self, canal: CanalInactividad, ahora: datetime, aviso: OutboxMessage
    ) -> bool:
        """Stamp aviso_inactividad_en = ahora and notify every administrator of
        the club, only if the channel is still as read (no message since)."""
        ...

    def eliminar_canal_inactivo(self, canal: CanalInactividad) -> bool:
        """Delete the channel only if its pending notice is still the one read."""
        ...

    # -- comunicados de clubes (HU-38) ------------------------------------------------

    def create_comunicado(
        self, club_id: UUID, values: dict[str, Any], mensaje: OutboxMessage
    ) -> StoredComunicado:
        """Insert the notice (values has its id), resolve the audience from the
        postulaciones of the club's convocatorias, record the recipients and
        write one outbox row per recipient, all in one transaction."""
        ...

    def list_comunicados_club(
        self, club_id: UUID, filters: ClubComunicadoFilters
    ) -> tuple[list[StoredComunicado], int]:
        """The notices a club published, newest first."""
        ...

    def list_comunicados_usuario(
        self, usuario_id: UUID, filters: ComunicadoFilters
    ) -> tuple[list[StoredComunicado], int]:
        """The notices addressed to the athlete, newest first."""
        ...

    # -- pagos con Webpay Plus (HU-40) -----------------------------------------------

    def list_planes(self, tipo: str | None = None) -> list[StoredPlan]:
        """Active plans, cheapest first; optionally only one tipo."""
        ...

    def get_plan(self, plan_id: int) -> StoredPlan | None:
        """An active plan."""
        ...

    def get_suscripcion(
        self, *, usuario_id: UUID | None = None, club_id: UUID | None = None
    ) -> StoredSuscripcion | None:
        """The subscription of an athlete or of a club, active or expired."""
        ...

    def create_transaccion(self, values: dict[str, Any]) -> StoredTransaccion:
        """Insert a PENDING payment (values: iniciada_por, usuario_id or club_id,
        plan_id, buy_order, session_id, token_ws, amount)."""
        ...

    def get_transaccion(
        self, *, token_ws: str | None = None, buy_order: str | None = None
    ) -> StoredTransaccion | None: ...

    def settle_transaccion(
        self, transaccion_id: UUID, status: str, resultado: dict[str, Any] | None
    ) -> tuple[StoredTransaccion, bool, StoredSuscripcion | None]:
        """Move a PENDING payment to APPROVED, REJECTED or CANCELLED, storing the
        Transbank result. Only the call that really changes it returns True:
        when APPROVED it also creates or extends the buyer's subscription by the
        plan's duration, in the same transaction, exactly once. A payment that
        is no longer PENDING is returned untouched with False."""
        ...
