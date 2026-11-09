"""HU-54: límites del plan gratuito en Ms-club y club destacado en el directorio.

Club gratis: 2 convocatorias abiertas a la vez, 4 comunicados cada 24 h, 1 canal.
Deportista gratis: 5 postulaciones a convocatorias cada 24 h. Con plan pagado: sin límite (canales: 20)."""
from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from test_club_admin import nuevo_rut, token  # also sets JWT_SECRET before the app is imported
from test_postulaciones import CHILE, CLUBS_PATH, FUTBOL, FakeUsers, Setup

from app.main import create_app
from app.repositories.mock_club_repository import MockClubRepository
from app.services.entitlements_client import EntitlementsClient, EntitlementsSettings

TOKEN = "internal-token-for-tests-at-least-32-bytes-long"
LIMITES = {
    "usuario": {"GRATIS": {"postulaciones_convocatorias": (5, 24)},
                "PRO": {"postulaciones_convocatorias": (None, 24)}},
    "club": {"GRATIS": {"convocatorias_abiertas": (2, None), "comunicados": (4, 24), "canales": (1, None)},
             "PRO": {"convocatorias_abiertas": (None, None), "comunicados": (None, 24), "canales": (20, None)}},
}


class Payments:
    """ms-pago stand-in: accounts in `pro` have a paid plan; status "down" refuses connections."""

    def __init__(self) -> None:
        self.pro: set[UUID] = set()
        self.status: int | str = 200
        self.calls = 0

    def body(self, tipo: str, cuenta_id: UUID) -> dict:
        plan = "PRO" if cuenta_id in self.pro else "GRATIS"
        return {"tipo": tipo, "id": str(cuenta_id), "plan": plan, "vigente_hasta": None,
                "beneficios": ["destacado", "filtros_avanzados"] if plan == "PRO" else [],
                "limites": {k: {"maximo": m, "ventana_horas": v} for k, (m, v) in LIMITES[tipo][plan].items()}}

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if request.headers.get("X-Internal-Service-Token") != TOKEN:
            return httpx.Response(401)
        if self.status == "down":
            raise httpx.ConnectError("refused", request=request)
        if self.status != 200:
            return httpx.Response(self.status)
        _, _, _, tipo, rest = request.url.path.strip("/").split("/", 4)
        if rest == "destacados":
            return httpx.Response(200, json={"ids": [str(i) for i in self.pro if tipo == "club"]})
        if rest == "batch":
            ids = json.loads(request.content)["ids"]
            return httpx.Response(200, json={"items": [self.body(tipo, UUID(i)) for i in ids]})
        return httpx.Response(200, json=self.body(tipo, UUID(rest)))

    def client(self) -> EntitlementsClient:
        # Cache disabled so a test can change the plan between requests.
        return EntitlementsClient(EntitlementsSettings("http://pago.test", TOKEN, 1, 0),
                                  httpx.Client(base_url="http://pago.test", transport=httpx.MockTransport(self.handler)))


class Limited(Setup):
    def __init__(self, payments: Payments | None = None) -> None:
        self.payments = payments or Payments()
        self.repository = MockClubRepository(sports=[FUTBOL])
        self.users = FakeUsers()
        self.client = TestClient(create_app(repository=self.repository, users=self.users,
                                            entitlements=self.payments.client()))
        self.owner_headers = token(uuid4())
        response = self.client.post(CLUBS_PATH, headers=self.owner_headers,
                                    json={"nombre": "Club Ñuñoa", "rut": nuevo_rut(), "deporte_ids": [1]})
        assert response.status_code == 201, response.text
        self.club_id = UUID(response.json()["id"])

    def post_convocatoria(self, **fields):
        body = {"deporte_id": 1, "titulo": "Prueba Sub-17", "categoria_edad": "Sub-17", "edad_minima": 15,
                "edad_maxima": 17, "fecha_prueba": datetime.combine(date.today() + timedelta(days=10), time(10),
                                                                    tzinfo=CHILE).isoformat(),
                "requisitos": ["Ficha médica"], "cupos": 30, "direccion": "Av. Grecia 2001", **fields}
        return self.client.post(f"{CLUBS_PATH}/{self.club_id}/announcements", headers=self.owner_headers, json=body)

    def notice(self):
        return self.client.post(f"{CLUBS_PATH}/{self.club_id}/notices", headers=self.owner_headers,
                                json={"titulo": "Aviso", "contenido": "Entrenamiento el sábado.", "audiencia": "TODOS"})

    def canal(self, nombre="Plantel"):
        return self.client.post(f"{CLUBS_PATH}/{self.club_id}/canales", headers=self.owner_headers,
                                json={"nombre": nombre})


def limit_body(response, limite, usado):
    assert response.status_code == 402, response.text
    body = response.json()
    assert (body["codigo"], body["limite"], body["usado"]) == ("LIMITE_PLAN", limite, usado)
    assert f"Te quedan 0 de {limite}" in body["detail"]
    return body


# -- club ------------------------------------------------------------------------------------


def test_free_club_has_two_open_convocatorias_and_closing_one_frees_a_slot():
    setup = Limited()
    first = setup.post_convocatoria().json()
    assert setup.post_convocatoria().status_code == 201
    body = limit_body(setup.post_convocatoria(), 2, 2)
    assert body["se_libera_en"] is None and body["recurso"] == "convocatorias_abiertas"
    closed = setup.client.post(f"{CLUBS_PATH}/{setup.club_id}/announcements/{first['id']}/close",
                               headers=setup.owner_headers)
    assert closed.status_code == 200, closed.text
    assert setup.post_convocatoria().status_code == 201


def test_free_club_publishes_four_notices_per_24_hours():
    setup = Limited()
    for _ in range(4):
        assert setup.notice().status_code == 201
    body = limit_body(setup.notice(), 4, 4)
    oldest = min(c.fecha_creacion for c in setup.repository._comunicados.values())
    assert datetime.fromisoformat(body["se_libera_en"].replace("Z", "+00:00")) == oldest + timedelta(hours=24)
    for comunicado in list(setup.repository._comunicados.values()):
        object.__setattr__(comunicado, "fecha_creacion", comunicado.fecha_creacion - timedelta(hours=25))
    assert setup.notice().status_code == 201


def test_free_club_has_one_channel_and_pro_up_to_the_technical_cap():
    setup = Limited()
    assert setup.canal("Uno").status_code == 201
    limit_body(setup.canal("Dos"), 1, 1)
    setup.payments.pro.add(setup.club_id)
    assert setup.canal("Dos").status_code == 201


def test_paid_club_has_no_limits():
    setup = Limited()
    setup.payments.pro.add(setup.club_id)
    for _ in range(4):
        assert setup.post_convocatoria().status_code == 201
    for _ in range(6):
        assert setup.notice().status_code == 201


@pytest.mark.parametrize("status", ["down", 503])
def test_fail_open_when_ms_pago_is_down(status, caplog):
    payments = Payments()
    payments.status = status
    setup = Limited(payments)
    for _ in range(3):
        assert setup.post_convocatoria().status_code == 201
    for _ in range(5):
        assert setup.notice().status_code == 201
    assert setup.canal("Uno").status_code == 201 and setup.canal("Dos").status_code == 201
    assert "fail-open" in caplog.text


# -- athlete ---------------------------------------------------------------------------------


def test_free_athlete_applies_to_five_convocatorias_per_24_hours_and_pro_has_no_limit():
    setup = Limited()
    setup.payments.pro.add(setup.club_id)  # the club may open many convocatorias
    calls = [setup.post_convocatoria().json()["id"] for _ in range(7)]
    athlete, headers = setup.athlete()
    for call in calls[:5]:
        assert setup.apply(call, headers).status_code == 201
    limit_body(setup.apply(calls[5], headers), 5, 5)
    setup.payments.pro.add(athlete)
    assert setup.apply(calls[5], headers).status_code == 201


# -- directory -------------------------------------------------------------------------------


def test_directory_marks_featured_clubs():
    setup = Limited()
    other = setup.client.post(CLUBS_PATH, headers=token(uuid4()),
                              json={"nombre": "Club Macul", "rut": nuevo_rut(), "deporte_ids": [1]}).json()
    setup.payments.pro.add(setup.club_id)
    items = {item["id"]: item for item in setup.client.get(CLUBS_PATH).json()["items"]}
    assert items[str(setup.club_id)]["es_destacado"] is True
    assert items[other["id"]]["es_destacado"] is False
    setup.payments.status = "down"
    assert all(item["es_destacado"] is False for item in setup.client.get(CLUBS_PATH).json()["items"])


def test_without_ms_pago_nothing_is_limited_or_featured():
    setup = Setup()
    for _ in range(3):
        setup.convocatoria()
    assert all(item["es_destacado"] is False for item in setup.client.get(CLUBS_PATH).json()["items"])


def test_repeating_an_application_at_the_limit_is_409_not_402():
    setup = Limited()
    setup.payments.pro.add(setup.club_id)
    calls = [setup.post_convocatoria().json()["id"] for _ in range(6)]
    _, headers = setup.athlete()
    for call in calls[:5]:
        assert setup.apply(call, headers).status_code == 201
    repeated = setup.apply(calls[0], headers)
    assert repeated.status_code == 409 and repeated.json()["detail"] == "Ya postulaste a esta convocatoria."
    limit_body(setup.apply(calls[5], headers), 5, 5)


def test_featured_clubs_go_first_on_every_page():
    setup = Limited()
    names = ["Club A", "Club B", "Club C", "Club Zeta"]
    ids = {}
    for name in names:
        ids[name] = setup.client.post(CLUBS_PATH, headers=token(uuid4()),
                                      json={"nombre": name, "rut": nuevo_rut(), "deporte_ids": [1]}).json()["id"]
    setup.payments.pro.add(UUID(ids["Club Zeta"]))  # last by name, first in the directory
    first = setup.client.get(CLUBS_PATH, params={"page_size": 2}).json()
    assert first["items"][0]["nombre"] == "Club Zeta" and first["items"][0]["es_destacado"] is True
    everything = [item["nombre"] for page in (1, 2, 3)
                  for item in setup.client.get(CLUBS_PATH, params={"page_size": 2, "page": page}).json()["items"]]
    assert everything == ["Club Zeta", "Club A", "Club B", "Club C", "Club Ñuñoa"]
    setup.payments.status = "down"
    assert setup.client.get(CLUBS_PATH, params={"page_size": 2}).json()["items"][0]["nombre"] == "Club A"


def test_plan_usage_for_the_club_and_for_the_athlete():
    setup = Limited()
    setup.post_convocatoria()
    setup.notice()
    setup.canal("Uno")
    body = setup.client.get(f"{CLUBS_PATH}/plan-usage/me", headers=setup.owner_headers).json()
    assert body["tipo"] == "club" and body["plan"] == "GRATIS" and body["id"] == str(setup.club_id)
    items = {item["recurso"]: item for item in body["items"]}
    assert (items["convocatorias_abiertas"]["limite"], items["convocatorias_abiertas"]["usado"]) == (2, 1)
    assert (items["comunicados"]["limite"], items["comunicados"]["usado"]) == (4, 1)
    assert (items["canales"]["limite"], items["canales"]["usado"]) == (1, 1)
    for _ in range(3):
        setup.notice()
    items = {i["recurso"]: i for i in setup.client.get(f"{CLUBS_PATH}/plan-usage/me",
                                                       headers=setup.owner_headers).json()["items"]}
    assert items["comunicados"]["usado"] == 4 and items["comunicados"]["se_libera_en"] is not None

    _, headers = setup.athlete()
    call = setup.post_convocatoria().json()["id"]
    assert setup.apply(call, headers).status_code == 201
    athlete = setup.client.get(f"{CLUBS_PATH}/plan-usage/me", headers=headers).json()
    assert athlete["tipo"] == "usuario"
    assert athlete["items"] == [{"recurso": "postulaciones_convocatorias", "limite": 5, "usado": 1,
                                 "ventana_horas": 24, "se_libera_en": None}]
    setup.payments.status = "down"
    unknown = setup.client.get(f"{CLUBS_PATH}/plan-usage/me", headers=headers).json()
    assert unknown["plan"] is None and unknown["items"][0]["limite"] is None
    assert setup.client.get(f"{CLUBS_PATH}/plan-usage/me").status_code == 401
