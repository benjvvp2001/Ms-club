from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from test_club_admin import token  # also sets JWT_SECRET before the app is imported

from app.main import create_app
from app.repositories.base import ClubSport
from app.repositories.mock_club_repository import MockClubRepository

CLUBS_PATH = "/api/v1/clubs"
CALLS_PATH = "/api/v1/clubs/announcements"
CHILE = ZoneInfo("America/Santiago")
FUTBOL = ClubSport(1, "Fútbol")
TENIS = ClubSport(2, "Tenis")
CURLING_INACTIVO = ClubSport(3, "Curling", is_active=False)
BASQUETBOL = ClubSport(4, "Básquetbol")


class Setup:
    def __init__(self) -> None:
        self.repository = MockClubRepository(sports=[FUTBOL, TENIS, CURLING_INACTIVO, BASQUETBOL])
        self.client = TestClient(create_app(repository=self.repository))

    def club(self, nombre: str = "Club Ñuñoa", deporte_ids: tuple[int, ...] = (1, 2)) -> tuple[UUID, dict]:
        """A representative and their club; returns (club_id, auth headers)."""
        owner = uuid4()
        headers = token(owner)
        response = self.client.post(
            CLUBS_PATH, headers=headers, json={"nombre": nombre, "deporte_ids": list(deporte_ids)}
        )
        assert response.status_code == 201, response.text
        return UUID(response.json()["id"]), headers

    def publish(self, club_id: UUID, headers: dict, /, **fields: object):
        return self.client.post(
            f"{CLUBS_PATH}/{club_id}/announcements", headers=headers, json=payload(**fields)
        )

    def published(self, club_id: UUID, headers: dict, /, **fields: object) -> dict:
        response = self.publish(club_id, headers, **fields)
        assert response.status_code == 201, response.text
        return response.json()


def in_days(days: int, at: time = time(10, 0)) -> datetime:
    return datetime.combine(date.today() + timedelta(days=days), at, tzinfo=CHILE)


def payload(**fields: object) -> dict:
    body = {
        "deporte_id": 1,
        "titulo": "Prueba masiva Sub-17",
        "descripcion": "Buscamos jugadores para la temporada 2027.",
        "categoria_edad": "Sub-17",
        "edad_minima": 15,
        "edad_maxima": 17,
        "fecha_prueba": in_days(10).isoformat(),
        "requisitos": ["Ficha médica al día", "Ropa deportiva y canilleras"],
        "cupos": 40,
        "direccion": "Av. Grecia 2001",
        "comuna": "Ñuñoa",
    }
    body.update(fields)
    return {key: value for key, value in body.items() if value is not ...}


def titles(response) -> list[str]:
    assert response.status_code == 200, response.text
    return [item["titulo"] for item in response.json()["items"]]


# -- publishing -----------------------------------------------------------------


def test_representative_publishes_a_tryout_visible_in_the_section_and_profile():
    setup = Setup()
    club_id, headers = setup.club()
    call = setup.published(club_id, headers)
    assert call["estado"] == "ABIERTA"
    assert call["club"]["id"] == str(club_id) and call["club"]["nombre"] == "Club Ñuñoa"
    assert call["deporte"] == {"id": 1, "nombre": "Fútbol", "descripcion": None}
    assert call["categoria_edad"] == "Sub-17"
    assert (call["edad_minima"], call["edad_maxima"]) == (15, 17)
    assert call["requisitos"] == ["Ficha médica al día", "Ropa deportiva y canilleras"]
    assert call["cupos"] == 40 and call["direccion"] == "Av. Grecia 2001"
    assert datetime.fromisoformat(call["fecha_prueba"]) == in_days(10)

    listed = setup.client.get(CALLS_PATH)
    assert listed.json()["total"] == 1 and listed.json()["items"][0]["id"] == call["id"]
    assert setup.client.get(f"{CALLS_PATH}/{call['id']}").json() == call
    profile = setup.client.get(f"{CLUBS_PATH}/{club_id}").json()
    assert [c["id"] for c in profile["convocatorias_abiertas"]] == [call["id"]]


def test_optional_fields_can_be_left_out():
    setup = Setup()
    club_id, headers = setup.club()
    call = setup.published(
        club_id, headers, titulo=..., descripcion=..., edad_minima=..., edad_maxima=..., comuna=...
    )
    assert call["titulo"] is None and call["edad_minima"] is None and call["comuna"] is None


@pytest.mark.parametrize(
    "fields",
    [
        {"fecha_prueba": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()},
        {"fecha_prueba": datetime.now(timezone.utc).isoformat()},
        {"fecha_prueba": "2027-03-01T10:00:00"},  # no UTC offset: ambiguous
        {"fecha_prueba": in_days(400).isoformat()},
        {"fecha_prueba": "mañana"},
        {"cupos": 0},
        {"cupos": -5},
        {"cupos": 1001},
        {"cupos": "muchos"},
        {"requisitos": []},
        {"requisitos": ["x"]},
        {"requisitos": "Ficha médica"},
        {"edad_minima": 18, "edad_maxima": 14},
        {"edad_minima": 2},
        {"categoria_edad": "S"},
        {"direccion": "Av"},
        {"comuna": "Ñuñoa; DROP TABLE convocatoria"},
        {"deporte_id": 0},
        {"estado": "CERRADA"},
        {"club_id": str(uuid4())},
        {"deporte_id": ...},
        {"categoria_edad": ...},
        {"fecha_prueba": ...},
        {"requisitos": ...},
        {"cupos": ...},
        {"direccion": ...},
    ],
)
def test_invalid_tryouts_are_rejected_with_400(fields):
    setup = Setup()
    club_id, headers = setup.club()
    response = setup.publish(club_id, headers, **fields)
    assert response.status_code == 400, response.text
    assert setup.client.get(CALLS_PATH).json()["total"] == 0


@pytest.mark.parametrize(
    ("deporte_id", "detail"),
    [
        (4, "El club no ofrece ese deporte: agrégalo a sus deportes antes de publicar."),
        (3, "Deporte inexistente o inactivo."),
        (99, "Deporte inexistente o inactivo."),
    ],
)
def test_sport_must_be_active_and_offered_by_the_club(deporte_id, detail):
    setup = Setup()
    club_id, headers = setup.club()
    response = setup.publish(club_id, headers, deporte_id=deporte_id)
    assert response.status_code == 400
    assert response.json() == {"detail": detail}


# -- permissions -------------------------------------------------------------------


def test_publishing_requires_a_token():
    setup = Setup()
    club_id, _ = setup.club()
    response = setup.client.post(f"{CLUBS_PATH}/{club_id}/announcements", json=payload())
    assert response.status_code == 401


@pytest.mark.parametrize("role", ["player", "admin"])
def test_only_club_representatives_publish(role):
    setup = Setup()
    club_id, _ = setup.club()
    response = setup.publish(club_id, token(uuid4(), role))
    assert response.status_code == 403
    assert response.json() == {"detail": "Solo un representante de club puede administrar clubes."}


def test_a_representative_cannot_publish_for_another_club():
    setup = Setup()
    club_id, _ = setup.club("Club A")
    _, other_headers = setup.club("Club B")
    response = setup.publish(club_id, other_headers)
    assert response.status_code == 403
    assert response.json() == {"detail": "No administras este club."}
    assert setup.client.get(CALLS_PATH).json()["total"] == 0


def test_publishing_for_a_missing_club_is_404():
    setup = Setup()
    _, headers = setup.club()
    assert setup.publish(uuid4(), headers).status_code == 404


def test_inactive_club_cannot_publish_and_its_calls_are_hidden():
    setup = Setup()
    club_id, headers = setup.club()
    call = setup.published(club_id, headers)
    setup.repository._clubs[club_id].is_active = False  # noqa: SLF001 - no endpoint deactivates clubs
    assert setup.publish(club_id, headers).status_code == 409
    assert setup.client.get(CALLS_PATH).json()["total"] == 0
    assert setup.client.get(f"{CALLS_PATH}/{call['id']}").status_code == 404


# -- section: filters ------------------------------------------------------------


@pytest.fixture
def section() -> Setup:
    setup = Setup()
    nunoa, nunoa_headers = setup.club("Club Ñuñoa", (1, 2))
    setup.nunoa = nunoa
    providencia, prov_headers = setup.club("Club Providencia", (1, 4))
    setup.published(nunoa, nunoa_headers, titulo="Fútbol Sub-17", fecha_prueba=in_days(3).isoformat())
    setup.published(
        nunoa, nunoa_headers, titulo="Tenis adultos", deporte_id=2, categoria_edad="Adulto",
        edad_minima=18, edad_maxima=..., fecha_prueba=in_days(5).isoformat(),
    )
    setup.published(
        providencia, prov_headers, titulo="Básquetbol +35", deporte_id=4, categoria_edad="+35",
        edad_minima=35, edad_maxima=..., fecha_prueba=in_days(20).isoformat(),
    )
    setup.published(
        providencia, prov_headers, titulo="Fútbol Sub-15", categoria_edad="Sub-15",
        edad_minima=13, edad_maxima=15, fecha_prueba=in_days(8).isoformat(),
    )
    return setup


def test_section_lists_open_calls_soonest_first(section):
    assert titles(section.client.get(CALLS_PATH)) == [
        "Fútbol Sub-17", "Tenis adultos", "Fútbol Sub-15", "Básquetbol +35",
    ]


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"deporte": "futbol"}, ["Fútbol Sub-17", "Fútbol Sub-15"]),
        ({"deporte": "BASQUETBOL"}, ["Básquetbol +35"]),
        ({"deporte_id": 2}, ["Tenis adultos"]),
        ({"categoria": "sub"}, ["Fútbol Sub-17", "Fútbol Sub-15"]),
        ({"categoria": "+35"}, ["Básquetbol +35"]),
        ({"edad": 16}, ["Fútbol Sub-17"]),
        ({"edad": 40}, ["Tenis adultos", "Básquetbol +35"]),
        ({"edad": 14, "deporte": "futbol"}, ["Fútbol Sub-15"]),
        ({"page": 2, "page_size": 3}, ["Básquetbol +35"]),
    ],
)
def test_section_filters_are_combinable(section, params, expected):
    assert titles(section.client.get(CALLS_PATH, params=params)) == expected


def test_section_filters_by_club(section):
    response = section.client.get(CALLS_PATH, params={"club_id": str(section.nunoa)})
    assert titles(response) == ["Fútbol Sub-17", "Tenis adultos"]


def test_section_filters_by_date_range(section):
    params = {"fecha_desde": str(date.today() + timedelta(days=4)), "fecha_hasta": str(date.today() + timedelta(days=10))}
    assert titles(section.client.get(CALLS_PATH, params=params)) == ["Tenis adultos", "Fútbol Sub-15"]


def test_date_filter_uses_chile_time():
    setup = Setup()
    club_id, headers = setup.club()
    # 23:30 in Chile is already the next day in UTC.
    late = in_days(6, time(23, 30))
    setup.published(club_id, headers, titulo="Nocturna", fecha_prueba=late.isoformat())
    day = late.date()
    assert titles(setup.client.get(CALLS_PATH, params={"fecha_desde": str(day), "fecha_hasta": str(day)})) == ["Nocturna"]
    assert titles(setup.client.get(CALLS_PATH, params={"fecha_desde": str(day + timedelta(days=1))})) == []


@pytest.mark.parametrize(
    "params",
    [
        {"estado": "PENDIENTE"},
        {"estado": "abierta"},
        {"edad": 2},
        {"club_id": "club-nunoa"},
        {"fecha_desde": "ayer"},
        {"fecha_desde": "2026-12-10", "fecha_hasta": "2026-12-01"},
        {"page_size": 51},
        {"zona": "Ñuñoa"},
    ],
)
def test_invalid_section_filters_are_rejected_with_400(params):
    setup = Setup()
    assert setup.client.get(CALLS_PATH, params=params).status_code == 400


# -- states --------------------------------------------------------------------------


def test_club_closes_a_call_which_leaves_the_open_section():
    setup = Setup()
    club_id, headers = setup.club()
    call = setup.published(club_id, headers)
    path = f"{CLUBS_PATH}/{club_id}/announcements/{call['id']}/close"
    closed = setup.client.post(path, headers=headers)
    assert closed.status_code == 200 and closed.json()["estado"] == "CERRADA"
    assert setup.client.post(path, headers=headers).json()["estado"] == "CERRADA"
    assert setup.client.get(CALLS_PATH).json()["total"] == 0
    assert titles(setup.client.get(CALLS_PATH, params={"estado": "CERRADA"})) == [call["titulo"]]
    assert setup.client.get(f"{CLUBS_PATH}/{club_id}").json()["convocatorias_abiertas"] == []


def test_call_whose_date_passed_is_finished_automatically():
    setup = Setup()
    club_id, headers = setup.club()
    # Published earlier; its date has passed since (the DTO rejects past dates).
    past = setup.repository.create_convocatoria(
        club_id,
        {
            "deporte_id": 1, "titulo": "Prueba de ayer", "categoria_edad": "Sub-17",
            "fecha_prueba": datetime.now(timezone.utc) - timedelta(days=1),
            "requisitos": ["Ficha médica"], "cupos": 10, "direccion": "Av. Grecia 2001",
        },
    )
    assert past.estado == "FINALIZADA"
    assert setup.client.get(CALLS_PATH).json()["total"] == 0
    assert titles(setup.client.get(CALLS_PATH, params={"estado": "FINALIZADA"})) == ["Prueba de ayer"]
    close = setup.client.post(f"{CLUBS_PATH}/{club_id}/announcements/{past.id}/close", headers=headers)
    assert close.status_code == 409


def test_only_the_owner_closes_and_only_calls_of_their_club():
    setup = Setup()
    club_a, headers_a = setup.club("Club A")
    club_b, headers_b = setup.club("Club B")
    call_a = setup.published(club_a, headers_a)
    call_b = setup.published(club_b, headers_b)
    close = lambda club, call, headers: setup.client.post(  # noqa: E731
        f"{CLUBS_PATH}/{club}/announcements/{call['id']}/close", headers=headers
    )
    assert close(club_a, call_a, headers_b).status_code == 403
    assert close(club_a, call_a, token(uuid4(), "player")).status_code == 403
    assert close(club_b, call_a, headers_b).status_code == 404
    assert setup.client.post(f"{CLUBS_PATH}/{club_a}/announcements/{call_a['id']}/close").status_code == 401
    assert setup.client.get(CALLS_PATH).json()["total"] == 2


def test_detail_of_unknown_or_malformed_call():
    setup = Setup()
    assert setup.client.get(f"{CALLS_PATH}/{uuid4()}").status_code == 404
    assert setup.client.get(f"{CALLS_PATH}/no-es-uuid").status_code == 400
