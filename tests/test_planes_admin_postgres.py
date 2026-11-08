"""HU-56 on real PostgreSQL: the whole migration chain on an empty base, the 012 migration over existing rows and the
plan management flow with the PostgreSQL repository.

Runs only with CLUBS_TEST_DATABASE_URL (the isolated qa_tests database). Each test builds its own pair of schemas
(<name> and <name>_api, the latter for clubs_api.notification_outbox of migration 004) and drops them at the end, so
nothing is left behind. The migration files name `public.` and `clubs_api` explicitly; they are applied with those
names replaced by the test schemas."""
from __future__ import annotations

import os
import re
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg_pool import ConnectionPool

from test_club_admin import token  # also sets JWT_SECRET before the app is imported
from test_planes_admin import ADMIN, PAY, PLAN_VALIDO, Gestion

from app.repositories.postgres_club_repository import PostgresClubRepository

DB = Path(__file__).parents[1] / "db"
MIGRATIONS = sorted((DB / "migrations").glob("*.sql"))


def apply(conn, schema: str, path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"\bclubs_api\b", f"{schema}_api", text)
    text = re.sub(r"\bpublic\.", f"{schema}.", text)
    conn.execute(text)


@pytest.fixture
def base():
    dsn = os.environ.get("CLUBS_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Set CLUBS_TEST_DATABASE_URL for isolated PostgreSQL plan tests")
    schema = "planes_test_" + uuid4().hex[:16]
    with psycopg.connect(dsn, autocommit=True) as setup:
        for name in (schema, f"{schema}_api"):
            setup.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(name)))
        try:
            setup.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            yield setup, schema, dsn
        finally:
            setup.execute("RESET search_path")
            for name in (schema, f"{schema}_api"):
                setup.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(name)))


def build(setup, schema, upto: str | None = None) -> None:
    apply(setup, schema, DB / "00_base_schema.sql")
    for path in MIGRATIONS:
        if upto is not None and path.name > upto:
            break
        apply(setup, schema, path)


def pool_for(dsn: str, schema: str) -> ConnectionPool:
    pool = ConnectionPool(dsn, kwargs={"options": f"-c search_path={schema}"}, min_size=1, max_size=4, open=True)
    pool.wait()
    return pool


def test_the_chain_covers_every_migration_and_015_is_the_last():
    assert MIGRATIONS[-1].name == "015_convocatoria_historial.sql"
    assert [path.name[:3] for path in MIGRATIONS] == [f"{n:03d}" for n in range(1, 16)]


def test_all_migrations_in_order_on_an_empty_base_and_again_leave_the_expected_schema(base):
    setup, schema, _ = base
    build(setup, schema)
    for path in MIGRATIONS:                                          # the service runs them all on every start
        apply(setup, schema, path)
    columns = {(t, c): (nullable, dtype) for t, c, nullable, dtype in setup.execute(
        "SELECT table_name, column_name, is_nullable, data_type FROM information_schema.columns "
        "WHERE table_schema = %s AND table_name IN ('plan_suscripcion', 'transaccion_pago', 'suscripcion')", (schema,))}
    assert columns[("plan_suscripcion", "categoria")] == ("NO", "character varying")
    assert columns[("plan_suscripcion", "beneficios")] == ("NO", "jsonb")
    for table in ("transaccion_pago", "suscripcion"):
        assert columns[(table, "plan_nombre")][0] == "NO" and columns[(table, "beneficios")] == ("NO", "jsonb")
        assert columns[(table, "duracion_dias")][0] == "NO"
    assert columns[("suscripcion", "precio_pagado")][0] == "NO"
    constraints = {name for (name,) in setup.execute(
        "SELECT conname FROM pg_constraint WHERE connamespace = %s::regnamespace", (schema,))}
    assert "plan_suscripcion_tipo_nombre_key" not in constraints
    assert {"plan_suscripcion_precio_check", "plan_suscripcion_categoria_check", "plan_suscripcion_beneficios_check"} <= constraints
    assert setup.execute("SELECT to_regclass(%s)", (f"{schema}.plan_suscripcion_nombre_activo_uq",)).fetchone()[0]
    assert columns[("plan_suscripcion", "es_base")] == ("NO", "boolean")
    assert columns[("plan_suscripcion", "fecha_desactivacion")] == ("YES", "timestamp with time zone")
    assert columns[("plan_suscripcion", "motivo_desactivacion")][0] == "YES"
    assert columns[("plan_suscripcion", "duracion_dias")][0] == "YES"                       # sin vencimiento
    assert columns[("suscripcion", "duracion_dias")][0] == "NO" and columns[("transaccion_pago", "duracion_dias")][0] == "NO"
    assert setup.execute("SELECT to_regclass(%s)", (f"{schema}.plan_suscripcion_base_uq",)).fetchone()[0]


def test_the_database_enforces_the_plan_rules(base):
    setup, schema, _ = base
    build(setup, schema)

    def insert(nombre="Plan X", tipo="USUARIO", precio=1000, dias=30, **extra):
        columns = {"nombre": nombre, "tipo": tipo, "precio": precio, "duracion_dias": dias, **extra}
        setup.execute(f"INSERT INTO plan_suscripcion ({', '.join(columns)}) VALUES ({', '.join(['%s'] * len(columns))})",
                      list(columns.values()))

    insert()
    insert(nombre="Gratis", precio=0)                                                    # a free plan is allowed
    with pytest.raises(psycopg.errors.UniqueViolation):
        insert(nombre="  plan x ", tipo="CLUB")                                          # unique among ACTIVE plans, any tipo
    setup.execute("UPDATE plan_suscripcion SET is_active = false WHERE nombre = 'Plan X'")
    insert(nombre="Plan X")                                                              # a retired name can be reused
    for bad in ({"precio": -1}, {"precio": 10_000_001}, {"dias": 0}, {"dias": 367}, {"categoria": "OTRA"},
                {"nombre": "ab"}, {"tipo": "ADMIN"}, {"beneficios": '["a","b","c","d","e","f","g","h","i","j","k"]'},
                {"beneficios": '{"no": "lista"}'}):
        with pytest.raises(psycopg.errors.CheckViolation):
            insert(**{"nombre": f"Malo {uuid4().hex[:6]}", **bad})
    role = setup.execute("SELECT 1 FROM pg_roles WHERE rolname = 'sportmatch_clubs'").fetchone()
    if role:   # the service may create and edit plans but never delete them
        privileges = [setup.execute("SELECT has_table_privilege('sportmatch_clubs', %s, %s)",
                                    (f"{schema}.plan_suscripcion", p)).fetchone()[0] for p in ("INSERT", "UPDATE", "DELETE")]
        assert privileges == [True, True, False]


def test_the_institution_column_is_only_for_convenios_and_the_migration_is_a_noop_when_repeated(base):
    setup, schema, dsn = base
    build(setup, schema)
    for _ in range(2):
        apply(setup, schema, DB / "migrations" / "013_plan_institucion.sql")
    setup.execute("INSERT INTO plan_suscripcion (nombre, tipo, precio, duracion_dias, categoria, institucion) "
                  "VALUES ('Convenio A', 'USUARIO', 0, 30, 'CONVENIO', 'DuocUC')")
    setup.execute("INSERT INTO plan_suscripcion (nombre, tipo, precio, duracion_dias) VALUES ('Plan normal', 'USUARIO', 100, 30)")
    assert setup.execute("SELECT institucion FROM plan_suscripcion WHERE nombre = 'Plan normal'").fetchone()[0] is None
    for categoria, institucion in (("PLAN", "DuocUC"), ("CONVENIO", "  "), ("CONVENIO", "ab"), ("CONVENIO", "x" * 81)):
        with pytest.raises((psycopg.errors.CheckViolation, psycopg.errors.StringDataRightTruncation)):
            setup.execute("INSERT INTO plan_suscripcion (nombre, tipo, precio, duracion_dias, categoria, institucion) "
                          "VALUES (%s, 'USUARIO', 0, 30, %s, %s)", (f"Malo {uuid4().hex[:6]}", categoria, institucion))
    pool = pool_for(dsn, schema)
    try:
        g = Gestion(repository=PostgresClubRepository(pool))
        created = g.create(nombre="Convenio UCh", categoria="CONVENIO", institucion="Universidad de Chile")
        assert created.status_code == 201 and created.json()["institucion"] == "Universidad de Chile"
        plan_id = created.json()["id"]
        assert g.client.patch(f"{ADMIN}/{plan_id}", headers=g.admin, json={"institucion": "UCh Norte"}).json()["institucion"] == "UCh Norte"
        assert g.client.get(f"{PAY}/plans").json()[-1]["institucion"] in ("UCh Norte", "DuocUC", None)
        assert g.client.patch(f"{ADMIN}/{plan_id}", headers=g.admin, json={"institucion": None}).json()["institucion"] is None
        assert setup.execute("SELECT institucion FROM plan_suscripcion WHERE id = %s", (plan_id,)).fetchone()[0] is None
        assert g.create(nombre="Plan con inst", institucion="DuocUC").status_code == 400
    finally:
        pool.close()


def test_the_012_migration_fills_existing_subscriptions_without_changing_them(base):
    setup, schema, _ = base
    build(setup, schema, upto="011_chat_postulantes_convocatoria.sql")
    plan = setup.execute("INSERT INTO plan_suscripcion (nombre, tipo, precio, duracion_dias) VALUES "
                         "('Pro', 'USUARIO', 4990, 30) RETURNING id").fetchone()[0]
    paid, no_payment = uuid4(), uuid4()
    transaccion = setup.execute(
        "INSERT INTO transaccion_pago (usuario_id, iniciada_por, plan_id, buy_order, session_id, token_ws, amount, status) "
        "VALUES (%s, %s, %s, 'SM1', 's1', 't1', 3990, 'APPROVED') RETURNING id", (paid, paid, plan)).fetchone()[0]
    setup.execute("UPDATE plan_suscripcion SET precio = 5990")       # the plan's price rose after that payment
    setup.execute("INSERT INTO suscripcion (usuario_id, plan_id, fecha_inicio, fecha_fin, ultima_transaccion_id) VALUES "
                  "(%s, %s, now() - interval '5 days', now() + interval '25 days', %s)", (paid, plan, transaccion))
    setup.execute("INSERT INTO suscripcion (usuario_id, plan_id, fecha_inicio, fecha_fin) VALUES "
                  "(%s, %s, now() - interval '5 days', now() + interval '25 days')", (no_payment, plan))
    before = setup.execute("SELECT usuario_id, plan_id, fecha_inicio, fecha_fin FROM suscripcion ORDER BY usuario_id").fetchall()

    for _ in range(3):
        apply(setup, schema, DB / "migrations" / "012_gestion_planes.sql")

    assert setup.execute("SELECT usuario_id, plan_id, fecha_inicio, fecha_fin FROM suscripcion ORDER BY usuario_id").fetchall() == before
    rows = {u: rest for u, *rest in setup.execute(
        "SELECT usuario_id, plan_nombre, precio_pagado, duracion_dias, beneficios FROM suscripcion")}
    assert rows[paid] == ["Pro", 3990, 30, []]            # what that payment really cost, not today's price
    assert rows[no_payment] == ["Pro", 5990, 30, []]      # no payment on record: today's values
    assert setup.execute("SELECT plan_nombre, duracion_dias, beneficios, amount FROM transaccion_pago").fetchone() == ("Pro", 30, [], 3990)
    assert setup.execute("SELECT categoria, beneficios FROM plan_suscripcion").fetchone() == ("PLAN", [])


def test_the_whole_flow_with_the_real_repository_and_the_audit_rollback(base):
    setup, schema, dsn = base
    build(setup, schema)
    pool = pool_for(dsn, schema)
    try:
        g = Gestion(repository=PostgresClubRepository(pool))
        created = g.create(nombre="Pro Mensual", precio=4990, duracion_dias=30, beneficios=["Uno bueno", "Otro bueno"])
        assert created.status_code == 201, created.text
        plan_id = created.json()["id"]
        assert g.create().status_code == 409                                                      # name taken (unique index)
        assert g.create(nombre="  PRO   mensual").status_code == 409
        convenio = g.create(nombre="Convenio Ñuñoa", tipo="CLUB", categoria="CONVENIO", precio=0, beneficios=[])
        assert convenio.status_code == 201 and convenio.json()["categoria"] == "CONVENIO"

        headers, receipt = g.buy(plan_id)                                                         # pays with the PostgreSQL repo
        before = g.subscription(headers).json()
        assert before["plan"]["precio"] == 4990 and before["plan"]["beneficios"] == ["Uno bueno", "Otro bueno"]
        g.client.patch(f"{ADMIN}/{plan_id}", headers=g.admin, json={"precio": 9990, "duracion_dias": 90, "beneficios": ["Cambio"]})
        assert g.subscription(headers).json() == before                                           # paid terms untouched
        assert g.client.get(f"{PAY}/plans").json()[0]["precio"] in (9990, 0)
        assert g.plans(estado="activo").json()["items"][-1]["suscripciones_vigentes"] == 1

        # A second buyer gets the new terms.
        _, receipt2 = g.buy(plan_id)
        assert receipt2["amount"] == 9990 and receipt2["suscripcion"]["plan"]["duracion_dias"] == 90

        # Audit failure: nothing is saved and the answer is 503.
        g.auditor.down = True
        assert g.client.patch(f"{ADMIN}/{plan_id}", headers=g.admin, json={"precio": 1}).status_code == 503
        assert g.create(nombre="No queda").status_code == 503
        assert g.client.post(f"{ADMIN}/{plan_id}/deactivate", headers=g.admin).status_code == 503
        g.auditor.down = False
        assert setup.execute("SELECT precio, is_active FROM plan_suscripcion WHERE id = %s", (plan_id,)).fetchone() == (9990, True)
        assert setup.execute("SELECT count(*) FROM plan_suscripcion WHERE nombre = 'No queda'").fetchone()[0] == 0

        # Deactivate: hidden from the catalog, row kept, subscription running, not buyable.
        assert g.client.post(f"{ADMIN}/{plan_id}/deactivate", headers=g.admin, json={"motivo": "Se retira"}).status_code == 200
        assert plan_id not in [p["id"] for p in g.client.get(f"{PAY}/plans").json()]
        assert setup.execute("SELECT is_active FROM plan_suscripcion WHERE id = %s", (plan_id,)).fetchone()[0] is False
        assert g.subscription(headers).json()["estado"] == "ACTIVE"
        assert g.client.post(f"{PAY}/webpay/initiate", headers=headers, json={"plan_id": plan_id}).status_code == 404
        assert g.plans(estado="inactivo").json()["items"][0]["suscripciones_vigentes"] == 2
        assert g.create(nombre="Pro Mensual").status_code == 201                                  # name free again
        actions = [e["action"] for e in g.auditor.entries]
        assert actions == ["PLAN_CREATED", "PLAN_CREATED", "PLAN_UPDATED", "PLAN_DEACTIVATED", "PLAN_CREATED"]
    finally:
        pool.close()


def test_a_concurrent_rename_to_the_same_name_creates_only_one_plan(base):
    setup, schema, dsn = base
    build(setup, schema)
    pool = pool_for(dsn, schema)
    try:
        g = Gestion(repository=PostgresClubRepository(pool))
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(4) as executor:
            codes = list(executor.map(lambda _: g.create(nombre="Carrera").status_code, range(4)))
        assert sorted(codes) == [201, 409, 409, 409]
        assert setup.execute("SELECT count(*) FROM plan_suscripcion WHERE nombre = 'Carrera'").fetchone()[0] == 1
    finally:
        pool.close()
