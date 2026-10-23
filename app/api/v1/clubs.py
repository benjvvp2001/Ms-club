from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, status

from app.api.dependencies import get_club_admin, get_club_service
from app.core.security import Actor
from app.schemas.club import (
    ClubCreate,
    ClubFilters,
    ClubManaged,
    ClubMio,
    ClubPage,
    ClubProfile,
    ClubUpdate,
    Deporte,
)
from app.services.club_service import ClubService

# Reading the directory is public (HU-30): athletes and visitors browse it
# without an account. Managing a club (HU-31) requires a Ms_Users access token
# with the club_admin role (get_club_admin), and only on the club the account
# administers (club_administrador).
router = APIRouter(prefix="/api/v1/clubs", tags=["clubs"])

DeporteId = Annotated[int, Path(ge=1)]


@router.get(
    "",
    response_model=ClubPage,
    summary="Listar y buscar clubes",
    description="Directorio paginado de clubes activos. Filtros opcionales y combinables: "
    "nombre (parcial, sin distinguir mayúsculas), deporte (nombre, sin distinguir "
    "mayúsculas ni tildes), deporte_id y zona (parcial sobre dirección, comuna y provincia).",
)
def list_clubs(
    filters: Annotated[ClubFilters, Query()],
    service: ClubService = Depends(get_club_service),
) -> ClubPage:
    return service.list_clubs(filters)


@router.post(
    "",
    response_model=ClubManaged,
    status_code=status.HTTP_201_CREATED,
    summary="Crear el perfil de mi club",
    description="Solo representantes de club (rol club_admin) que aún no administran uno. "
    "Exige el RUT del club (persona jurídica): 422 si es inválido, 409 si ya existe. "
    "Quien lo crea queda como administrador PRINCIPAL.",
)
def create_club(
    payload: ClubCreate,
    actor: Actor = Depends(get_club_admin),
    service: ClubService = Depends(get_club_service),
) -> ClubManaged:
    return service.create_club(actor, payload)


# Fixed paths go before /{club_id}, otherwise "deportes" and "me" would be read as an id.
@router.get("/deportes", response_model=list[Deporte], summary="Catálogo de deportes activos")
def list_sports(service: ClubService = Depends(get_club_service)) -> list[Deporte]:
    return service.list_sports()


@router.get(
    "/me",
    response_model=ClubMio,
    summary="El club que administro (panel de edición)",
    description="Incluye mi_rol: PRINCIPAL o ADMIN. Si la cuenta aún no administra un club "
    "y su correo verificado tiene una asignación pendiente, la activa y entra como ADMIN. "
    "404 si no administra ninguno.",
)
def my_club(
    actor: Actor = Depends(get_club_admin),
    service: ClubService = Depends(get_club_service),
) -> ClubMio:
    return service.my_club(actor)


@router.get(
    "/{club_id}",
    response_model=ClubProfile,
    summary="Perfil público de un club",
    description="Incluye ubicación, deportes activos, redes sociales y convocatorias abiertas.",
)
def club_profile(
    club_id: UUID,
    service: ClubService = Depends(get_club_service),
) -> ClubProfile:
    return service.get_profile(club_id)


@router.patch(
    "/{club_id}",
    response_model=ClubManaged,
    summary="Editar el perfil del club",
    description="Solo un administrador del club. Cambian solo los campos enviados; "
    "null borra un campo opcional; redes_sociales y deporte_ids se reemplazan completos.",
)
def update_club(
    club_id: UUID,
    payload: ClubUpdate,
    actor: Actor = Depends(get_club_admin),
    service: ClubService = Depends(get_club_service),
) -> ClubManaged:
    return service.update_club(actor, club_id, payload)


@router.put(
    "/{club_id}/deportes/{deporte_id}",
    response_model=list[Deporte],
    summary="Agregar un deporte al club",
)
def add_sport(
    club_id: UUID,
    deporte_id: DeporteId,
    actor: Actor = Depends(get_club_admin),
    service: ClubService = Depends(get_club_service),
) -> list[Deporte]:
    return service.add_sport(actor, club_id, deporte_id)


@router.delete(
    "/{club_id}/deportes/{deporte_id}",
    response_model=list[Deporte],
    summary="Quitar un deporte del club",
)
def remove_sport(
    club_id: UUID,
    deporte_id: DeporteId,
    actor: Actor = Depends(get_club_admin),
    service: ClubService = Depends(get_club_service),
) -> list[Deporte]:
    return service.remove_sport(actor, club_id, deporte_id)
