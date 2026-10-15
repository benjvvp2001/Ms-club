from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from app.main import create_app
from app.repositories.base import ClubSport, StoredClub
from app.repositories.mock_club_repository import MockClubRepository
from app.schemas.club import ClubFilters

CLUBS_PATH = "/api/v1/clubs"

FUTBOL = ClubSport(1, "Fútbol")
TENIS = ClubSport(2, "Tenis", "Canchas de arcilla")
PADEL = ClubSport(3, "Pádel")
NATACION_INACTIVO = ClubSport(4, "Natación", is_active=False)


def club(nombre: str, direccion: str | None = None, **extra: object) -> StoredClub:
    return StoredClub(id=uuid4(), nombre=nombre, direccion=direccion, **extra)


def make_client() -> tuple[TestClient, MockClubRepository]:
    repository = MockClubRepository(
        [
            club(
                "Club Deportivo Ñuñoa",
                "Av. Grecia 2001, Ñuñoa",
                descripcion="Fútbol y tenis en el corazón de Ñuñoa. " * 10,
                email_contacto="contacto@cdnunoa.example",
                telefono="+56 2 2345 6789",
                latitud=-33.4569,
                longitud=-70.5989,
                logo="https://cdn.example.com/logo.png",
                deportes=[TENIS, FUTBOL, NATACION_INACTIVO],
            ),
            club("Tenis Providencia", "Av. Pocuro 1200, Providencia", deportes=[TENIS]),
            club("Pádel 50% Las Condes", "Las Condes", deportes=[PADEL]),
            club("Club Cerrado", "Ñuñoa", deportes=[FUTBOL], is_active=False),
        ]
    )
    client = TestClient(create_app(repository=repository))
    return client, repository


def find(repository: MockClubRepository, nombre: str) -> StoredClub:
    clubs, _ = repository.search_clubs(ClubFilters(nombre=nombre))
    return clubs[0]


def names(response) -> list[str]:
    assert response.status_code == 200, response.text
    return [item["nombre"] for item in response.json()["items"]]


def test_lists_active_clubs_ordered_by_name_with_their_active_sports():
    client, _ = make_client()
    response = client.get(CLUBS_PATH)
    assert names(response) == ["Club Deportivo Ñuñoa", "Pádel 50% Las Condes", "Tenis Providencia"]
    body = response.json()
    assert body["total"] == 3 and body["page"] == 1 and body["page_size"] == 20
    first = body["items"][0]
    assert [d["nombre"] for d in first["deportes"]] == ["Fútbol", "Tenis"]
    assert len(first["descripcion_corta"]) <= 160 and first["descripcion_corta"].endswith("…")
    assert "email_contacto" not in first and "telefono" not in first


def test_club_without_description_lists_none():
    client, _ = make_client()
    item = client.get(CLUBS_PATH, params={"nombre": "providencia"}).json()["items"][0]
    assert item["descripcion_corta"] is None


def test_paginates_and_reports_the_full_total():
    client, _ = make_client()
    page_2 = client.get(CLUBS_PATH, params={"page": 2, "page_size": 2})
    assert names(page_2) == ["Tenis Providencia"]
    assert page_2.json()["total"] == 3
    beyond = client.get(CLUBS_PATH, params={"page": 9, "page_size": 2})
    assert names(beyond) == [] and beyond.json()["total"] == 3


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"nombre": "TENIS"}, ["Tenis Providencia"]),
        ({"nombre": "club"}, ["Club Deportivo Ñuñoa"]),
        ({"nombre": "50%"}, ["Pádel 50% Las Condes"]),
        ({"deporte": "tenis"}, ["Club Deportivo Ñuñoa", "Tenis Providencia"]),
        ({"deporte": "FUTBOL"}, ["Club Deportivo Ñuñoa"]),
        ({"deporte": "padel"}, ["Pádel 50% Las Condes"]),
        ({"deporte": "natacion"}, []),
        ({"deporte_id": 2}, ["Club Deportivo Ñuñoa", "Tenis Providencia"]),
        ({"zona": "ñuñoa"}, ["Club Deportivo Ñuñoa"]),
        ({"zona": "providencia", "deporte": "tenis"}, ["Tenis Providencia"]),
        ({"nombre": "club", "deporte_id": 3}, []),
    ],
)
def test_filters_are_optional_and_combinable(params, expected):
    client, _ = make_client()
    assert names(client.get(CLUBS_PATH, params=params)) == expected


@pytest.mark.parametrize(
    "params",
    [
        {"page": 0},
        {"page_size": 0},
        {"page_size": 51},
        {"page": "uno"},
        {"nombre": "a"},
        {"deporte": "x"},
        {"deporte_id": 0},
        {"deporte_id": "futbol"},
        {"zona": "x" * 101},
        {"comuna": "Ñuñoa"},
    ],
)
def test_invalid_search_parameters_are_rejected_with_400(params):
    client, _ = make_client()
    response = client.get(CLUBS_PATH, params=params)
    assert response.status_code == 400
    assert isinstance(response.json()["detail"], list)


def test_profile_returns_public_data_and_open_convocatorias():
    client, repository = make_client()
    nunoa = find(repository, "Club Deportivo Ñuñoa")
    repository.create_convocatoria(
        nunoa.id,
        {
            "deporte_id": FUTBOL.id,
            "titulo": "Selección sub-18",
            "categoria_edad": "Sub-18",
            "fecha_prueba": datetime.now(timezone.utc) + timedelta(days=10),
            "requisitos": ["Ficha médica"],
            "cupos": 4,
            "direccion": "Av. Grecia 2001",
        },
    )

    response = client.get(f"{CLUBS_PATH}/{nunoa.id}")
    assert response.status_code == 200, response.text
    profile = response.json()
    assert profile["ubicacion"] == {
        "direccion": "Av. Grecia 2001, Ñuñoa",
        "comuna": None,
        "provincia": None,
        "latitud": -33.4569,
        "longitud": -70.5989,
    }
    assert profile["telefono"] == "+56 2 2345 6789"
    assert [d["nombre"] for d in profile["deportes"]] == ["Fútbol", "Tenis"]
    assert profile["convocatorias_abiertas"][0]["titulo"] == "Selección sub-18"
    assert "usuario_id" not in profile


def test_profile_of_missing_or_inactive_club_is_404():
    client, repository = make_client()
    inactive = repository.add_club(club("Otro", is_active=False))
    for club_id in (uuid4(), inactive.id):
        response = client.get(f"{CLUBS_PATH}/{club_id}")
        assert response.status_code == 404
        assert response.json() == {"detail": "Club no encontrado."}


@pytest.mark.parametrize("ref", ["club-deportivo-nunoa", "123", "not a uuid"])
def test_profile_with_malformed_id_is_400(ref):
    client, _ = make_client()
    assert client.get(f"{CLUBS_PATH}/{ref}").status_code == 400


def test_directory_is_public_and_health_is_ready():
    client, _ = make_client()
    assert client.get(CLUBS_PATH).status_code == 200
    assert client.get(f"{CLUBS_PATH}/health/ready").json() == {
        "status": "ready",
        "service": "ms_club",
    }
