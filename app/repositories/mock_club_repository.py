from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

from zoneinfo import ZoneInfo

from app.repositories.base import (
    ApplyResult,
    ClubSport,
    OutboxEvent,
    OutboxMessage,
    ReviewResult,
    StoredClub,
    StoredComunicado,
    StoredConvocatoria,
    StoredPlan,
    StoredPostulacion,
    StoredSuscripcion,
    StoredTransaccion,
)
from app.schemas.club import ClubFilters
from app.schemas.common import fold_name
from app.schemas.comunicado import (
    ESTADOS_POR_AUDIENCIA,
    ClubComunicadoFilters,
    ComunicadoFilters,
)
from app.schemas.convocatoria import ConvocatoriaFilters
from app.schemas.postulacion import CandidatoFilters, PostulacionFilters

CHILE = ZoneInfo("America/Santiago")


class MockClubRepository:
    """In-memory ClubRepository for tests and local runs without PostgreSQL."""

    def __init__(
        self,
        clubs: list[StoredClub] | None = None,
        sports: list[ClubSport] | None = None,
        planes: list[StoredPlan] | None = None,
    ) -> None:
        self._clubs: dict[UUID, StoredClub] = {}
        self._sports: dict[int, ClubSport] = {sport.id: sport for sport in sports or []}
        self._convocatorias: dict[UUID, dict[str, Any]] = {}
        self._postulaciones: dict[UUID, dict[str, Any]] = {}
        # Events the Ms_gateway worker would read from clubs_api.notification_outbox.
        self.outbox: list[OutboxEvent] = []
        self._last_tick: datetime | None = None
        self._planes: dict[int, StoredPlan] = {plan.id: plan for plan in planes or []}
        self._transacciones: dict[UUID, StoredTransaccion] = {}
        self._suscripciones: dict[tuple[str, UUID], dict[str, Any]] = {}
        self._comunicados: dict[UUID, StoredComunicado] = {}
        self._destinatarios: dict[UUID, set[UUID]] = {}
        for club in clubs or []:
            self.add_club(club)

    def _tick(self) -> datetime:
        """Now, strictly increasing: the system clock can repeat the same value
        (Windows) and the lists are ordered by these stamps. PostgreSQL's now()
        has microsecond precision per transaction and needs no such help."""
        now = datetime.now(timezone.utc)
        if self._last_tick is not None and now <= self._last_tick:
            now = self._last_tick + timedelta(microseconds=1)
        self._last_tick = now
        return now

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
        now = self._tick()
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
        club.fecha_actualizacion = self._tick()
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


    # -- convocatorias (HU-32) --------------------------------------------------

    def create_convocatoria(self, club_id: UUID, values: dict[str, Any]) -> StoredConvocatoria:
        convocatoria_id = uuid4()
        self._convocatorias[convocatoria_id] = {
            **values, "id": convocatoria_id, "club_id": club_id, "cerrada_en": None,
            "fecha_creacion": self._tick(),
        }
        convocatoria = self.get_convocatoria(convocatoria_id)
        assert convocatoria is not None
        return convocatoria

    def get_convocatoria(
        self, convocatoria_id: UUID, *, include_inactive_clubs: bool = False
    ) -> StoredConvocatoria | None:
        row = self._convocatorias.get(convocatoria_id)
        if row is None:
            return None
        return self._stored(row, active_clubs_only=not include_inactive_clubs)

    def search_convocatorias(
        self, filters: ConvocatoriaFilters
    ) -> tuple[list[StoredConvocatoria], int]:
        matches = [
            convocatoria
            for convocatoria in map(self._stored, self._convocatorias.values())
            if convocatoria is not None and _call_matches(convocatoria, filters)
        ]
        matches.sort(
            key=lambda c: (c.fecha_prueba, str(c.id)), reverse=filters.estado == "FINALIZADA"
        )
        start = (filters.page - 1) * filters.page_size
        return matches[start : start + filters.page_size], len(matches)

    def close_convocatoria(self, convocatoria_id: UUID) -> StoredConvocatoria:
        row = self._convocatorias[convocatoria_id]
        if row["cerrada_en"] is None:
            row["cerrada_en"] = self._tick()
        convocatoria = self._stored(row)
        assert convocatoria is not None
        return convocatoria

    def _stored(
        self, row: dict[str, Any], *, active_clubs_only: bool = True
    ) -> StoredConvocatoria | None:
        club = self._clubs.get(row["club_id"])
        if club is None or (active_clubs_only and not club.is_active):
            return None
        values = {key: value for key, value in row.items() if key != "deporte_id"}
        return StoredConvocatoria(
            **values,
            club_nombre=club.nombre,
            club_logo=club.logo,
            deporte=self._sports[row["deporte_id"]],
            estado=_estado(row["fecha_prueba"], row["cerrada_en"]),
            postulaciones_activas=sum(
                1
                for postulacion in self._postulaciones.values()
                if postulacion["convocatoria_id"] == row["id"] and postulacion["estado"] != "RECHAZADO"
            ),
        )

    # -- postulaciones (HU-33) ----------------------------------------------------

    def create_postulacion(
        self, convocatoria_id: UUID, usuario_id: UUID, values: dict[str, Any]
    ) -> tuple[ApplyResult, StoredPostulacion | None]:
        row = self._convocatorias.get(convocatoria_id)
        convocatoria = self._stored(row) if row is not None else None
        if convocatoria is None:
            return "no_encontrada", None
        if convocatoria.estado != "ABIERTA":
            return "no_abierta", None
        if any(
            p["convocatoria_id"] == convocatoria_id and p["usuario_id"] == usuario_id
            for p in self._postulaciones.values()
        ):
            return "duplicada", None
        if convocatoria.postulaciones_activas >= convocatoria.cupos:
            return "sin_cupos", None
        now = self._tick()
        postulacion = {
            "id": uuid4(), "convocatoria_id": convocatoria_id, "usuario_id": usuario_id,
            "estado": "PENDIENTE", "mensaje": values.get("mensaje"),
            "antecedentes": list(values.get("antecedentes", [])),
            "perfil_deportivo": dict(values["perfil_deportivo"]), "comentario_club": None,
            "fecha_creacion": now, "fecha_actualizacion": now,
        }
        self._postulaciones[postulacion["id"]] = postulacion
        return "creada", self._stored_postulacion(postulacion)

    def list_postulaciones(
        self, usuario_id: UUID, filters: PostulacionFilters
    ) -> tuple[list[StoredPostulacion], int]:
        matches = sorted(
            (
                p for p in self._postulaciones.values()
                if p["usuario_id"] == usuario_id and filters.estado in (None, p["estado"])
            ),
            key=lambda p: (p["fecha_creacion"], str(p["id"])),
            reverse=True,
        )
        start = (filters.page - 1) * filters.page_size
        page = matches[start : start + filters.page_size]
        return [self._stored_postulacion(p) for p in page], len(matches)

    # -- revisión de candidatos (HU-34) -----------------------------------------------

    def list_candidatos(
        self, convocatoria_id: UUID, filters: CandidatoFilters
    ) -> tuple[list[StoredPostulacion], int, dict[str, int]]:
        todas = sorted(
            (p for p in self._postulaciones.values() if p["convocatoria_id"] == convocatoria_id),
            key=lambda p: (p["fecha_creacion"], str(p["id"])),
        )
        conteo: dict[str, int] = {}
        for postulacion in todas:
            conteo[postulacion["estado"]] = conteo.get(postulacion["estado"], 0) + 1
        matches = [p for p in todas if filters.estado in (None, p["estado"])]
        start = (filters.page - 1) * filters.page_size
        page = matches[start : start + filters.page_size]
        return [self._stored_postulacion(p) for p in page], len(matches), conteo

    def get_postulacion(self, postulacion_id: UUID) -> StoredPostulacion | None:
        row = self._postulaciones.get(postulacion_id)
        return self._stored_postulacion(row) if row is not None else None

    def review_postulacion(
        self,
        postulacion_id: UUID,
        estado: str,
        *,
        comentario: str | None,
        set_comentario: bool,
        evento: OutboxEvent,
    ) -> tuple[ReviewResult, StoredPostulacion]:
        row = self._postulaciones[postulacion_id]
        anterior = row["estado"]
        if anterior == "RECHAZADO" and estado != "RECHAZADO":
            convocatoria = self._convocatorias[row["convocatoria_id"]]
            taken = sum(
                1
                for p in self._postulaciones.values()
                if p["convocatoria_id"] == row["convocatoria_id"] and p["estado"] != "RECHAZADO"
            )
            if taken >= convocatoria["cupos"]:
                return "sin_cupos", self._stored_postulacion(row)
        row["estado"] = estado
        if set_comentario:
            row["comentario_club"] = comentario
        row["fecha_actualizacion"] = self._tick()
        if estado == anterior:
            return "sin_cambios", self._stored_postulacion(row)
        self.outbox.append(evento)
        return "actualizada", self._stored_postulacion(row)

    # -- comunicados de clubes (HU-38) ------------------------------------------------

    def create_comunicado(
        self, club_id: UUID, values: dict[str, Any], mensaje: OutboxMessage
    ) -> StoredComunicado:
        estados = ESTADOS_POR_AUDIENCIA[values["audiencia"]]
        usuario_ids = sorted(
            {
                p["usuario_id"]
                for p in self._postulaciones.values()
                if p["estado"] in estados
                and self._convocatorias[p["convocatoria_id"]]["club_id"] == club_id
            },
            key=str,
        )
        club = self._clubs[club_id]
        comunicado = StoredComunicado(
            club_id=club_id,
            club_nombre=club.nombre,
            club_logo=club.logo,
            fecha_creacion=self._tick(),
            destinatarios=len(usuario_ids),
            **values,
        )
        self._comunicados[comunicado.id] = comunicado
        self._destinatarios[comunicado.id] = set(usuario_ids)
        for usuario_id in usuario_ids:
            self.outbox.append(
                OutboxEvent(
                    user_id=usuario_id,
                    kind=mensaje.kind,
                    title=mensaje.title,
                    body=mensaje.body,
                    data=dict(mensaje.data),
                )
            )
        return replace(comunicado)

    def list_comunicados_club(
        self, club_id: UUID, filters: ClubComunicadoFilters
    ) -> tuple[list[StoredComunicado], int]:
        return self._page_comunicados(
            lambda c: c.club_id == club_id
            and filters.audiencia in (None, c.audiencia)
            and filters.prioridad in (None, c.prioridad),
            filters.page,
            filters.page_size,
        )

    def list_comunicados_usuario(
        self, usuario_id: UUID, filters: ComunicadoFilters
    ) -> tuple[list[StoredComunicado], int]:
        return self._page_comunicados(
            lambda c: usuario_id in self._destinatarios[c.id]
            and filters.club_id in (None, c.club_id)
            and filters.prioridad in (None, c.prioridad),
            filters.page,
            filters.page_size,
        )

    def _page_comunicados(self, keep, page: int, page_size: int):
        matches = sorted(
            (c for c in self._comunicados.values() if keep(c)),
            key=lambda c: (c.fecha_creacion, str(c.id)),
            reverse=True,
        )
        start = (page - 1) * page_size
        return [replace(c) for c in matches[start : start + page_size]], len(matches)

    # -- pagos con Webpay Plus (HU-40) -----------------------------------------------

    def list_planes(self, tipo: str | None = None) -> list[StoredPlan]:
        return sorted(
            (p for p in self._planes.values() if p.is_active and tipo in (None, p.tipo)),
            key=lambda p: (p.precio, p.id),
        )

    def get_plan(self, plan_id: int) -> StoredPlan | None:
        plan = self._planes.get(plan_id)
        return plan if plan is not None and plan.is_active else None

    def get_suscripcion(
        self, *, usuario_id: UUID | None = None, club_id: UUID | None = None
    ) -> StoredSuscripcion | None:
        if (usuario_id is None) == (club_id is None):
            raise ValueError("Give exactly one of usuario_id and club_id")
        key = ("u", usuario_id) if usuario_id else ("c", club_id)
        row = self._suscripciones.get(key)
        if row is None:
            return None
        estado = "ACTIVE" if row["fecha_fin"] > datetime.now(timezone.utc) else "EXPIRED"
        return StoredSuscripcion(plan=self._planes[row["plan_id"]], estado=estado, **{
            k: v for k, v in row.items() if k not in {"plan_id", "ultima_transaccion_id"}
        })

    def create_transaccion(self, values: dict[str, Any]) -> StoredTransaccion:
        if any(t.buy_order == values["buy_order"] or t.token_ws == values["token_ws"]
               for t in self._transacciones.values()):
            raise ValueError("buy_order and token_ws must be unique")
        now = self._tick()
        transaccion = StoredTransaccion(
            id=uuid4(),
            plan=self._planes[values["plan_id"]],
            status="PENDING",
            fecha_creacion=now,
            fecha_actualizacion=now,
            **{k: v for k, v in values.items() if k != "plan_id"},
        )
        self._transacciones[transaccion.id] = transaccion
        return replace(transaccion)

    def get_transaccion(
        self, *, token_ws: str | None = None, buy_order: str | None = None
    ) -> StoredTransaccion | None:
        if (token_ws is None) == (buy_order is None):
            raise ValueError("Give exactly one of token_ws and buy_order")
        for transaccion in self._transacciones.values():
            if (token_ws is not None and transaccion.token_ws == token_ws) or (
                buy_order is not None and transaccion.buy_order == buy_order
            ):
                return replace(transaccion)
        return None

    def settle_transaccion(
        self, transaccion_id: UUID, status: str, resultado: dict[str, Any] | None
    ) -> tuple[StoredTransaccion, bool, StoredSuscripcion | None]:
        transaccion = self._transacciones[transaccion_id]
        changed = transaccion.status == "PENDING"
        if changed:
            for column, value in (resultado or {}).items():
                setattr(transaccion, column, value)
            transaccion.status = status
            transaccion.fecha_actualizacion = self._tick()
            if status == "APPROVED":
                self._extend_suscripcion(transaccion)
        suscripcion = None
        if transaccion.status == "APPROVED":
            suscripcion = self.get_suscripcion(
                usuario_id=transaccion.usuario_id, club_id=transaccion.club_id
            )
        return replace(transaccion), changed, suscripcion

    def _extend_suscripcion(self, transaccion: StoredTransaccion) -> None:
        key = ("u", transaccion.usuario_id) if transaccion.usuario_id else ("c", transaccion.club_id)
        now = datetime.now(timezone.utc)
        dias = timedelta(days=transaccion.plan.duracion_dias)
        row = self._suscripciones.get(key)
        if row is None:
            self._suscripciones[key] = {
                "id": uuid4(), "usuario_id": transaccion.usuario_id, "club_id": transaccion.club_id,
                "plan_id": transaccion.plan.id, "fecha_inicio": now, "fecha_fin": now + dias,
                "ultima_transaccion_id": transaccion.id,
            }
            return
        if row["fecha_fin"] <= now:
            row["fecha_inicio"] = now
        row["plan_id"] = transaccion.plan.id
        row["fecha_fin"] = max(row["fecha_fin"], now) + dias
        row["ultima_transaccion_id"] = transaccion.id

    def set_suscripcion_fin(
        self, *, usuario_id: UUID | None = None, club_id: UUID | None = None, fecha_fin: datetime
    ) -> None:
        """Test helper: move a subscription's end (for example into the past)."""
        key = ("u", usuario_id) if usuario_id else ("c", club_id)
        self._suscripciones[key]["fecha_fin"] = fecha_fin
        self._suscripciones[key]["fecha_inicio"] = min(
            self._suscripciones[key]["fecha_inicio"], fecha_fin - timedelta(seconds=1)
        )

    def set_postulacion_estado(self, postulacion_id: UUID, estado: str) -> None:
        """Test helper: set a state without the review rules or notifications."""
        self._postulaciones[postulacion_id]["estado"] = estado

    def _stored_postulacion(self, row: dict[str, Any]) -> StoredPostulacion:
        convocatoria = self._stored(self._convocatorias[row["convocatoria_id"]], active_clubs_only=False)
        assert convocatoria is not None
        values = {key: value for key, value in row.items() if key != "convocatoria_id"}
        return StoredPostulacion(**values, convocatoria=convocatoria)


def _estado(fecha_prueba: datetime, cerrada_en: datetime | None) -> str:
    if fecha_prueba <= datetime.now(timezone.utc):
        return "FINALIZADA"
    return "CERRADA" if cerrada_en is not None else "ABIERTA"


def _call_matches(convocatoria: StoredConvocatoria, filters: ConvocatoriaFilters) -> bool:
    day = convocatoria.fecha_prueba.astimezone(CHILE).date()
    checks = [
        convocatoria.estado == filters.estado,
        filters.club_id is None or convocatoria.club_id == filters.club_id,
        filters.deporte_id is None or convocatoria.deporte.id == filters.deporte_id,
        filters.deporte is None
        or fold_name(convocatoria.deporte.nombre) == fold_name(filters.deporte),
        filters.categoria is None
        or filters.categoria.lower() in convocatoria.categoria_edad.lower(),
        filters.edad is None
        or (
            (convocatoria.edad_minima is None or convocatoria.edad_minima <= filters.edad)
            and (convocatoria.edad_maxima is None or convocatoria.edad_maxima >= filters.edad)
        ),
        filters.fecha_desde is None or day >= filters.fecha_desde,
        filters.fecha_hasta is None or day <= filters.fecha_hasta,
    ]
    return all(checks)


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
