from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status

from app.core.security import Actor
from app.repositories.base import ClubRepository, ClubSport, StoredClub
from app.schemas.club import (
    DESCRIPCION_CORTA_MAX,
    ClubCreate,
    ClubFilters,
    ClubManaged,
    ClubMio,
    ClubPage,
    ClubProfile,
    ClubSummary,
    ClubUpdate,
    Ubicacion,
)
from app.schemas.common import PAGE_SIZE_MAX, Deporte
from app.schemas.convocatoria import ConvocatoriaFilters, ConvocatoriaRead
from app.services.authorization import club_not_found, require_managed_club
from app.services.convocatoria_service import to_convocatoria_read
from app.services.rut import normalizar_rut
from app.services.users_client import ADMIN_NAME_FALLBACK, AccountNames, account_name


class ClubService:
    def __init__(self, repository: ClubRepository, names: AccountNames | None = None) -> None:
        self._repository = repository
        # Ms_Users, for the creator's name in club_administrador; optional.
        self._names = names

    # -- public directory (HU-30) ---------------------------------------------

    def list_clubs(self, filters: ClubFilters) -> ClubPage:
        clubs, total = self._repository.search_clubs(filters)
        return ClubPage(
            items=[_summary(club) for club in clubs],
            total=total,
            page=filters.page,
            page_size=filters.page_size,
        )

    def get_profile(self, club_id: UUID) -> ClubProfile:
        club = self._repository.get_club(club_id)
        if club is None:
            raise club_not_found()
        return ClubProfile(
            **_public_data(club), convocatorias_abiertas=self._open_convocatorias(club.id)
        )

    def list_sports(self) -> list[Deporte]:
        return _deportes(self._repository.list_sports())

    # -- club administration (HU-31) ------------------------------------------
    #
    # Edition panel: only a representative (club_admin) and only on the club
    # they administer (club_administrador, require_managed_club).

    def my_club(self, actor: Actor) -> ClubMio:
        club = self._repository.get_club_by_admin(actor.id)
        admin = self._repository.get_admin(club.id, actor.id) if club is not None else None
        if club is None or admin is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Aún no tienes un club.")
        return ClubMio(**_managed(club).model_dump(), mi_rol=admin.rol)

    def create_club(self, actor: Actor, payload: ClubCreate) -> ClubManaged:
        if not actor.is_club_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Solo un representante de club puede crear un club.",
            )
        rut = normalizar_rut(payload.rut)
        if rut is None:
            raise HTTPException(
                status_code=422,  # the contract asks 422 (other input errors here are 400)
                detail="RUT inválido: usa el RUT del club (persona jurídica), por ejemplo 76123456-0.",
            )
        self._check_sports(payload.deporte_ids)
        if self._repository.get_club_by_admin(actor.id) is not None:
            raise _already_admin()
        # The name never blocks the creation: Ms_Users failing gives a neutral one.
        nombre = account_name(self._names, actor, ADMIN_NAME_FALLBACK)
        result, club = self._repository.create_club(
            actor.id, rut, nombre, payload.insert_values(), payload.deporte_ids
        )
        if result == "ya_administra":
            raise _already_admin()
        if result == "rut_duplicado":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Ya existe un club con ese RUT.",
            )
        assert club is not None
        return _managed(club)

    def update_club(self, actor: Actor, club_id: UUID, payload: ClubUpdate) -> ClubManaged:
        club = self._managed_club(actor, club_id)
        changes = payload.changes()
        latitud = changes.get("latitud", club.latitud)
        longitud = changes.get("longitud", club.longitud)
        if (latitud is None) != (longitud is None):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="latitud y longitud se envían juntas.",
            )
        self._check_sports(payload.deporte_ids)
        return _managed(self._repository.update_club(club.id, changes, payload.deporte_ids))

    def add_sport(self, actor: Actor, club_id: UUID, deporte_id: int) -> list[Deporte]:
        club = self._managed_club(actor, club_id)
        if self._repository.get_sport(deporte_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deporte no encontrado.")
        self._repository.add_sport(club.id, deporte_id)
        return self._club_sports(club.id)

    def remove_sport(self, actor: Actor, club_id: UUID, deporte_id: int) -> list[Deporte]:
        club = self._managed_club(actor, club_id)
        self._repository.remove_sport(club.id, deporte_id)
        return self._club_sports(club.id)

    def _check_sports(self, deporte_ids: list[int] | None) -> None:
        if not deporte_ids:
            return
        unknown = sorted(set(deporte_ids) - {sport.id for sport in self._repository.list_sports()})
        if unknown:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Deportes inexistentes o inactivos: {unknown}.",
            )

    def _managed_club(self, actor: Actor, club_id: UUID) -> StoredClub:
        return require_managed_club(self._repository, actor, club_id)

    def _club_sports(self, club_id: UUID) -> list[Deporte]:
        club = self._repository.get_club(club_id, include_inactive=True)
        assert club is not None
        return _deportes(club.deportes)

    def _open_convocatorias(self, club_id: UUID) -> list[ConvocatoriaRead]:
        """Soonest first; the full list is in the convocatorias section."""
        convocatorias, _ = self._repository.search_convocatorias(
            ConvocatoriaFilters(club_id=club_id, estado="ABIERTA", page_size=PAGE_SIZE_MAX)
        )
        return [to_convocatoria_read(c) for c in convocatorias]


def _already_admin() -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Ya administras un club.")


def _deportes(deportes: list[ClubSport]) -> list[Deporte]:
    return [Deporte(id=d.id, nombre=d.nombre, descripcion=d.descripcion) for d in deportes]


def _short(descripcion: str | None) -> str | None:
    if descripcion is None:
        return None
    descripcion = descripcion.strip()
    if len(descripcion) > DESCRIPCION_CORTA_MAX:
        descripcion = descripcion[: DESCRIPCION_CORTA_MAX - 1].rstrip() + "…"
    return descripcion


def _summary(club: StoredClub) -> ClubSummary:
    return ClubSummary(
        id=club.id,
        nombre=club.nombre,
        descripcion_corta=_short(club.descripcion),
        logo=club.logo,
        direccion=club.direccion,
        comuna=club.comuna,
        verificado=club.estado_verificacion == "verificado",
        deportes=_deportes(club.deportes),
    )


def _public_data(club: StoredClub) -> dict:
    return {
        "id": club.id,
        "nombre": club.nombre,
        "rut": club.rut,
        "descripcion": club.descripcion,
        "historia": club.historia,
        "logo": club.logo,
        "portada": club.portada,
        "ubicacion": Ubicacion(
            direccion=club.direccion,
            comuna=club.comuna,
            provincia=club.provincia,
            latitud=club.latitud,
            longitud=club.longitud,
        ),
        "email_contacto": club.email_contacto,
        "telefono": club.telefono,
        "sitio_web": club.sitio_web,
        "redes_sociales": club.redes_sociales,
        "verificado": club.estado_verificacion == "verificado",
        "deportes": _deportes(club.deportes),
    }


def to_managed(club: StoredClub) -> ClubManaged:
    return _managed(club)


def _managed(club: StoredClub) -> ClubManaged:
    return ClubManaged(
        **_public_data(club),
        is_active=club.is_active,
        estado_verificacion=club.estado_verificacion,
        fecha_creacion=club.fecha_creacion,
        fecha_actualizacion=club.fecha_actualizacion,
    )
