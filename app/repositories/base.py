from __future__ import annotations

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
        Leaving RECHAZADO takes a cupo again: sin_cupos when none is free."""
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
