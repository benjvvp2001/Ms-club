from __future__ import annotations

from uuid import UUID, uuid4

from fastapi import HTTPException, status

from app.core.security import Actor
from app.repositories.base import ClubRepository, OutboxMessage, StoredComunicado
from app.schemas.comunicado import (
    ClubComunicadoFilters,
    ComunicadoClubPage,
    ComunicadoClubRead,
    ComunicadoCreate,
    ComunicadoFilters,
    ComunicadoPage,
    ComunicadoRead,
)
from app.schemas.convocatoria import ClubRef
from app.services.authorization import require_managed_club

# The alert may show on a locked screen: only a short preview goes in the push;
# the full text is read in the app.
_PREVIEW_MAX = 120


class ComunicadoService:
    """Notices from clubs to their athletes (HU-38)."""

    def __init__(self, repository: ClubRepository) -> None:
        self._repository = repository

    def publish(self, actor: Actor, club_id: UUID, payload: ComunicadoCreate) -> ComunicadoClubRead:
        club = require_managed_club(self._repository, actor, club_id)
        if not club.is_active:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="El club está inactivo: no puede publicar comunicados.",
            )
        comunicado_id = uuid4()
        comunicado = self._repository.create_comunicado(
            club.id,
            {"id": comunicado_id, **payload.insert_values()},
            _mensaje(club.nombre, comunicado_id, club.id, payload),
        )
        return to_club_read(comunicado)

    def list_for_club(
        self, actor: Actor, club_id: UUID, filters: ClubComunicadoFilters
    ) -> ComunicadoClubPage:
        club = require_managed_club(self._repository, actor, club_id)
        comunicados, total = self._repository.list_comunicados_club(club.id, filters)
        return ComunicadoClubPage(
            items=[to_club_read(c) for c in comunicados],
            total=total,
            page=filters.page,
            page_size=filters.page_size,
        )

    def my_feed(self, actor: Actor, filters: ComunicadoFilters) -> ComunicadoPage:
        comunicados, total = self._repository.list_comunicados_usuario(actor.id, filters)
        return ComunicadoPage(
            items=[to_read(c) for c in comunicados],
            total=total,
            page=filters.page,
            page_size=filters.page_size,
        )


def _mensaje(
    club_nombre: str, comunicado_id: UUID, club_id: UUID, payload: ComunicadoCreate
) -> OutboxMessage:
    contenido = " ".join(payload.contenido.split())
    if len(contenido) > _PREVIEW_MAX:
        contenido = contenido[: _PREVIEW_MAX - 1].rstrip() + "…"
    urgente = payload.prioridad == "URGENTE"
    return OutboxMessage(
        kind="comunicado",
        title=f"Urgente: {payload.titulo}" if urgente else payload.titulo,
        body=f"{club_nombre}: {contenido}",
        data={
            "screen": "comunicado",
            "comunicadoId": str(comunicado_id),
            "clubId": str(club_id),
            "prioridad": payload.prioridad,
        },
    )


def to_read(comunicado: StoredComunicado) -> ComunicadoRead:
    return ComunicadoRead(
        id=comunicado.id,
        club=ClubRef(id=comunicado.club_id, nombre=comunicado.club_nombre, logo=comunicado.club_logo),
        titulo=comunicado.titulo,
        contenido=comunicado.contenido,
        audiencia=comunicado.audiencia,
        prioridad=comunicado.prioridad,
        fecha_creacion=comunicado.fecha_creacion,
    )


def to_club_read(comunicado: StoredComunicado) -> ComunicadoClubRead:
    return ComunicadoClubRead(**to_read(comunicado).model_dump(), destinatarios=comunicado.destinatarios)
