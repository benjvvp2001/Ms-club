"""HU-56 (panel web): reactivar un plan o convenio, suscripciones de un plan, plan base, fecha y motivo de la
desactivación y duración "sin vencimiento". Sin base de datos real (repositorio en memoria y auditor falso); lo mismo
contra PostgreSQL está en test_planes_admin_postgres.py."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from test_club_admin import token  # also sets JWT_SECRET before the app is imported
from test_pagos import SETTINGS
from test_planes_admin import ADMIN, PAY, Gestion

from app.main import create_app
from app.repositories.base import StoredClub, StoredPlan


class FakeNames:
    def __init__(self, names=None):
        self.names_by_id = names or {}
        self.asked: list[list] = []

    def names(self, ids):
        self.asked.append(list(ids))
        return {i: self.names_by_id[i] for i in ids if i in self.names_by_id}


def gestion(planes=None, names=None) -> Gestion:
    g = Gestion(planes=planes)
    g.names = names if names is not None else FakeNames()
    g.client = TestClient(create_app(repository=g.repository, webpay=g.webpay, webpay_settings=SETTINGS,
                                     plan_auditor=g.auditor, account_names=g.names))
    return g


def buy_as(g: Gestion, plan_id: int, athlete_id):
    headers = token(athlete_id, "player")
    started = g.client.post(f"{PAY}/webpay/initiate", headers=headers, json={"plan_id": plan_id})
    assert started.status_code == 201, started.text
    g.webpay.decide(started.json()["token_ws"], "aprobar")
    assert g.client.post(f"{PAY}/webpay/commit", headers=headers, json={"token_ws": started.json()["token_ws"]}).status_code == 200


def deactivated(g: Gestion, **changes) -> int:
    plan_id = g.create(**changes).json()["id"]
    assert g.client.post(f"{ADMIN}/{plan_id}/deactivate", headers=g.admin, json={"motivo": "Fin de temporada"}).status_code == 200
    return plan_id


# -- reactivar -----------------------------------------------------------------------------------------


def test_reactivating_puts_the_plan_back_in_the_catalog_and_clears_the_deactivation():
    g = gestion()
    plan_id = deactivated(g)
    inactive = g.plans(estado="inactivo").json()["items"][0]
    assert inactive["is_active"] is False and inactive["motivo_desactivacion"] == "Fin de temporada"
    assert datetime.fromisoformat(inactive["fecha_desactivacion"]) <= datetime.now(timezone.utc)
    assert g.client.get(f"{PAY}/plans").json() == []

    response = g.client.post(f"{ADMIN}/{plan_id}/reactivate", headers=g.admin, json={"motivo": "Vuelve la oferta"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["is_active"] is True and body["fecha_desactivacion"] is None and body["motivo_desactivacion"] is None
    assert [p["id"] for p in g.client.get(f"{PAY}/plans").json()] == [plan_id]
    last = g.auditor.entries[-1]
    assert (last["action"], last["plan_id"], last["actor"], last["motivo"]) == (
        "PLAN_REACTIVATED", plan_id, g.admin_id, "Vuelve la oferta")
    assert last["metadata"]["campos"] == "is_active"


def test_reactivating_without_a_body_records_a_default_reason():
    g = gestion()
    plan_id = deactivated(g)
    assert g.client.post(f"{ADMIN}/{plan_id}/reactivate", headers=g.admin).status_code == 200
    assert g.auditor.entries[-1]["motivo"] == "Reactivación de plan o convenio"


def test_only_a_deactivated_plan_can_be_reactivated():
    g = gestion()
    plan_id = g.create().json()["id"]
    again = g.client.post(f"{ADMIN}/{plan_id}/reactivate", headers=g.admin)
    assert again.status_code == 409 and "ya está activo" in again.json()["detail"]
    assert g.client.post(f"{ADMIN}/9999/reactivate", headers=g.admin).status_code == 404


def test_the_name_must_still_be_free_among_active_plans():
    g = gestion()
    old_id = deactivated(g)
    g.create()                                   # another active plan took the name "Pro Mensual"
    entries = len(g.auditor.entries)
    blocked = g.client.post(f"{ADMIN}/{old_id}/reactivate", headers=g.admin)
    assert blocked.status_code == 409 and "ya existe un plan activo con ese nombre" in blocked.json()["detail"]
    assert g.plans(estado="inactivo").json()["total"] == 1       # still deactivated
    assert len(g.auditor.entries) == entries                      # and nothing was audited


def test_if_the_audit_fails_the_plan_stays_deactivated_and_the_answer_is_503():
    g = gestion()
    plan_id = deactivated(g)
    g.auditor.down = True
    response = g.client.post(f"{ADMIN}/{plan_id}/reactivate", headers=g.admin)
    assert response.status_code == 503 and "el cambio no se guardó" in response.json()["detail"]
    g.auditor.down = False
    assert g.plans(estado="inactivo").json()["total"] == 1 and g.client.get(f"{PAY}/plans").json() == []


def test_without_an_auditor_configured_a_plan_is_not_reactivated():
    g = gestion()
    plan_id = deactivated(g)
    bare = TestClient(create_app(repository=g.repository, webpay=g.webpay, webpay_settings=SETTINGS))
    assert bare.post(f"{ADMIN}/{plan_id}/reactivate", headers=g.admin).status_code == 503
    assert g.plans(estado="inactivo").json()["total"] == 1


@pytest.mark.parametrize("role", ["player", "club_admin"])
def test_a_non_admin_cannot_reactivate_or_list_subscriptions_even_if_the_plan_does_not_exist(role):
    g = gestion()
    headers = token(uuid4(), role)
    for method, path in (("POST", f"{ADMIN}/9999/reactivate"), ("GET", f"{ADMIN}/9999/subscriptions")):
        assert g.client.request(method, path, headers=headers).status_code == 403
        assert g.client.request(method, path).status_code == 401


def test_a_reactivated_plan_can_be_bought_again():
    g = gestion()
    plan_id = deactivated(g)
    assert g.client.post(f"{PAY}/webpay/initiate", headers=token(uuid4(), "player"), json={"plan_id": plan_id}).status_code == 404
    g.client.post(f"{ADMIN}/{plan_id}/reactivate", headers=g.admin)
    buy_as(g, plan_id, uuid4())


# -- fecha y motivo de la desactivación, plan base --------------------------------------------------------


def test_deactivating_stores_the_date_and_the_reason_and_a_missing_reason_stays_empty():
    g = gestion()
    first = g.create().json()["id"]
    second = g.create(nombre="Otro plan").json()["id"]
    done = g.client.post(f"{ADMIN}/{first}/deactivate", headers=g.admin, json={"motivo": "Fin de temporada"}).json()
    assert done["motivo_desactivacion"] == "Fin de temporada" and done["fecha_desactivacion"] is not None
    bare = g.client.post(f"{ADMIN}/{second}/deactivate", headers=g.admin).json()
    assert bare["motivo_desactivacion"] is None and bare["fecha_desactivacion"] is not None
    active = g.create(nombre="Activo").json()
    assert active["fecha_desactivacion"] is None and active["es_base"] is False


def test_the_base_plan_cannot_be_deactivated():
    base = StoredPlan(id=1, nombre="Gratuito", tipo="USUARIO", precio=0, duracion_dias=None, es_base=True,
                      fecha_creacion=datetime.now(timezone.utc))
    g = gestion(planes=[base])
    listed = g.plans().json()["items"][0]
    assert listed["es_base"] is True and listed["duracion_dias"] is None
    blocked = g.client.post(f"{ADMIN}/1/deactivate", headers=g.admin, json={"motivo": "Probando"})
    assert blocked.status_code == 409 and "plan base" in blocked.json()["detail"]
    assert g.plans(estado="activo").json()["total"] == 1 and g.auditor.entries == []


# -- duración sin vencimiento -------------------------------------------------------------------------------


def test_a_free_plan_can_have_no_expiry_and_a_paid_one_cannot():
    g = gestion()
    free = g.create(nombre="Gratuito sin vencimiento", precio=0, duracion_dias=None, beneficios=["Chat"])
    assert free.status_code == 201 and free.json()["duracion_dias"] is None
    assert g.client.get(f"{PAY}/plans").json()[0]["duracion_dias"] is None
    paid = g.create(nombre="De pago sin duración", precio=4990, duracion_dias=None)
    assert paid.status_code == 400                          # the service answers 400 to a body that breaks the rules


def test_editing_cannot_leave_a_paid_plan_without_a_duration():
    g = gestion()
    paid = g.create().json()["id"]
    to_none = g.client.patch(f"{ADMIN}/{paid}", headers=g.admin, json={"duracion_dias": None})
    assert to_none.status_code == 400 and "necesita una duración" in to_none.json()["detail"]
    free = g.create(nombre="Gratis", precio=0, duracion_dias=None).json()["id"]
    price = g.client.patch(f"{ADMIN}/{free}", headers=g.admin, json={"precio": 1000})
    assert price.status_code == 400 and "necesita una duración" in price.json()["detail"]
    both = g.client.patch(f"{ADMIN}/{free}", headers=g.admin, json={"precio": 1000, "duracion_dias": 30})
    assert both.status_code == 200 and both.json()["duracion_dias"] == 30
    back = g.client.patch(f"{ADMIN}/{both.json()['id']}", headers=g.admin, json={"precio": 0, "duracion_dias": None})
    assert back.status_code == 200 and back.json()["duracion_dias"] is None


# -- suscripciones de un plan --------------------------------------------------------------------------------


def test_the_subscriptions_of_a_plan_show_the_short_name_state_and_dates():
    ana, luis = uuid4(), uuid4()
    g = gestion(names=FakeNames({ana: "Ana T."}))
    plan_id = g.create().json()["id"]
    buy_as(g, plan_id, ana)
    buy_as(g, plan_id, luis)                                   # Ms_Users does not know this name
    page = g.client.get(f"{ADMIN}/{plan_id}/subscriptions", headers=g.admin)
    assert page.status_code == 200, page.text
    body = page.json()
    assert (body["total"], body["pagina"], body["limit"]) == (2, 1, 20)
    by_name = {item["suscriptor"]: item for item in body["items"]}
    assert set(by_name) == {"Ana T.", None}
    item = by_name["Ana T."]
    assert (item["estado"], item["tipo_suscriptor"], item["precio_pagado"]) == ("ACTIVE", "USUARIO", 4990)
    assert datetime.fromisoformat(item["fecha_fin"]) - datetime.fromisoformat(item["fecha_inicio"]) >= timedelta(days=29)
    assert set(item) == {"id", "suscriptor", "tipo_suscriptor", "estado", "plan_nombre", "precio_pagado", "fecha_inicio", "fecha_fin"}
    assert item["plan_nombre"] == "Pro Mensual"
    assert "@" not in page.text
    assert g.names.asked and set(g.names.asked[-1]) == {ana, luis}
    assert g.plans().json()["items"][0]["suscripciones_vigentes"] == 2


def test_a_club_subscriber_is_shown_by_the_name_of_the_club():
    g = gestion()
    plan_id = g.create(nombre="Plan de club", tipo="CLUB", precio=19990).json()["id"]
    club = StoredClub(id=uuid4(), nombre="Club Deportivo Norte")
    g.repository._clubs[club.id] = club
    now = datetime.now(timezone.utc)
    g.repository._suscripciones[("c", club.id)] = {
        "id": uuid4(), "usuario_id": None, "club_id": club.id, "plan": g.repository.get_plan_any(plan_id),
        "fecha_inicio": now - timedelta(days=5), "fecha_fin": now + timedelta(days=25), "ultima_transaccion_id": None}
    item = g.client.get(f"{ADMIN}/{plan_id}/subscriptions", headers=g.admin).json()["items"][0]
    assert (item["suscriptor"], item["tipo_suscriptor"], item["estado"]) == ("Club Deportivo Norte", "CLUB", "ACTIVE")
    assert g.names.asked == []                                  # no athlete: Ms_Users is not asked


def test_the_subscriptions_can_be_filtered_by_state_and_are_paged():
    g = gestion()
    plan_id = g.create().json()["id"]
    athletes = [uuid4() for _ in range(3)]
    for athlete in athletes:
        buy_as(g, plan_id, athlete)
    g.repository.set_suscripcion_fin(usuario_id=athletes[0], fecha_fin=datetime.now(timezone.utc) - timedelta(days=1))
    def ask(**params):
        return g.client.get(f"{ADMIN}/{plan_id}/subscriptions", headers=g.admin, params=params).json()
    assert ask(estado="vigente")["total"] == 2 and ask(estado="vencida")["total"] == 1 and ask()["total"] == 3
    assert [i["estado"] for i in ask()["items"]] == ["ACTIVE", "ACTIVE", "EXPIRED"]    # the current ones first
    first, second = ask(limit=2), ask(limit=2, pagina=2)
    assert len(first["items"]) == 2 and len(second["items"]) == 1
    assert {i["id"] for i in first["items"]}.isdisjoint({i["id"] for i in second["items"]})


def test_the_subscription_limits_and_filters_are_validated():
    g = gestion()
    plan_id = g.create().json()["id"]
    url = f"{ADMIN}/{plan_id}/subscriptions"
    assert g.client.get(url, headers=g.admin, params={"limit": 100}).status_code == 200
    for params in ({"limit": 101}, {"limit": 0}, {"pagina": 0}, {"estado": "otra"}, {"pagina": "x"}):
        assert g.client.get(url, headers=g.admin, params=params).status_code == 400, params
    assert g.client.get(f"{ADMIN}/9999/subscriptions", headers=g.admin).status_code == 404
    assert g.client.get(f"{ADMIN}/abc/subscriptions", headers=g.admin).status_code == 400
    assert g.client.post(url, headers=g.admin).status_code == 405


def test_the_plan_of_a_deactivated_offer_keeps_listing_its_subscribers():
    ana = uuid4()
    g = gestion(names=FakeNames({ana: "Ana T."}))
    plan_id = g.create().json()["id"]
    buy_as(g, plan_id, ana)
    g.client.post(f"{ADMIN}/{plan_id}/deactivate", headers=g.admin)
    items = g.client.get(f"{ADMIN}/{plan_id}/subscriptions", headers=g.admin).json()["items"]
    assert [i["suscriptor"] for i in items] == ["Ana T."] and items[0]["estado"] == "ACTIVE"
