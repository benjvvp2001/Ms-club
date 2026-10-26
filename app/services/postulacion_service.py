from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status

from app.core.security import Actor
from app.repositories.base import ClubRepository, OutboxEvent, StoredConvocatoria, StoredPostulacion
from app.schemas.convocatoria import ConvocatoriaRead
from app.schemas.postulacion import (
    Antecedente,
    CandidatoFilters,
    CandidatoPage,
    CandidatoRead,
    PerfilDeportivo,
    PostulacionCreate,
    PostulacionEstadoUpdate,
    PostulacionFilters,
    PostulacionPage,
    PostulacionRead,
)
from app.services.authorization import require_managed_club
from app.services.convocatoria_service import to_convocatoria_read
from app.services.users_client import AthleteProfiles

# Inbox/push text per new estado. The club's comment is not included: alerts can
# show on a locked screen, so the detail stays inside the app.
_AVISOS = {
    "PRESELECCIONADO": ("Quedaste preseleccionado", "{club} te preseleccionó en {prueba}."),
    "ACEPTADO": ("¡Te seleccionaron!", "{club} confirmó tu selección en {prueba}."),
    "RECHAZADO": ("Postulación no seleccionada", "{club} no continuará con tu postulación a {prueba}."),
}

_CONFLICTS = {
    "no_abierta": "La convocatoria ya no está abierta.",
    "duplicada": "Ya postulaste a esta convocatoria.",
    "sin_cupos": "La convocatoria no tiene cupos disponibles.",
}


class PostulacionService:
    """Athletes' applications to convocatorias (HU-33)."""

    def __init__(self, repository: ClubRepository, users: AthleteProfiles) -> None:
        self._repository = repository
        self._users = users

    def apply(
        self, actor: Actor, convocatoria_id: UUID, payload: PostulacionCreate
    ) -> PostulacionRead:
        convocatoria = self._repository.get_convocatoria(convocatoria_id)
        if convocatoria is None:
            raise _convocatoria_not_found()
        if convocatoria.estado != "ABIERTA":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"La convocatoria no está abierta ({convocatoria.estado}).",
            )

        perfil = self._users.athlete_profile(actor)
        _check_age(convocatoria, perfil)

        # Open state, duplicates and cupos are checked again atomically with the insert.
        result, postulacion = self._repository.create_postulacion(
            convocatoria.id,
            actor.id,
            {**payload.insert_values(), "perfil_deportivo": perfil.model_dump()},
        )
        if result == "no_encontrada":
            raise _convocatoria_not_found()
        if result != "creada" or postulacion is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_CONFLICTS[result])
        return to_postulacion_read(postulacion)

    # -- revisión por el club (HU-34) ---------------------------------------------------
    # Only an administrator of the club that owns the convocatoria; the club
    # may be inactive (its administrators still manage it) and the tryout may be over
    # (selections are confirmed after it). The club's decision is one of
    # PENDIENTE (en espera), PRESELECCIONADO, ACEPTADO or RECHAZADO.

    def list_candidatos(
        self, actor: Actor, convocatoria_id: UUID, filters: CandidatoFilters
    ) -> CandidatoPage:
        convocatoria = self._repository.get_convocatoria(convocatoria_id, include_inactive_clubs=True)
        if convocatoria is None:
            raise _convocatoria_not_found()
        require_managed_club(self._repository, actor, convocatoria.club_id)
        candidatos, total, conteo = self._repository.list_candidatos(convocatoria.id, filters)
        return CandidatoPage(
            convocatoria=to_convocatoria_read(convocatoria),
            items=[to_candidato_read(c) for c in candidatos],
            total=total,
            page=filters.page,
            page_size=filters.page_size,
            conteo_por_estado={
                estado: conteo.get(estado, 0)
                for estado in ("PENDIENTE", "PRESELECCIONADO", "ACEPTADO", "RECHAZADO")
            },
        )

    def review(
        self, actor: Actor, postulacion_id: UUID, payload: PostulacionEstadoUpdate
    ) -> CandidatoRead:
        postulacion = self._repository.get_postulacion(postulacion_id)
        if postulacion is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Postulación no encontrada.")
        require_managed_club(self._repository, actor, postulacion.convocatoria.club_id)
        result, actualizada = self._repository.review_postulacion(
            postulacion.id,
            payload.estado,
            comentario=payload.comentario,
            set_comentario="comentario" in payload.model_fields_set,
            evento=_aviso(postulacion, payload.estado),
        )
        if result == "sin_cupos":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="La convocatoria no tiene cupos para volver a considerar esta postulación.",
            )
        return to_candidato_read(actualizada)

    def set_chat_postulantes(
        self, actor: Actor, club_id: UUID, convocatoria_id: UUID, habilitado: bool
    ) -> ConvocatoriaRead:
        """The club decides whether its applicants may write in their consulta
        for this convocatoria. Only an administrator of the owning club."""
        require_managed_club(self._repository, actor, club_id)
        convocatoria = self._repository.set_chat_postulantes(club_id, convocatoria_id, habilitado)
        if convocatoria is None:
            raise _convocatoria_not_found()
        return to_convocatoria_read(convocatoria)

    def my_postulaciones(self, actor: Actor, filters: PostulacionFilters) -> PostulacionPage:
        postulaciones, total = self._repository.list_postulaciones(actor.id, filters)
        return PostulacionPage(
            items=[to_postulacion_read(p) for p in postulaciones],
            total=total,
            page=filters.page,
            page_size=filters.page_size,
        )


def _convocatoria_not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Convocatoria no encontrada.")


def _check_age(convocatoria: StoredConvocatoria, perfil: PerfilDeportivo) -> None:
    """Only when both the convocatoria has a numeric range and the athlete's age is known."""
    edad = perfil.edad
    if edad is None:
        return
    minima, maxima = convocatoria.edad_minima, convocatoria.edad_maxima
    if (minima is not None and edad < minima) or (maxima is not None and edad > maxima):
        rango = f"{minima or '–'} a {maxima or '–'} años"
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Tu edad ({edad}) no está en el rango de la convocatoria ({rango}).",
        )


def _aviso(postulacion: StoredPostulacion, estado: str) -> OutboxEvent | None:
    """Notification for the athlete; written only if the estado really changes.
    PENDIENTE (en espera) has no notice."""
    if estado not in _AVISOS:
        return None
    convocatoria = postulacion.convocatoria
    prueba = f"«{convocatoria.titulo}»" if convocatoria.titulo else (
        f"la prueba de {convocatoria.deporte.nombre} {convocatoria.categoria_edad}"
    )
    title, body = _AVISOS[estado]
    return OutboxEvent(
        user_id=postulacion.usuario_id,
        kind="convocatoria",
        title=title,
        body=body.format(club=convocatoria.club_nombre, prueba=prueba),
        data={
            "screen": "convocatoria",
            "convocatoriaId": str(convocatoria.id),
            "postulacionId": str(postulacion.id),
            "estado": estado,
        },
    )


def to_candidato_read(postulacion: StoredPostulacion) -> CandidatoRead:
    return CandidatoRead(
        id=postulacion.id,
        usuario_id=postulacion.usuario_id,
        estado=postulacion.estado,
        perfil_deportivo=PerfilDeportivo.model_validate(postulacion.perfil_deportivo),
        mensaje=postulacion.mensaje,
        antecedentes=[Antecedente.model_validate(entry) for entry in postulacion.antecedentes],
        comentario_club=postulacion.comentario_club,
        fecha_postulacion=postulacion.fecha_creacion,
        fecha_actualizacion=postulacion.fecha_actualizacion,
    )


def to_postulacion_read(postulacion: StoredPostulacion) -> PostulacionRead:
    return PostulacionRead(
        id=postulacion.id,
        estado=postulacion.estado,
        convocatoria=to_convocatoria_read(postulacion.convocatoria),
        mensaje=postulacion.mensaje,
        antecedentes=[Antecedente.model_validate(entry) for entry in postulacion.antecedentes],
        perfil_deportivo=PerfilDeportivo.model_validate(postulacion.perfil_deportivo),
        comentario_club=postulacion.comentario_club,
        fecha_creacion=postulacion.fecha_creacion,
        fecha_actualizacion=postulacion.fecha_actualizacion,
    )
