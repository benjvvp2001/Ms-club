"""The whole migration chain of the clubs service on real PostgreSQL (CLUBS_TEST_DATABASE_URL).

Payments, plans and subscriptions moved to Ms-pago: ms-club no longer has code for them, but migrations 006 and 012-014
stay (they are already applied in the databases and the migrator reads every file in order). The tables they create
stay in the database, unused. This test keeps the chain honest: it applies on an empty base, twice."""
from __future__ import annotations

from pg_support import MIGRATIONS, apply, base, build  # noqa: F401  (base: fixture)


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
