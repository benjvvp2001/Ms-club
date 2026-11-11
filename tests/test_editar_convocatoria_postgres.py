"""HU-68 on real PostgreSQL: migration 015, the row lock between accepting and lowering the cupos, the history row, the
notice rows and a notice that cannot be written. Runs only with CLUBS_TEST_DATABASE_URL (the isolated qa_tests
database; one throwaway pair of schemas per test)."""
from __future__ import annotations

import logging
import threading
from datetime import date, datetime, time, timedelta
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql

import app.repositories.postgres_club_repository as repository_module
from test_club_admin import nuevo_rut, token  # also sets JWT_SECRET before the app is imported
from pg_support import DB, apply, base, build, pool_for  # noqa: F401  (base: fixture)
from test_postulaciones import CHILE, CLUBS_PATH, FakeUsers, perfil

from app.main import create_app
from app.repositories.postgres_club_repository import PostgresClubRepository

MIGRATION = DB / "migrations" / "015_convocatoria_historial.sql"


class Pg:
    """A club with a convocatoria on the PostgreSQL repository, and helpers to apply and review."""

    def __init__(self, setup, schema, dsn, cupos=5) -> None:
        self.setup, self.schema = setup, schema
        self.pool = pool_for(dsn, schema)
        self.outbox = sql.Identifier(f"{schema}_api", "notification_outbox")
        self._original_outbox = repository_module._OUTBOX
        repository_module._OUTBOX = self.outbox
        self.repository = PostgresClubRepository(self.pool)
        self.client = TestClient(create_app(repository=self.repository, users=FakeUsers()))
        self.owner = uuid4()
        self.headers = token(self.owner)
        created = self.client.post(CLUBS_PATH, headers=self.headers,
                                   json={"nombre": "Club Ñuñoa", "rut": nuevo_rut(), "deporte_ids": [1]})
        assert created.status_code == 201, created.text
        self.club_id = UUID(created.json()["id"])
        made = self.client.post(f"{CLUBS_PATH}/{self.club_id}/announcements", headers=self.headers, json={
            "deporte_id": 1, "titulo": "Prueba Sub-17", "categoria_edad": "Sub-17", "edad_minima": 15,
            "edad_maxima": 17, "requisitos": ["Ficha médica"], "cupos": cupos, "direccion": "Av. Grecia 2001",
            "fecha_prueba": datetime.combine(date.today() + timedelta(days=10), time(10), tzinfo=CHILE).isoformat()})
        assert made.status_code == 201, made.text
        self.call = made.json()

    def close(self) -> None:
        repository_module._OUTBOX = self._original_outbox
        self.pool.close()

    @property
    def path(self) -> str:
        return f"{CLUBS_PATH}/{self.club_id}/announcements/{self.call['id']}"

    def patch(self, body):
        return self.client.patch(self.path, headers=self.headers, json=body)

    def applied(self) -> dict:
        athlete = uuid4()
        response = self.client.post(f"/api/v1/clubs/announcements/{self.call['id']}/applications",
                                    headers=token(athlete, "player"), json={})
        assert response.status_code == 201, response.text
        return {"user": athlete, **response.json()}

    def review(self, postulacion_id, estado):
        return self.client.patch(f"/api/v1/clubs/announcements/applications/{postulacion_id}",
                                 headers=self.headers, json={"estado": estado})

    def notices(self) -> list[tuple]:
        with self.pool.connection() as conn:
            return conn.execute(
                sql.SQL("SELECT user_id, kind, data FROM {} WHERE data ? 'cambios' ORDER BY id").format(self.outbox)
            ).fetchall()

    def history(self) -> list[tuple]:
        with self.pool.connection() as conn:
            return conn.execute(
                "SELECT usuario_id, cambios, avisados, fecha FROM convocatoria_historial ORDER BY id").fetchall()

    def count(self, table: str) -> int:
        with self.pool.connection() as conn:
            return conn.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))).fetchone()[0]


@pytest.fixture
def pg(base):  # noqa: F811
    setup, schema, dsn = base
    build(setup, schema)
    world = Pg(setup, schema, dsn)
    try:
        yield world
    finally:
        world.close()


def test_the_migration_is_repeatable_and_the_table_enforces_its_rules(base):  # noqa: F811
    setup, schema, _ = base
    build(setup, schema)
    for _ in range(2):
        apply(setup, schema, MIGRATION)
    columns = {c: (n, t) for c, n, t in setup.execute(
        "SELECT column_name, is_nullable, data_type FROM information_schema.columns "
        "WHERE table_schema = %s AND table_name = 'convocatoria_historial'", (schema,))}
    assert set(columns) == {"id", "convocatoria_id", "usuario_id", "fecha", "cambios", "avisados"}
    assert columns["cambios"] == ("NO", "jsonb")
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        setup.execute("INSERT INTO convocatoria_historial (convocatoria_id, usuario_id, cambios) VALUES (%s, %s, '{\"a\":1}')",
                      (uuid4(), uuid4()))


def test_editing_through_the_api_updates_the_row_the_history_and_the_notices(pg):
    first, second = pg.applied(), pg.applied()
    rejected = pg.applied()
    assert pg.review(rejected["id"], "RECHAZADO").status_code == 200
    before = pg.count("convocatoria_historial")
    response = pg.patch({"direccion": "Av. Nueva 77", "comuna": "Macul", "titulo": "Otro título"})
    assert response.status_code == 200, response.text
    assert response.json()["direccion"] == "Av. Nueva 77" and response.json()["titulo"] == "Otro título"
    notices = pg.notices()
    assert {n[0] for n in notices} == {first["user"], second["user"]} and len(notices) == 2
    assert notices[0][1] == "convocatoria" and notices[0][2]["cambios"] == ["lugar"]
    ((actor, cambios, avisados, fecha),) = pg.history()[before:]
    assert actor == pg.owner and avisados == 2 and fecha is not None
    assert cambios["direccion"] == {"antes": "Av. Grecia 2001", "despues": "Av. Nueva 77"}
    assert cambios["titulo"]["despues"] == "Otro título"


def test_repeating_the_same_request_writes_nothing_more(pg):
    pg.applied()
    body = {"direccion": "Av. Nueva 77"}
    assert pg.patch(body).status_code == 200
    rows = (len(pg.notices()), len(pg.history()))
    assert pg.patch(body).status_code == 200 and pg.patch(body).status_code == 200
    assert (len(pg.notices()), len(pg.history())) == rows == (1, 1)


def test_minor_changes_write_history_but_no_notice(pg):
    pg.applied()
    assert pg.patch({"descripcion": "Nueva", "cupos": 9}).status_code == 200
    assert pg.notices() == [] and len(pg.history()) == 1 and pg.history()[0][2] == 0


def test_cupos_floor_equal_allowed_and_below_refused(pg):
    for _ in range(3):
        assert pg.review(pg.applied()["id"], "ACEPTADO").status_code == 200
    pg.applied()
    below = pg.patch({"cupos": 2})
    assert below.status_code == 409 and "3 postulantes aceptados" in below.json()["detail"]
    assert pg.patch({"cupos": 3}).status_code == 200
    assert pg.client.get(f"/api/v1/clubs/announcements/{pg.call['id']}").json()["cupos"] == 3


def test_a_failing_notice_keeps_the_edit_and_logs(pg, caplog):
    pg.applied()
    # The outbox table is gone: the INSERT of the notice fails, the edit must survive.
    pg.setup.execute(sql.SQL("DROP TABLE {}").format(pg.outbox))
    with caplog.at_level(logging.ERROR):
        response = pg.patch({"direccion": "Av. Nueva 77"})
    assert response.status_code == 200, response.text
    assert pg.client.get(f"/api/v1/clubs/announcements/{pg.call['id']}").json()["direccion"] == "Av. Nueva 77"
    assert any("notice could not be written" in r.getMessage() for r in caplog.records)
    assert len(pg.history()) == 1 and pg.history()[0][2] == 0


def test_closed_convocatoria_is_409_and_nothing_changes(pg):
    assert pg.client.post(f"{pg.path}/close", headers=pg.headers).status_code == 200
    assert pg.patch({"titulo": "Otro título"}).status_code == 409
    assert pg.history() == []


def test_accepting_and_lowering_the_cupos_at_the_same_time_never_leave_cupos_below_the_accepted(pg):
    """Many rounds of: cupos 3 with 2 accepted and 1 pending; one thread accepts the pending one while another lowers
    the cupos to 2. Either order is fine as long as the end state keeps accepted <= cupos."""
    outcomes = {"accepted_first": 0, "lowered_first": 0}
    for round_ in range(12):
        # a fresh convocatoria per round on the same club
        made = pg.client.post(f"{CLUBS_PATH}/{pg.club_id}/announcements", headers=pg.headers, json={
            "deporte_id": 1, "categoria_edad": "Sub-17", "requisitos": ["Ficha médica"], "cupos": 3,
            "direccion": "Av. Grecia 2001",
            "fecha_prueba": datetime.combine(date.today() + timedelta(days=10), time(10), tzinfo=CHILE).isoformat()})
        call_id = made.json()["id"]
        athletes = []
        for _ in range(3):
            who = uuid4()
            athletes.append(pg.client.post(f"/api/v1/clubs/announcements/{call_id}/applications",
                                           headers=token(who, "player"), json={}).json()["id"])
        for pid in athletes[:2]:
            assert pg.review(pid, "ACEPTADO").status_code == 200
        barrier = threading.Barrier(2)
        results: dict[str, int] = {}

        def accept():
            barrier.wait()
            results["accept"] = pg.review(athletes[2], "ACEPTADO").status_code

        def lower():
            barrier.wait()
            results["lower"] = pg.client.patch(f"{CLUBS_PATH}/{pg.club_id}/announcements/{call_id}",
                                               headers=pg.headers, json={"cupos": 2}).status_code

        threads = [threading.Thread(target=accept), threading.Thread(target=lower)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        with pg.pool.connection() as conn:
            cupos = conn.execute("SELECT cupos FROM convocatoria WHERE id = %s", (call_id,)).fetchone()[0]
            accepted = conn.execute("SELECT count(*) FROM postulacion_convocatoria "
                                    "WHERE convocatoria_id = %s AND estado = 'ACEPTADO'", (call_id,)).fetchone()[0]
        assert accepted <= cupos, (round_, results, cupos, accepted)
        assert set(results.values()) <= {200, 409}, results
        if results["accept"] == 200:
            assert (results["lower"], cupos) == (409, 3) and accepted == 3
            outcomes["accepted_first"] += 1
        else:
            assert (results["lower"], cupos, accepted) == (200, 2, 2)
            outcomes["lowered_first"] += 1
    assert sum(outcomes.values()) == 12
