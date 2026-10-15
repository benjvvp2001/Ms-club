from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from test_club_admin import token  # also sets JWT_SECRET before the app is imported

from app.core.security import Actor
from app.main import create_app
from app.repositories.base import ClubSport
from app.repositories.mock_club_repository import MockClubRepository
from app.schemas.postulacion import PerfilDeportivo
from app.services.users_client import UsersClient

CLUBS_PATH = "/api/v1/clubs"
CALLS_PATH = "/api/v1/clubs/announcements"
MY_PATH = "/api/v1/clubs/announcements/applications/me"
CHILE = ZoneInfo("America/Santiago")
FUTBOL = ClubSport(1, "Fútbol")


def perfil(nombre: str = "Ana", edad: int | None = 16) -> PerfilDeportivo:
    return PerfilDeportivo(
        nombre=nombre, apellido_inicial="T", edad=edad,
        foto_perfil="https://cdn.example.com/ana.jpg", biografia="Lateral izquierda.",
        deportes=[{"deporte_codigo": "futbol", "nivel": 4}],
    )


class FakeUsers:
    """Ms_Users stand-in: the public card of each athlete, or the error it would give."""

    def __init__(self) -> None:
        self.profiles: dict[UUID, PerfilDeportivo] = {}
        self.error: HTTPException | None = None

    def athlete_profile(self, actor: Actor) -> PerfilDeportivo:
        if self.error is not None:
            raise self.error
        return self.profiles.get(actor.id) or perfil()


class Setup:
    def __init__(self) -> None:
        self.repository = MockClubRepository(sports=[FUTBOL])
        self.users = FakeUsers()
        self.client = TestClient(create_app(repository=self.repository, users=self.users))
        self.owner_headers = token(uuid4())
        response = self.client.post(
            CLUBS_PATH, headers=self.owner_headers, json={"nombre": "Club Ñuñoa", "deporte_ids": [1]}
        )
        assert response.status_code == 201, response.text
        self.club_id = UUID(response.json()["id"])

    def convocatoria(self, **fields: object) -> dict:
        body = {
            "deporte_id": 1, "titulo": "Prueba Sub-17", "categoria_edad": "Sub-17",
            "edad_minima": 15, "edad_maxima": 17,
            "fecha_prueba": datetime.combine(date.today() + timedelta(days=10), time(10), tzinfo=CHILE).isoformat(),
            "requisitos": ["Ficha médica"], "cupos": 2, "direccion": "Av. Grecia 2001",
        }
        body.update(fields)
        body = {key: value for key, value in body.items() if value is not ...}
        response = self.client.post(f"{CLUBS_PATH}/{self.club_id}/announcements", headers=self.owner_headers, json=body)
        assert response.status_code == 201, response.text
        return response.json()

    def athlete(self, **profile: object) -> tuple[UUID, dict]:
        athlete_id = uuid4()
        if profile:
            self.users.profiles[athlete_id] = perfil(**profile)
        return athlete_id, token(athlete_id, "player")

    def apply(self, convocatoria_id: object, headers: dict, json: dict | None = None):
        return self.client.post(f"{CALLS_PATH}/{convocatoria_id}/applications", headers=headers, json=json or {})


ANTECEDENTES = [
    {"institucion": "Club Deportivo Macul", "deporte": "Fútbol", "anio_inicio": 2021,
     "anio_fin": 2024, "logros": "Campeona regional sub-15"},
    {"institucion": "Liceo Lastarria", "deporte": "Fútbol", "anio_inicio": 2024},
]


# -- applying -------------------------------------------------------------------------


def test_athlete_applies_with_profile_attached_and_gets_pending():
    setup = Setup()
    call = setup.convocatoria()
    _, headers = setup.athlete(nombre="Ana", edad=16)
    response = setup.apply(call["id"], headers, {"mensaje": "Juego de lateral.", "antecedentes": ANTECEDENTES})
    assert response.status_code == 201, response.text
    postulacion = response.json()
    assert UUID(postulacion["id"]) and postulacion["estado"] == "PENDIENTE"
    assert postulacion["mensaje"] == "Juego de lateral."
    assert postulacion["antecedentes"][0]["logros"] == "Campeona regional sub-15"
    assert postulacion["antecedentes"][1]["anio_fin"] is None
    assert postulacion["perfil_deportivo"] == {
        "nombre": "Ana", "apellido_inicial": "T", "edad": 16,
        "foto_perfil": "https://cdn.example.com/ana.jpg", "biografia": "Lateral izquierda.",
        "deportes": [{"deporte_codigo": "futbol", "nivel": 4}],
    }
    assert postulacion["convocatoria"]["id"] == call["id"]
    assert postulacion["convocatoria"]["cupos_disponibles"] == 1
    assert setup.client.get(f"{CALLS_PATH}/{call['id']}").json()["cupos_disponibles"] == 1


def test_message_and_background_are_optional():
    setup = Setup()
    call = setup.convocatoria()
    _, headers = setup.athlete()
    response = setup.apply(call["id"], headers)
    assert response.status_code == 201
    assert response.json()["mensaje"] is None and response.json()["antecedentes"] == []


def test_profile_is_a_snapshot_the_athlete_cannot_forge():
    setup = Setup()
    call = setup.convocatoria()
    athlete_id, headers = setup.athlete(nombre="Ana")
    forged = setup.apply(call["id"], headers, {"perfil_deportivo": {"nombre": "Messi"}})
    assert forged.status_code == 400
    setup.apply(call["id"], headers)
    setup.users.profiles[athlete_id] = perfil(nombre="Ana María")
    mine = setup.client.get(MY_PATH, headers=headers).json()["items"][0]
    assert mine["perfil_deportivo"]["nombre"] == "Ana"


def test_duplicate_application_is_409():
    setup = Setup()
    call = setup.convocatoria()
    _, headers = setup.athlete()
    assert setup.apply(call["id"], headers).status_code == 201
    second = setup.apply(call["id"], headers, {"mensaje": "Otra vez"})
    assert second.status_code == 409
    assert second.json() == {"detail": "Ya postulaste a esta convocatoria."}
    assert setup.client.get(MY_PATH, headers=headers).json()["total"] == 1


def test_no_cupos_left_is_409_and_a_rejection_frees_one():
    setup = Setup()
    call = setup.convocatoria(cupos=1)
    _, first = setup.athlete()
    _, second = setup.athlete()
    accepted = setup.apply(call["id"], first).json()
    full = setup.apply(call["id"], second)
    assert full.status_code == 409
    assert full.json() == {"detail": "La convocatoria no tiene cupos disponibles."}
    assert setup.client.get(f"{CALLS_PATH}/{call['id']}").json()["cupos_disponibles"] == 0

    setup.repository.set_postulacion_estado(UUID(accepted["id"]), "RECHAZADO")
    assert setup.apply(call["id"], second).status_code == 201


@pytest.mark.parametrize(
    ("edad", "fields", "expected"),
    [
        (16, {}, 201),
        (15, {}, 201),
        (17, {}, 201),
        (14, {}, 409),
        (18, {}, 409),
        (None, {}, 201),  # age unknown in Ms_Users: the club decides
        (40, {"edad_minima": ..., "edad_maxima": ..., "categoria_edad": "Libre"}, 201),
        (40, {"edad_minima": 35, "edad_maxima": ..., "categoria_edad": "+35"}, 201),
        (30, {"edad_minima": 35, "edad_maxima": ..., "categoria_edad": "+35"}, 409),
    ],
)
def test_athlete_age_must_fit_the_numeric_range(edad, fields, expected):
    setup = Setup()
    call = setup.convocatoria(**fields)
    _, headers = setup.athlete(edad=edad)
    response = setup.apply(call["id"], headers)
    assert response.status_code == expected, response.text
    if expected == 409:
        assert response.json()["detail"].startswith(f"Tu edad ({edad}) no está en el rango")


# -- convocatoria state ------------------------------------------------------------------


def test_unknown_or_malformed_convocatoria():
    setup = Setup()
    _, headers = setup.athlete()
    missing = setup.apply(uuid4(), headers)
    assert missing.status_code == 404 and missing.json() == {"detail": "Convocatoria no encontrada."}
    assert setup.apply("no-es-uuid", headers).status_code == 400


def test_closed_convocatoria_is_409():
    setup = Setup()
    call = setup.convocatoria()
    setup.client.post(f"{CLUBS_PATH}/{setup.club_id}/announcements/{call['id']}/close", headers=setup.owner_headers)
    _, headers = setup.athlete()
    response = setup.apply(call["id"], headers)
    assert response.status_code == 409
    assert response.json() == {"detail": "La convocatoria no está abierta (CERRADA)."}


def test_finished_convocatoria_is_409():
    setup = Setup()
    past = setup.repository.create_convocatoria(
        setup.club_id,
        {"deporte_id": 1, "categoria_edad": "Sub-17", "fecha_prueba": datetime.now(timezone.utc) - timedelta(hours=1),
         "requisitos": ["Ficha médica"], "cupos": 5, "direccion": "Av. Grecia 2001"},
    )
    _, headers = setup.athlete()
    response = setup.apply(past.id, headers)
    assert response.status_code == 409
    assert response.json() == {"detail": "La convocatoria no está abierta (FINALIZADA)."}


def test_convocatoria_of_inactive_club_is_404_but_stays_in_history():
    setup = Setup()
    call = setup.convocatoria()
    _, headers = setup.athlete()
    setup.apply(call["id"], headers)
    setup.repository._clubs[setup.club_id].is_active = False  # noqa: SLF001 - no endpoint deactivates clubs
    _, other = setup.athlete()
    assert setup.apply(call["id"], other).status_code == 404
    history = setup.client.get(MY_PATH, headers=headers).json()
    assert history["total"] == 1 and history["items"][0]["convocatoria"]["id"] == call["id"]


# -- who can apply ---------------------------------------------------------------------------


def test_applying_requires_a_token():
    setup = Setup()
    call = setup.convocatoria()
    assert setup.client.post(f"{CALLS_PATH}/{call['id']}/applications", json={}).status_code == 401
    assert setup.client.get(MY_PATH).status_code == 401


@pytest.mark.parametrize("role", ["club_admin", "admin"])
def test_only_athletes_apply_and_follow_applications(role):
    setup = Setup()
    call = setup.convocatoria()
    headers = token(uuid4(), role)
    response = setup.apply(call["id"], headers)
    assert response.status_code == 403
    assert response.json() == {"detail": "Solo los deportistas pueden postular a convocatorias."}
    assert setup.client.get(MY_PATH, headers=headers).status_code == 403


def test_legacy_athlete_role_usuario_can_apply():
    setup = Setup()
    call = setup.convocatoria()
    assert setup.apply(call["id"], token(uuid4(), "usuario")).status_code == 201


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (HTTPException(403, "Tu perfil deportivo no está disponible: verifica tu correo en SportMatch."), 403),
        (HTTPException(503, "No se pudo obtener tu perfil deportivo. Inténtalo de nuevo."), 503),
    ],
)
def test_users_service_errors_create_nothing(error, expected):
    setup = Setup()
    call = setup.convocatoria()
    setup.users.error = error
    _, headers = setup.athlete()
    response = setup.apply(call["id"], headers)
    assert response.status_code == expected
    assert response.json() == {"detail": error.detail}
    assert setup.client.get(f"{CALLS_PATH}/{call['id']}").json()["cupos_disponibles"] == 2


# -- payload validation ----------------------------------------------------------------------


this_year = date.today().year


@pytest.mark.parametrize(
    "body",
    [
        {"mensaje": "x" * 1001},
        {"antecedentes": [{"institucion": "C", "deporte": "Fútbol", "anio_inicio": 2020}]},
        {"antecedentes": [{"institucion": "Club", "deporte": "Fútbol", "anio_inicio": 2022, "anio_fin": 2020}]},
        {"antecedentes": [{"institucion": "Club", "deporte": "Fútbol", "anio_inicio": this_year + 1}]},
        {"antecedentes": [{"institucion": "Club", "deporte": "Fútbol", "anio_inicio": 1900}]},
        {"antecedentes": [{"institucion": "Club", "deporte": "Fútbol"}]},
        {"antecedentes": [{"institucion": "Club", "deporte": "Fútbol", "anio_inicio": 2020, "logros": "x" * 301}]},
        {"antecedentes": [ANTECEDENTES[1]] * 11},
        {"antecedentes": "Jugué 5 años"},
        {"estado": "ACEPTADO"},
        {"usuario_id": str(uuid4())},
    ],
)
def test_invalid_application_payload_is_400(body):
    setup = Setup()
    call = setup.convocatoria()
    _, headers = setup.athlete()
    assert setup.apply(call["id"], headers, body).status_code == 400
    assert setup.client.get(MY_PATH, headers=headers).json()["total"] == 0


# -- tracking -----------------------------------------------------------------------------------


def test_athlete_follows_only_their_own_applications_newest_first():
    setup = Setup()
    first_call = setup.convocatoria(titulo="Primera")
    second_call = setup.convocatoria(titulo="Segunda")
    _, headers = setup.athlete()
    _, other = setup.athlete()
    first = setup.apply(first_call["id"], headers).json()
    second = setup.apply(second_call["id"], headers).json()
    setup.apply(first_call["id"], other)

    mine = setup.client.get(MY_PATH, headers=headers).json()
    assert mine["total"] == 2
    assert [p["id"] for p in mine["items"]] == [second["id"], first["id"]]
    assert [p["convocatoria"]["titulo"] for p in mine["items"]] == ["Segunda", "Primera"]

    setup.repository.set_postulacion_estado(UUID(first["id"]), "PRESELECCIONADO")
    reviewing = setup.client.get(MY_PATH, headers=headers, params={"estado": "PRESELECCIONADO"}).json()
    assert [p["id"] for p in reviewing["items"]] == [first["id"]]
    assert reviewing["items"][0]["estado"] == "PRESELECCIONADO"
    paged = setup.client.get(MY_PATH, headers=headers, params={"page": 2, "page_size": 1}).json()
    assert [p["id"] for p in paged["items"]] == [first["id"]]


@pytest.mark.parametrize(
    "params", [{"estado": "EN_REVISION"}, {"estado": "PRESELECCIONADA"}, {"estado": "pendiente"}, {"page_size": 51}]
)
def test_invalid_tracking_filters_are_400(params):
    setup = Setup()
    _, headers = setup.athlete()
    assert setup.client.get(MY_PATH, headers=headers, params=params).status_code == 400


# -- UsersClient (Ms_Users over HTTP) -------------------------------------------------------------


CARD = {
    "user_id": "ignored", "nombre": "Ana", "apellido_inicial": "T", "edad": 16,
    "foto_perfil": None, "biografia": None, "deportes": [{"deporte_codigo": "futbol", "nivel": 3}],
    "compatibilidad": 80, "distancia_km": None, "nivel_coincidente": False,
}


def users_client(handler) -> UsersClient:
    return UsersClient(httpx.Client(base_url="http://ms-users.test", transport=httpx.MockTransport(handler)))


def test_users_client_asks_for_the_athletes_own_card_with_their_token():
    actor = Actor(id=uuid4(), roles=frozenset({"player"}), token="the-token")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["auth"] = request.headers["Authorization"]
        return httpx.Response(200, json=CARD)

    profile = users_client(handler).athlete_profile(actor)
    assert seen == {"path": f"/api/v1/users/athletes/{actor.id}", "auth": "Bearer the-token"}
    assert profile.model_dump() == {
        "nombre": "Ana", "apellido_inicial": "T", "edad": 16, "foto_perfil": None,
        "biografia": None, "deportes": [{"deporte_codigo": "futbol", "nivel": 3}],
    }


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (httpx.Response(401), 401),
        (httpx.Response(403), 403),
        (httpx.Response(404), 403),
        (httpx.Response(500), 503),
        (httpx.Response(200, text="<html>"), 503),
        (httpx.Response(200, json={"nombre": "Ana"}), 503),
        (httpx.Response(200, json=[1, 2]), 503),
    ],
)
def test_users_client_maps_ms_users_answers(response, expected):
    actor = Actor(id=uuid4(), roles=frozenset({"player"}), token="t")
    with pytest.raises(HTTPException) as error:
        users_client(lambda request: response).athlete_profile(actor)
    assert error.value.status_code == expected


def test_users_client_maps_connection_errors_to_503():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(HTTPException) as error:
        users_client(handler).athlete_profile(Actor(id=uuid4(), roles=frozenset({"player"}), token="t"))
    assert error.value.status_code == 503
