"""HU-56 (panel web) en PostgreSQL real: la migración 014 (plan base, fecha de desactivación y duración sin
vencimiento), reactivar, suscripciones de un plan y el plan base con el repositorio de PostgreSQL.

Corre solo con CLUBS_TEST_DATABASE_URL (la base aislada qa_tests). Cada prueba arma su propio par de esquemas y los
borra al terminar: no queda nada."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from test_pagos import SETTINGS
from test_planes_admin import ADMIN, PAY, Gestion
from test_planes_admin_panel import FakeNames, buy_as, deactivated
from test_planes_admin_postgres import DB, apply, base, build, pool_for  # noqa: F401  (base is a fixture)

from app.main import create_app
from app.repositories.postgres_club_repository import PostgresClubRepository


def test_the_014_migration_keeps_old_rows_and_is_a_noop_when_repeated(base):
    setup, schema, _ = base
    build(setup, schema, upto="013_plan_institucion.sql")
    setup.execute("INSERT INTO plan_suscripcion (nombre, tipo, precio, duracion_dias) VALUES ('Activo viejo', 'USUARIO', 4990, 30)")
    setup.execute("INSERT INTO plan_suscripcion (nombre, tipo, precio, duracion_dias, is_active) "
                  "VALUES ('Retirado viejo', 'USUARIO', 100, 30, false)")
    for _ in range(3):                                          # applied three times: the same result
        apply(setup, schema, DB / "migrations" / "014_plan_base_desactivacion.sql")
    rows = setup.execute("SELECT nombre, es_base, fecha_desactivacion, motivo_desactivacion, duracion_dias, precio "
                         "FROM plan_suscripcion ORDER BY id").fetchall()
    assert rows == [("Activo viejo", False, None, None, 30, 4990), ("Retirado viejo", False, None, None, 30, 100)]


def test_the_database_enforces_the_014_rules(base):
    setup, schema, _ = base
    build(setup, schema)

    def insert(**c):
        values = {"nombre": f"Plan {uuid4().hex[:8]}", "tipo": "USUARIO", "precio": 100, "duracion_dias": 30, **c}
        setup.execute(f"INSERT INTO plan_suscripcion ({', '.join(values)}) VALUES ({', '.join(['%s'] * len(values))})",
                      list(values.values()))

    insert(precio=0, duracion_dias=None)                                               # free and no expiry: allowed
    for bad in ({"precio": 100, "duracion_dias": None},                                # paid without duration
                {"duracion_dias": 0}, {"duracion_dias": 367},
                {"is_active": True, "fecha_desactivacion": "2026-11-01T00:00:00Z"},   # an active plan has no deactivation date
                {"is_active": False, "motivo_desactivacion": "ab"},
                {"is_active": False, "es_base": True}):                                # the base plan is always active
        with pytest.raises(psycopg.errors.CheckViolation):
            insert(**bad)
    insert(es_base=True)
    with pytest.raises(psycopg.errors.UniqueViolation):
        insert(es_base=True)                                                           # one base plan per tipo
    insert(es_base=True, tipo="CLUB")
    # A paid transaction or subscription always has a duration.
    nullable = setup.execute("SELECT is_nullable FROM information_schema.columns WHERE table_schema = %s "
                             "AND table_name = 'suscripcion' AND column_name = 'duracion_dias'", (schema,)).fetchone()[0]
    assert nullable == "NO"


def test_reactivating_and_listing_subscriptions_with_the_real_repository(base):
    setup, schema, dsn = base
    build(setup, schema)
    pool = pool_for(dsn, schema)
    try:
        g = Gestion(repository=PostgresClubRepository(pool))
        plan_id = deactivated(g)                                                     # created and deactivated with a reason
        row = setup.execute("SELECT is_active, motivo_desactivacion, fecha_desactivacion IS NOT NULL "
                            "FROM plan_suscripcion WHERE id = %s", (plan_id,)).fetchone()
        assert row == (False, "Fin de temporada", True)
        # The name is taken by another active plan: refused, still deactivated, nothing audited.
        g.create()
        entries = len(g.auditor.entries)
        assert g.client.post(f"{ADMIN}/{plan_id}/reactivate", headers=g.admin).status_code == 409
        assert setup.execute("SELECT is_active FROM plan_suscripcion WHERE id = %s", (plan_id,)).fetchone()[0] is False
        assert len(g.auditor.entries) == entries
        # Free the name (the other plan is renamed): a failing audit rolls the reactivation back, then it works.
        other = g.plans(estado="activo").json()["items"][0]["id"]
        assert g.client.patch(f"{ADMIN}/{other}", headers=g.admin, json={"nombre": "Pro Renombrado"}).status_code == 200
        g.auditor.down = True
        assert g.client.post(f"{ADMIN}/{plan_id}/reactivate", headers=g.admin).status_code == 503
        g.auditor.down = False
        assert setup.execute("SELECT is_active FROM plan_suscripcion WHERE id = %s", (plan_id,)).fetchone()[0] is False
        ok = g.client.post(f"{ADMIN}/{plan_id}/reactivate", headers=g.admin, json={"motivo": "Vuelve la oferta"})
        assert ok.status_code == 200 and ok.json()["fecha_desactivacion"] is None
        assert setup.execute("SELECT is_active, motivo_desactivacion, fecha_desactivacion FROM plan_suscripcion "
                             "WHERE id = %s", (plan_id,)).fetchone() == (True, None, None)
        assert g.auditor.entries[-1]["action"] == "PLAN_REACTIVATED"
        assert g.client.post(f"{ADMIN}/{plan_id}/reactivate", headers=g.admin).status_code == 409   # already active

        # Subscriptions of the plan: athletes by short name, a club by its name; current first; paged; by state.
        athletes = [uuid4(), uuid4(), uuid4()]
        names: dict[UUID, str] = {athletes[0]: "Ana T.", athletes[1]: "Luis P."}
        g.client = TestClient(create_app(repository=g.repository, webpay=g.webpay, webpay_settings=SETTINGS,
                                         plan_auditor=g.auditor, account_names=FakeNames(names)))
        for athlete in athletes:
            buy_as(g, plan_id, athlete)
        club_plan = g.create(nombre="Plan de club", tipo="CLUB", precio=19990).json()["id"]
        club_id, tx_id = uuid4(), uuid4()
        setup.execute("INSERT INTO club (id, usuario_id, nombre) VALUES (%s, %s, 'Club Norte')", (club_id, uuid4()))
        setup.execute("INSERT INTO transaccion_pago (id, club_id, iniciada_por, plan_id, buy_order, session_id, token_ws, amount, "
                      "status, plan_nombre, duracion_dias, beneficios) VALUES (%s, %s, %s, %s, 'bo1', 'se1', %s, 19990, "
                      "'APPROVED', 'Plan de club', 30, '[]'::jsonb)", (tx_id, club_id, uuid4(), club_plan, "t" * 64))
        setup.execute("INSERT INTO suscripcion (club_id, plan_id, fecha_inicio, fecha_fin, ultima_transaccion_id, plan_nombre, "
                      "precio_pagado, duracion_dias, beneficios) VALUES (%s, %s, now() - interval '5 days', "
                      "now() + interval '25 days', %s, 'Plan de club', 19990, 30, '[]'::jsonb)", (club_id, club_plan, tx_id))
        url = f"{ADMIN}/{plan_id}/subscriptions"
        page = g.client.get(url, headers=g.admin, params={"limit": 2}).json()
        assert page["total"] == 3 and len(page["items"]) == 2 and all(i["estado"] == "ACTIVE" for i in page["items"])
        assert {i["suscriptor"] for i in g.client.get(url, headers=g.admin).json()["items"]} == {"Ana T.", "Luis P.", None}
        club_page = g.client.get(f"{ADMIN}/{club_plan}/subscriptions", headers=g.admin).json()
        assert [(i["suscriptor"], i["tipo_suscriptor"], i["precio_pagado"]) for i in club_page["items"]] == [
            ("Club Norte", "CLUB", 19990)]
        setup.execute("UPDATE suscripcion SET fecha_fin = now() - interval '1 day', fecha_inicio = now() - interval '31 days' "
                      "WHERE usuario_id = %s", (athletes[0],))
        assert g.client.get(url, headers=g.admin, params={"estado": "vencida"}).json()["total"] == 1
        assert g.client.get(url, headers=g.admin, params={"estado": "vigente"}).json()["total"] == 2
        assert [i["estado"] for i in g.client.get(url, headers=g.admin).json()["items"]] == ["ACTIVE", "ACTIVE", "EXPIRED"]
        counted = next(p for p in g.plans().json()["items"] if p["id"] == plan_id)
        assert counted["suscripciones_vigentes"] == 2

        # Base plan: cannot be deactivated. A free plan with no expiry is stored as NULL and cannot become paid alone.
        base_id = g.create(nombre="Gratuito base", precio=0, duracion_dias=None, beneficios=["Chat"]).json()["id"]
        setup.execute("UPDATE plan_suscripcion SET es_base = true WHERE id = %s", (base_id,))
        blocked = g.client.post(f"{ADMIN}/{base_id}/deactivate", headers=g.admin)
        assert blocked.status_code == 409 and "plan base" in blocked.json()["detail"]
        assert setup.execute("SELECT is_active, duracion_dias FROM plan_suscripcion WHERE id = %s", (base_id,)).fetchone() == (True, None)
        assert g.client.patch(f"{ADMIN}/{base_id}", headers=g.admin, json={"precio": 500}).status_code == 400
        assert next(p for p in g.client.get(f"{PAY}/plans").json() if p["id"] == base_id)["duracion_dias"] is None
        assert next(p for p in g.plans().json()["items"] if p["id"] == base_id)["es_base"] is True
    finally:
        pool.close()


def test_a_concurrent_reactivation_and_creation_of_the_same_name_leaves_one_active_plan(base):
    setup, schema, dsn = base
    build(setup, schema)
    pool = pool_for(dsn, schema)
    try:
        g = Gestion(repository=PostgresClubRepository(pool))
        plan_id = deactivated(g, nombre="Carrera dos")
        jobs = [lambda: g.client.post(f"{ADMIN}/{plan_id}/reactivate", headers=g.admin).status_code,
                lambda: g.create(nombre="Carrera dos").status_code,
                lambda: g.client.post(f"{ADMIN}/{plan_id}/reactivate", headers=g.admin).status_code]
        with ThreadPoolExecutor(3) as executor:
            codes = list(executor.map(lambda job: job(), jobs))
        assert codes.count(409) >= 1
        assert setup.execute("SELECT count(*) FROM plan_suscripcion WHERE lower(nombre) = 'carrera dos' AND is_active").fetchone()[0] == 1
    finally:
        pool.close()
