"""HU-56: gestionar los planes y convenios (administrador de la plataforma). Sin base de datos real: el repositorio
en memoria y un auditor falso; el test contra PostgreSQL está en test_planes_admin_postgres.py."""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from test_club_admin import nuevo_rut, token  # also sets JWT_SECRET before the app is imported
from test_pagos import SETTINGS, Pagos

from app.main import create_app
from app.repositories.base import ClubSport, StoredPlan
from app.repositories.mock_club_repository import MockClubRepository
from app.services.plan_audit import PlanAuditUnavailable
from app.services.webpay import MockWebpay

ADMIN = "/api/v1/payments/admin/plans"
PAY = "/api/v1/payments"
PLAN_VALIDO = {"nombre": "Pro Mensual", "tipo": "USUARIO", "precio": 4990, "duracion_dias": 30,
               "beneficios": ["Actividades sin límite", "Filtros avanzados"], "descripcion": "Para quien entrena seguido"}


class FakeAuditor:
    def __init__(self) -> None:
        self.entries: list[dict] = []
        self.down = False

    def record(self, *, actor_id, action, plan_id, motivo, metadata) -> None:
        if self.down:
            raise PlanAuditUnavailable
        self.entries.append({"actor": actor_id, "action": action, "plan_id": plan_id, "motivo": motivo,
                             "metadata": metadata})


class Gestion:
    def __init__(self, planes: list[StoredPlan] | None = None, repository=None) -> None:
        self.repository = repository or MockClubRepository(sports=[ClubSport(1, "Fútbol")], planes=planes or [])
        self.auditor = FakeAuditor()
        self.webpay = MockWebpay(SETTINGS.public_url)
        self.client = TestClient(create_app(repository=self.repository, webpay=self.webpay,
                                            webpay_settings=SETTINGS, plan_auditor=self.auditor))
        self.admin_id = uuid4()
        self.admin = token(self.admin_id, "admin")

    def create(self, **changes):
        return self.client.post(ADMIN, headers=self.admin, json={**PLAN_VALIDO, **changes})

    def plans(self, **params):
        return self.client.get(ADMIN, headers=self.admin, params=params)

    def buy(self, plan_id: int):
        """A new athlete pays the plan (simulated Webpay)."""
        athlete_id, headers = uuid4(), token(uuid4(), "player")
        started = self.client.post(f"{PAY}/webpay/initiate", headers=headers, json={"plan_id": plan_id})
        assert started.status_code == 201, started.text
        self.webpay.decide(started.json()["token_ws"], "aprobar")
        paid = self.client.post(f"{PAY}/webpay/commit", headers=headers, json={"token_ws": started.json()["token_ws"]})
        assert paid.status_code == 200, paid.text
        return headers, paid.json()

    def subscription(self, headers):
        return self.client.get(f"{PAY}/subscription/me", headers=headers)


# -- crear, editar, desactivar --------------------------------------------------------------------


def test_the_admin_creates_a_plan_and_a_convenio():
    g = Gestion()
    created = g.create()
    assert created.status_code == 201, created.text
    body = created.json()
    assert (body["nombre"], body["tipo"], body["categoria"], body["precio"], body["duracion_dias"]) == (
        "Pro Mensual", "USUARIO", "PLAN", 4990, 30)
    assert body["beneficios"] == ["Actividades sin límite", "Filtros avanzados"]
    assert body["is_active"] is True and body["suscripciones_vigentes"] == 0
    convenio = g.create(nombre="Convenio Municipalidad de Ñuñoa", tipo="CLUB", categoria="CONVENIO", precio=0,
                        duracion_dias=365, beneficios=[])
    assert convenio.status_code == 201 and convenio.json()["categoria"] == "CONVENIO" and convenio.json()["precio"] == 0
    listed = g.client.get(f"{PAY}/plans").json()
    assert {p["nombre"] for p in listed} == {"Pro Mensual", "Convenio Municipalidad de Ñuñoa"}
    assert next(p for p in listed if p["categoria"] == "CONVENIO")["tipo"] == "CLUB"


def test_the_admin_edits_only_what_is_sent():
    g = Gestion()
    plan_id = g.create().json()["id"]
    edited = g.client.patch(f"{ADMIN}/{plan_id}", headers=g.admin, json={"precio": 5990, "beneficios": ["Uno nuevo"]})
    assert edited.status_code == 200, edited.text
    body = edited.json()
    assert (body["precio"], body["beneficios"], body["nombre"], body["duracion_dias"]) == (5990, ["Uno nuevo"], "Pro Mensual", 30)
    cleared = g.client.patch(f"{ADMIN}/{plan_id}", headers=g.admin, json={"descripcion": None})
    assert cleared.status_code == 200 and cleared.json()["descripcion"] is None


def test_an_edit_that_changes_nothing_writes_and_audits_nothing():
    g = Gestion()
    plan_id = g.create().json()["id"]
    before = len(g.auditor.entries)
    same = g.client.patch(f"{ADMIN}/{plan_id}", headers=g.admin, json={"precio": 4990})
    assert same.status_code == 200 and len(g.auditor.entries) == before


def test_the_type_and_the_category_cannot_be_edited():
    g = Gestion()
    plan_id = g.create().json()["id"]
    for field, value in (("tipo", "CLUB"), ("categoria", "CONVENIO"), ("is_active", False), ("id", 99)):
        assert g.client.patch(f"{ADMIN}/{plan_id}", headers=g.admin, json={field: value}).status_code == 400, field
    assert g.client.patch(f"{ADMIN}/{plan_id}", headers=g.admin, json={}).status_code == 400
    assert g.client.patch(f"{ADMIN}/{plan_id}", headers=g.admin, json={"motivo": "Solo motivo"}).status_code == 400


def test_the_admin_deactivates_a_plan_and_it_is_never_deleted():
    g = Gestion()
    plan_id = g.create().json()["id"]
    done = g.client.post(f"{ADMIN}/{plan_id}/deactivate", headers=g.admin, json={"motivo": "Se retira la oferta"})
    assert done.status_code == 200 and done.json()["is_active"] is False
    assert g.repository.get_plan_any(plan_id) is not None                       # logical delete: the row is still there
    assert g.client.post(f"{ADMIN}/{plan_id}/deactivate", headers=g.admin).status_code == 409   # already off
    assert g.client.patch(f"{ADMIN}/{plan_id}", headers=g.admin, json={"precio": 1}).status_code == 409
    assert g.client.delete(f"{ADMIN}/{plan_id}", headers=g.admin).status_code == 405             # no DELETE route
    assert g.client.post(f"{ADMIN}/9999/deactivate", headers=g.admin).status_code == 404


def test_the_name_is_unique_among_active_plans_and_a_deactivated_one_frees_it():
    g = Gestion()
    first = g.create().json()["id"]
    assert g.create().status_code == 409
    assert g.create(nombre="  pro   MENSUAL ").status_code == 409                # case and spaces do not make it different
    other = g.create(nombre="Otro plan").json()["id"]
    assert g.client.patch(f"{ADMIN}/{other}", headers=g.admin, json={"nombre": "Pro Mensual"}).status_code == 409
    assert g.client.post(f"{ADMIN}/{first}/deactivate", headers=g.admin).status_code == 200
    assert g.create().status_code == 201                                          # the retired name can be used again


# -- validaciones ---------------------------------------------------------------------------------


@pytest.mark.parametrize("changes", [
    {"nombre": ""}, {"nombre": "ab"}, {"nombre": "x" * 61}, {"nombre": "Plan\x00malo"},
    {"nombre": "Plan <script>"}, {"nombre": "a@b.cl plan"}, {"nombre": None},
    {"precio": -1}, {"precio": 10_000_001}, {"precio": 49.9}, {"precio": 4990.0}, {"precio": "4990"},
    {"precio": True}, {"precio": None}, {"precio": 1e999}, {"precio": float("nan")}, {"precio": 10 ** 30},
    {"duracion_dias": 0}, {"duracion_dias": -5}, {"duracion_dias": 367}, {"duracion_dias": 30.5}, {"duracion_dias": True},
    {"tipo": "ADMIN"}, {"tipo": None}, {"categoria": "OTRO"},
    {"beneficios": "uno"}, {"beneficios": ["ab"]}, {"beneficios": ["x" * 121]}, {"beneficios": ["a" * 5] * 11},
    {"beneficios": ["Beneficio\x00"]}, {"beneficios": ["Igual", "igual"]},
    {"beneficios": [1]}, {"descripcion": "d" * 256}, {"descripcion": "con\x00nul"},
    {"motivo": "ab"}, {"motivo": "x" * 301}, {"motivo": "escribe a alguien@correo.cl"}, {"motivo": "con\x00nul"},
    {"extra": 1}, {"id": 5}, {"is_active": False},
])
def test_invalid_data_is_rejected_without_echoing_it(changes):
    g = Gestion()
    body = {**PLAN_VALIDO, **changes}
    try:
        response = g.client.post(ADMIN, headers=g.admin, json=body)
    except ValueError:        # NaN cannot even be serialized as JSON by the client: it never reaches the service
        assert any(isinstance(v, float) and not math.isfinite(v) for v in changes.values())
        return
    assert response.status_code == 400, response.text
    text = response.text
    for value in changes.values():
        if isinstance(value, str) and len(value) > 3:
            assert value not in text                                              # the sent value is not echoed back
    assert "input" not in response.json()["detail"][0]
    assert g.auditor.entries == []                                                # nothing was audited or saved


@pytest.mark.parametrize("campo", ["nombre", "descripcion", "motivo", "beneficio"])
def test_lone_unicode_surrogates_and_nul_sent_as_json_escapes_are_a_400_not_a_500(campo):
    g = Gestion()
    headers = {**g.admin, "Content-Type": "application/json"}
    # The two characters backslash-u-d-8-0-0 etc. inside the JSON text: the server decodes them to a lone surrogate.
    for escape in (r"\ud800", r"\udc00", r"\u0000", r"x\ud83dx"):
        fields = {"nombre": '"Plan X"', "tipo": '"USUARIO"', "precio": "100", "duracion_dias": "30"}
        value = f'"Texto {escape}"'
        if campo == "beneficio":
            fields["beneficios"] = f"[{value}]"
        else:
            fields[campo] = value
        raw = ("{" + ",".join(f'"{k}":{v}' for k, v in fields.items()) + "}").encode("ascii")
        response = g.client.post(ADMIN, headers=headers, content=raw)
        assert response.status_code == 400, (campo, escape, response.text)
        assert "Texto" not in response.text
    assert g.auditor.entries == [] and g.repository._planes == {}


def test_a_body_that_is_not_json_or_has_infinity_is_a_400_not_a_500():
    g = Gestion()
    headers = {**g.admin, "Content-Type": "application/json"}
    for raw in (b'{"nombre": "Plan X", "tipo": "USUARIO", "precio": Infinity, "duracion_dias": 30}',
                b'{"nombre": "Plan X", "tipo": "USUARIO", "precio": NaN, "duracion_dias": 30}',
                b'{"nombre": ', b'[]', b'null', b''):
        assert g.client.post(ADMIN, headers=headers, content=raw).status_code == 400, raw


def test_the_limits_are_accepted():
    g = Gestion()
    assert g.create(nombre="Gratis", precio=0, duracion_dias=1).status_code == 201
    assert g.create(nombre="Tope", precio=10_000_000, duracion_dias=366, beneficios=[f"Beneficio {n}" for n in range(10)]
                    ).status_code == 201
    assert g.create(nombre="N" * 60).status_code == 201


# -- institución de un convenio -------------------------------------------------------------------


def test_a_convenio_can_have_an_institution_and_a_plan_cannot():
    g = Gestion()
    convenio = g.create(nombre="Convenio DuocUC", categoria="CONVENIO", institucion="  DuocUC   Maipú ")
    assert convenio.status_code == 201, convenio.text
    assert convenio.json()["institucion"] == "DuocUC Maipú"                            # spaces are normalized
    assert g.client.get(f"{PAY}/plans").json()[0]["institucion"] == "DuocUC Maipú"   # the public catalog shows it
    assert g.create(nombre="Plan con institución", institucion="DuocUC").status_code == 400
    sin = g.create(nombre="Convenio sin institución", categoria="CONVENIO")
    assert sin.status_code == 201 and sin.json()["institucion"] is None
    assert g.create(nombre="Convenio vacío", categoria="CONVENIO", institucion="   ").json()["institucion"] is None


@pytest.mark.parametrize("institucion", ["ab", "x" * 81, "Inst\x00itución", "alguien@correo.cl", "<b>Inst</b>", 12345])
def test_an_invalid_institution_is_rejected_without_echoing_it(institucion):
    g = Gestion()
    response = g.create(nombre="Convenio X", categoria="CONVENIO", institucion=institucion)
    assert response.status_code == 400, response.text
    assert "alguien@correo.cl" not in response.text and g.auditor.entries == []


def test_the_institution_can_be_edited_cleared_and_is_audited_by_field_name():
    g = Gestion()
    plan_id = g.create(nombre="Convenio UCh", categoria="CONVENIO", institucion="Universidad de Chile").json()["id"]
    edited = g.client.patch(f"{ADMIN}/{plan_id}", headers=g.admin, json={"institucion": "Universidad de Chile Norte"})
    assert edited.status_code == 200 and edited.json()["institucion"] == "Universidad de Chile Norte"
    assert g.auditor.entries[-1]["action"] == "PLAN_UPDATED" and g.auditor.entries[-1]["metadata"]["campos"] == "institucion"
    assert "Universidad" not in str(g.auditor.entries[-1]["metadata"])                  # free text never reaches the log
    cleared = g.client.patch(f"{ADMIN}/{plan_id}", headers=g.admin, json={"institucion": None})
    assert cleared.status_code == 200 and cleared.json()["institucion"] is None
    same = len(g.auditor.entries)
    assert g.client.patch(f"{ADMIN}/{plan_id}", headers=g.admin, json={"institucion": None}).status_code == 200
    assert len(g.auditor.entries) == same                                              # nothing changed: nothing audited


def test_a_plan_cannot_get_an_institution_by_editing():
    g = Gestion()
    plan_id = g.create().json()["id"]
    assert g.client.patch(f"{ADMIN}/{plan_id}", headers=g.admin, json={"institucion": "DuocUC"}).status_code == 400
    assert g.repository.get_plan_any(plan_id).institucion is None
    assert g.client.patch(f"{ADMIN}/{plan_id}", headers=g.admin, json={"institucion": None}).status_code == 200


# -- roles ----------------------------------------------------------------------------------------


def test_only_an_admin_manages_plans_and_a_non_admin_always_gets_403():
    g = Gestion()
    plan_id = g.create().json()["id"]
    routes = [("get", ADMIN, None), ("post", ADMIN, PLAN_VALIDO), ("patch", f"{ADMIN}/{plan_id}", {"precio": 1}),
              ("patch", f"{ADMIN}/9999", {"precio": 1}), ("post", f"{ADMIN}/{plan_id}/deactivate", None),
              ("post", f"{ADMIN}/9999/deactivate", None), ("patch", f"{ADMIN}/no-es-numero", {"precio": 1}),
              ("post", ADMIN, {"nombre": ""})]                                   # invalid body: still 403, not 400
    for role in ("player", "usuario", "club_admin"):
        headers = token(uuid4(), role)
        for method, path, body in routes:
            response = getattr(g.client, method)(path, headers=headers, **({"json": body} if body is not None else {}))
            assert response.status_code == 403, (role, method, path)
    for method, path, body in routes:
        response = getattr(g.client, method)(path, **({"json": body} if body is not None else {}))
        assert response.status_code == 401, (method, path)
    assert g.client.get(ADMIN, headers={"Authorization": "Bearer no-es-un-token"}).status_code == 401
    assert g.repository.get_plan_any(plan_id).precio == 4990 and len(g.auditor.entries) == 1


def test_a_token_with_both_athlete_and_admin_roles_is_an_admin_and_one_with_neither_is_not():
    g = Gestion()
    both = token(uuid4(), "player", roles=["player", "admin"])
    assert g.client.get(ADMIN, headers=both).status_code == 200
    assert g.client.get(ADMIN, headers=token(uuid4(), "ADMIN")).status_code == 403   # roles are case sensitive


# -- listado de administración -----------------------------------------------------------------


def test_the_admin_list_shows_active_and_inactive_with_the_subscriptions_in_force_and_is_paged():
    g = Gestion()
    a = g.create(nombre="Plan A").json()["id"]
    b = g.create(nombre="Plan B", precio=2000).json()["id"]
    c = g.create(nombre="Plan C", tipo="CLUB", categoria="CONVENIO", precio=8000).json()["id"]
    g.buy(a)
    g.buy(a)
    g.client.post(f"{ADMIN}/{b}/deactivate", headers=g.admin)
    everything = g.plans().json()
    assert everything["total"] == 3 and [i["id"] for i in everything["items"]] == [c, b, a]   # newest first
    by_id = {i["id"]: i for i in everything["items"]}
    assert (by_id[a]["suscripciones_vigentes"], by_id[b]["suscripciones_vigentes"], by_id[b]["is_active"]) == (2, 0, False)
    assert [i["id"] for i in g.plans(estado="activo").json()["items"]] == [c, a]
    assert [i["id"] for i in g.plans(estado="inactivo").json()["items"]] == [b]
    assert [i["id"] for i in g.plans(categoria="CONVENIO").json()["items"]] == [c]
    assert [i["id"] for i in g.plans(tipo="CLUB").json()["items"]] == [c]
    page = g.plans(limit=2, pagina=2).json()
    assert page["total"] == 3 and page["pagina"] == 2 and [i["id"] for i in page["items"]] == [a]
    for bad in ({"limit": 51}, {"limit": 0}, {"pagina": 0}, {"estado": "otro"}, {"tipo": "X"}, {"categoria": "X"}):
        assert g.plans(**bad).status_code == 400, bad


def test_an_expired_subscription_is_not_counted_as_in_force():
    g = Gestion()
    plan_id = g.create().json()["id"]
    headers, _ = g.buy(plan_id)
    athlete = UUID(next(iter(g.repository._suscripciones))[1].hex)
    g.repository.set_suscripcion_fin(usuario_id=athlete, fecha_fin=datetime.now(timezone.utc) - timedelta(days=1))
    assert g.plans().json()["items"][0]["suscripciones_vigentes"] == 0


# -- el cambio de precio no afecta lo ya pagado -------------------------------------------------------


def test_a_change_of_price_duration_or_benefits_does_not_alter_a_paid_subscription():
    g = Gestion()
    plan_id = g.create(precio=4990, duracion_dias=30, beneficios=["Beneficio original"]).json()["id"]
    headers, receipt = g.buy(plan_id)
    before = g.subscription(headers).json()
    assert (before["plan"]["precio"], before["plan"]["duracion_dias"], before["plan"]["beneficios"]) == (
        4990, 30, ["Beneficio original"])
    edited = g.client.patch(f"{ADMIN}/{plan_id}", headers=g.admin, json={
        "precio": 9990, "duracion_dias": 90, "beneficios": ["Otro"], "nombre": "Pro Renovado"})
    assert edited.status_code == 200
    after = g.subscription(headers).json()
    assert after == before                                                        # same price, days, benefits, name, dates
    assert after["plan"]["nombre"] == "Pro Mensual" and after["estado"] == "ACTIVE"
    assert g.client.get(f"{PAY}/plans").json()[0]["precio"] == 9990               # the catalog shows the new price
    # The receipt keeps what was paid too.
    stored = g.repository.get_transaccion(token_ws=receipt["transaccion_id"] and g.repository._transacciones[UUID(receipt["transaccion_id"])].token_ws)
    assert stored.amount == 4990 and stored.plan.precio == 4990 and stored.plan.duracion_dias == 30


def test_a_payment_started_before_the_change_is_settled_with_the_terms_it_started_with():
    g = Gestion()
    plan_id = g.create(precio=4990, duracion_dias=30).json()["id"]
    headers = token(uuid4(), "player")
    started = g.client.post(f"{PAY}/webpay/initiate", headers=headers, json={"plan_id": plan_id}).json()
    g.client.patch(f"{ADMIN}/{plan_id}", headers=g.admin, json={"precio": 20000, "duracion_dias": 7})
    g.webpay.decide(started["token_ws"], "aprobar")
    paid = g.client.post(f"{PAY}/webpay/commit", headers=headers, json={"token_ws": started["token_ws"]}).json()
    sub = paid["suscripcion"]
    assert paid["amount"] == 4990 and sub["plan"]["precio"] == 4990 and sub["plan"]["duracion_dias"] == 30
    days = (datetime.fromisoformat(sub["fecha_fin"]) - datetime.fromisoformat(sub["fecha_inicio"])).days
    assert days == 30                                                             # not 7


def test_renewing_after_a_price_change_takes_the_new_terms_and_keeps_the_days_already_paid():
    g = Gestion()
    plan_id = g.create(precio=4990, duracion_dias=30).json()["id"]
    headers, _ = g.buy(plan_id)
    first = g.subscription(headers).json()
    g.client.patch(f"{ADMIN}/{plan_id}", headers=g.admin, json={"precio": 5990})
    started = g.client.post(f"{PAY}/webpay/initiate", headers=headers, json={"plan_id": plan_id}).json()
    assert started["amount"] == 5990
    g.webpay.decide(started["token_ws"], "aprobar")
    renewed = g.client.post(f"{PAY}/webpay/commit", headers=headers, json={"token_ws": started["token_ws"]}).json()
    sub = renewed["suscripcion"]
    assert sub["plan"]["precio"] == 5990
    assert datetime.fromisoformat(sub["fecha_fin"]) - datetime.fromisoformat(first["fecha_fin"]) == timedelta(days=30)


# -- catálogo y planes desactivados ---------------------------------------------------------------------


def test_the_catalog_hides_a_deactivated_plan_and_it_can_no_longer_be_bought():
    g = Gestion()
    plan_id = g.create().json()["id"]
    assert [p["id"] for p in g.client.get(f"{PAY}/plans").json()] == [plan_id]
    g.client.post(f"{ADMIN}/{plan_id}/deactivate", headers=g.admin)
    assert g.client.get(f"{PAY}/plans").json() == []
    buyer = token(uuid4(), "player")
    response = g.client.post(f"{PAY}/webpay/initiate", headers=buyer, json={"plan_id": plan_id})
    assert response.status_code == 404 and response.json() == {"detail": "Plan no encontrado."}


def test_a_subscription_in_force_of_a_deactivated_plan_runs_until_its_end_and_cannot_be_renewed():
    g = Gestion()
    plan_id = g.create().json()["id"]
    headers, _ = g.buy(plan_id)
    first = g.subscription(headers).json()
    g.client.post(f"{ADMIN}/{plan_id}/deactivate", headers=g.admin)
    still = g.subscription(headers)
    assert still.status_code == 200 and still.json() == first and still.json()["estado"] == "ACTIVE"
    # No renewal and no new purchase of that plan: there is no automatic renewal in this service either.
    assert g.client.post(f"{PAY}/webpay/initiate", headers=headers, json={"plan_id": plan_id}).status_code == 404
    assert g.plans(estado="inactivo").json()["items"][0]["suscripciones_vigentes"] == 1
    g.repository.set_suscripcion_fin(usuario_id=next(iter(g.repository._suscripciones))[1],
                                     fecha_fin=datetime.now(timezone.utc) - timedelta(seconds=1))
    assert g.subscription(headers).json()["estado"] == "EXPIRED"                  # it ends on its date, as always


def test_a_payment_already_started_when_the_plan_is_deactivated_is_still_honored():
    g = Gestion()
    plan_id = g.create().json()["id"]
    headers = token(uuid4(), "player")
    started = g.client.post(f"{PAY}/webpay/initiate", headers=headers, json={"plan_id": plan_id}).json()
    g.client.post(f"{ADMIN}/{plan_id}/deactivate", headers=g.admin)
    g.webpay.decide(started["token_ws"], "aprobar")
    paid = g.client.post(f"{PAY}/webpay/commit", headers=headers, json={"token_ws": started["token_ws"]})
    assert paid.status_code == 200 and paid.json()["suscripcion"]["estado"] == "ACTIVE"   # the money was already taken


def test_a_free_plan_is_listed_but_does_not_go_through_webpay():
    g = Gestion()
    plan_id = g.create(nombre="Convenio gratuito", categoria="CONVENIO", precio=0).json()["id"]
    assert [p["precio"] for p in g.client.get(f"{PAY}/plans").json()] == [0]
    response = g.client.post(f"{PAY}/webpay/initiate", headers=token(uuid4(), "player"), json={"plan_id": plan_id})
    assert response.status_code == 409 and "gratuito" in response.json()["detail"]


def test_the_existing_buyer_rules_still_apply_to_a_convenio_of_clubs():
    g = Gestion()
    plan_id = g.create(nombre="Convenio de clubes", tipo="CLUB", categoria="CONVENIO", precio=9000).json()["id"]
    athlete = token(uuid4(), "player")
    assert g.client.post(f"{PAY}/webpay/initiate", headers=athlete, json={"plan_id": plan_id}).status_code == 403


# -- auditoría ------------------------------------------------------------------------------------------


def test_every_change_is_audited_with_the_previous_and_the_new_price_and_the_changed_fields():
    g = Gestion()
    plan_id = g.create(motivo="Alta del plan de verano").json()["id"]
    g.client.patch(f"{ADMIN}/{plan_id}", headers=g.admin, json={"precio": 5990, "duracion_dias": 60, "motivo": "Ajuste de temporada"})
    g.client.post(f"{ADMIN}/{plan_id}/deactivate", headers=g.admin, json={"motivo": "Fin de temporada"})
    created, updated, deactivated = g.auditor.entries
    assert (created["action"], created["motivo"], created["plan_id"], created["actor"]) == (
        "PLAN_CREATED", "Alta del plan de verano", plan_id, g.admin_id)
    assert created["metadata"]["precio_nuevo"] == 4990 and created["metadata"]["precio_anterior"] is None
    assert updated["action"] == "PLAN_UPDATED" and updated["motivo"] == "Ajuste de temporada"
    assert updated["metadata"]["precio_anterior"] == 4990 and updated["metadata"]["precio_nuevo"] == 5990
    assert updated["metadata"]["duracion_anterior"] == 30 and updated["metadata"]["duracion_nueva"] == 60
    assert set(updated["metadata"]["campos"].split(",")) == {"precio", "duracion_dias"}
    assert deactivated["action"] == "PLAN_DEACTIVATED" and deactivated["motivo"] == "Fin de temporada"
    assert deactivated["metadata"]["campos"] == "is_active" and deactivated["metadata"]["precio_nuevo"] == 5990


def test_the_reason_is_optional_and_a_default_one_is_recorded():
    g = Gestion()
    plan_id = g.create().json()["id"]
    g.client.post(f"{ADMIN}/{plan_id}/deactivate", headers=g.admin)
    assert [e["motivo"] for e in g.auditor.entries] == ["Creación de plan o convenio", "Desactivación de plan o convenio"]
    assert all(3 <= len(e["motivo"]) <= 300 for e in g.auditor.entries)


def test_the_audit_metadata_has_only_numbers_and_fixed_words():
    g = Gestion()
    plan_id = g.create(nombre="Plan con nombre libre", descripcion="Texto libre de la descripción").json()["id"]
    g.client.patch(f"{ADMIN}/{plan_id}", headers=g.admin, json={"nombre": "Otro nombre libre"})
    for entry in g.auditor.entries:
        assert len(entry["metadata"]) <= 10
        assert "libre" not in str(entry["metadata"]) and "descripción" not in str(entry["metadata"])
        assert all(not any(bad in key for bad in ("pass", "token", "secret", "rut", "email", "key")) for key in entry["metadata"])


@pytest.mark.parametrize("step", ["create", "update", "deactivate"])
def test_if_the_audit_fails_the_change_is_not_saved_and_the_answer_is_503(step):
    g = Gestion()
    plan_id = g.create().json()["id"] if step != "create" else None
    snapshot = g.repository.get_plan_any(plan_id) if plan_id else None
    count = len(g.repository._planes)
    g.auditor.down = True
    if step == "create":
        response = g.create(nombre="Otro plan")
    elif step == "update":
        response = g.client.patch(f"{ADMIN}/{plan_id}", headers=g.admin, json={"precio": 1})
    else:
        response = g.client.post(f"{ADMIN}/{plan_id}/deactivate", headers=g.admin)
    assert response.status_code == 503 and "auditoría" in response.json()["detail"]
    assert len(g.repository._planes) == count                                     # nothing created
    if snapshot:
        assert g.repository.get_plan_any(plan_id) == snapshot                    # nothing changed (price, is_active)
    g.auditor.down = False
    assert g.create(nombre="Otro plan").status_code == 201                        # and it works again afterwards


def test_without_an_auditor_configured_no_change_is_saved():
    repository = MockClubRepository(planes=[])
    client = TestClient(create_app(repository=repository, webpay=MockWebpay(SETTINGS.public_url), webpay_settings=SETTINGS))
    admin = token(uuid4(), "admin")
    assert client.post(ADMIN, headers=admin, json=PLAN_VALIDO).status_code == 503
    assert repository._planes == {}


# -- el catálogo público y el flujo de pago existentes no cambian de forma -------------------------------


def test_the_public_catalog_exposes_category_and_benefits_but_not_admin_fields():
    g = Gestion()
    g.create(nombre="Visible", categoria="CONVENIO", beneficios=["Uno bueno"])
    item = g.client.get(f"{PAY}/plans").json()[0]
    assert item["categoria"] == "CONVENIO" and item["beneficios"] == ["Uno bueno"]
    assert not {"is_active", "suscripciones_vigentes", "fecha_creacion"} & set(item)


def test_the_unchanged_payment_flow_still_works_with_the_demo_plans():
    pagos = Pagos()
    athlete = token(uuid4(), "player")
    started = pagos.started(athlete, 1)
    pagos.webpay.decide(started["token_ws"], "aprobar")
    assert pagos.commit(athlete, token_ws=started["token_ws"]).status_code == 200
    assert pagos.subscription(athlete).json()["plan"]["precio"] == 3990
