from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status

from app.core.security import Actor
from app.repositories.base import ClubRepository, StoredConvocatoria
from app.schemas.common import Deporte
from app.schemas.convocatoria import (
    ClubRef,
    ConvocatoriaCreate,
    ConvocatoriaFilters,
    ConvocatoriaPage,
    ConvocatoriaRead,
    ConvocatoriaUpdate,
)
from app.services.authorization import require_managed_club
from app.services.convocatoria_cambios import build_aviso
from app.services.entitlements_client import EntitlementsClient, enforce


class ConvocatoriaService:
    """Tryout announcements of clubs (HU-32)."""

    def __init__(self, repository: ClubRepository, entitlements: EntitlementsClient | None = None) -> None:
        self._repository = repository
        # HU-54: plan limits read from ms-pago; None = no limits (fail-open).
        self._entitlements = entitlements

    def list_convocatorias(self, filters: ConvocatoriaFilters) -> ConvocatoriaPage:
        convocatorias, total = self._repository.search_convocatorias(filters)
        return ConvocatoriaPage(
            items=[to_convocatoria_read(c) for c in convocatorias],
            total=total,
            page=filters.page,
            page_size=filters.page_size,
        )

    def get_convocatoria(self, convocatoria_id: UUID) -> ConvocatoriaRead:
        convocatoria = self._repository.get_convocatoria(convocatoria_id)
        if convocatoria is None:
            raise _convocatoria_not_found()
        return to_convocatoria_read(convocatoria)

    def publish(self, actor: Actor, club_id: UUID, payload: ConvocatoriaCreate) -> ConvocatoriaRead:
        club = require_managed_club(self._repository, actor, club_id)
        if not club.is_active:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="El club está inactivo: no puede publicar convocatorias.",
            )
        if self._repository.get_sport(payload.deporte_id) is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Deporte inexistente o inactivo.",
            )
        if all(sport.id != payload.deporte_id for sport in club.deportes):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="El club no ofrece ese deporte: agrégalo a sus deportes antes de publicar.",
            )
        enforce(self._entitlements, "club", club.id, "convocatorias_abiertas",
                lambda _: self._repository.search_convocatorias(
                    ConvocatoriaFilters(club_id=club.id, estado="ABIERTA", page_size=1))[1])
        convocatoria = self._repository.create_convocatoria(club.id, payload.insert_values())
        return to_convocatoria_read(convocatoria)

    def update(
        self, actor: Actor, club_id: UUID, convocatoria_id: UUID, payload: ConvocatoriaUpdate
    ) -> ConvocatoriaRead:
        """HU-68. Same shape as editing an activity (HU-24): only the fields sent change, a convocatoria of
        another club of the same account is 404, and cupos cannot go below the athletes already accepted."""
        club = require_managed_club(self._repository, actor, club_id)
        if not club.is_active:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="El club está inactivo: no puede editar convocatorias.",
            )
        changes = payload.changes()
        if "deporte_id" in changes:
            if self._repository.get_sport(changes["deporte_id"]) is None:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Deporte inexistente o inactivo.")
            if all(sport.id != changes["deporte_id"] for sport in club.deportes):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="El club no ofrece ese deporte: agrégalo a sus deportes antes de publicar.",
                )
        outcome = self._repository.update_convocatoria(
            club.id, convocatoria_id, changes, actor_id=actor.id, aviso=build_aviso
        )
        if outcome.result == "no_encontrada":
            raise _convocatoria_not_found()
        if outcome.result == "cerrada":
            raise HTTPException(status.HTTP_409_CONFLICT, "La convocatoria está cerrada; no se puede editar.")
        if outcome.result == "finalizada":
            raise HTTPException(status.HTTP_409_CONFLICT, "La convocatoria ya finalizó; no se puede editar.")
        if outcome.result == "cupos_bajo_aceptados":
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Ya hay {outcome.aceptados} postulantes aceptados; los cupos no pueden ser menos.",
            )
        if outcome.result == "rango_edad":
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "edad_minima no puede superar a edad_maxima."
            )
        assert outcome.convocatoria is not None
        return to_convocatoria_read(outcome.convocatoria)

    def close(self, actor: Actor, club_id: UUID, convocatoria_id: UUID) -> ConvocatoriaRead:
        club = require_managed_club(self._repository, actor, club_id)
        convocatoria = self._repository.get_convocatoria(convocatoria_id)
        if convocatoria is None or convocatoria.club_id != club.id:
            raise _convocatoria_not_found()
        if convocatoria.estado == "FINALIZADA":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="La convocatoria ya finalizó.",
            )
        return to_convocatoria_read(self._repository.close_convocatoria(convocatoria.id))


def _convocatoria_not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Convocatoria no encontrada.")


def to_convocatoria_read(convocatoria: StoredConvocatoria) -> ConvocatoriaRead:
    return ConvocatoriaRead(
        id=convocatoria.id,
        club=ClubRef(
            id=convocatoria.club_id, nombre=convocatoria.club_nombre, logo=convocatoria.club_logo
        ),
        deporte=Deporte(
            id=convocatoria.deporte.id,
            nombre=convocatoria.deporte.nombre,
            descripcion=convocatoria.deporte.descripcion,
        ),
        titulo=convocatoria.titulo,
        descripcion=convocatoria.descripcion,
        categoria_edad=convocatoria.categoria_edad,
        edad_minima=convocatoria.edad_minima,
        edad_maxima=convocatoria.edad_maxima,
        fecha_prueba=convocatoria.fecha_prueba,
        requisitos=list(convocatoria.requisitos),
        cupos=convocatoria.cupos,
        cupos_disponibles=max(convocatoria.cupos - convocatoria.postulaciones_activas, 0),
        direccion=convocatoria.direccion,
        comuna=convocatoria.comuna,
        estado=convocatoria.estado,
        fecha_creacion=convocatoria.fecha_creacion,
        chat_postulantes_habilitado=convocatoria.chat_postulantes_habilitado,
    )
