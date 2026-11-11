"""HU-67 on real PostgreSQL: an accepted applicant cannot be preselected, decided under the row lock, also when the
acceptance and the preselection arrive at the same moment. Runs only with CLUBS_TEST_DATABASE_URL (qa_tests)."""
from __future__ import annotations

import threading

from psycopg import sql

from test_editar_convocatoria_postgres import Pg, pg  # noqa: F401  (pg: fixture)
from pg_support import base  # noqa: F401  (base: fixture)

REFUSED = "Esta postulación ya fue aceptada; no se puede preseleccionar."


def row(world: Pg, postulacion_id: str) -> tuple:
    with world.pool.connection() as conn:
        return conn.execute(
            "SELECT estado, comentario_club, fecha_actualizacion FROM postulacion_convocatoria WHERE id = %s",
            (postulacion_id,)).fetchone()


def notices(world: Pg, postulacion_id: str) -> list[str]:
    with world.pool.connection() as conn:
        rows = conn.execute(
            sql.SQL("SELECT data->>'estado' FROM {} WHERE data->>'postulacionId' = %s ORDER BY id").format(world.outbox),
            (postulacion_id,)).fetchall()
    return [r[0] for r in rows]


def test_preselecting_an_accepted_applicant_is_refused_and_the_row_is_untouched(pg):
    ana = pg.applied()
    assert pg.review(ana["id"], "ACEPTADO").status_code == 200
    before, sent = row(pg, ana["id"]), notices(pg, ana["id"])
    response = pg.review(ana["id"], "PRESELECCIONADO")
    assert response.status_code == 409 and response.json() == {"detail": REFUSED}
    assert row(pg, ana["id"]) == before                         # estado, comment and updated date identical
    assert notices(pg, ana["id"]) == sent == ["ACEPTADO"]


def test_the_rest_of_the_transitions_work_on_the_real_database(pg):
    ana, bea = pg.applied(), pg.applied()
    for estado in ("PRESELECCIONADO", "PRESELECCIONADO", "ACEPTADO", "PENDIENTE", "PRESELECCIONADO", "RECHAZADO",
                   "PRESELECCIONADO", "ACEPTADO", "ACEPTADO"):
        assert pg.review(ana["id"], estado).status_code == 200, estado
    assert row(pg, ana["id"])[0] == "ACEPTADO"
    assert pg.review(bea["id"], "RECHAZADO").status_code == 200
    assert notices(pg, ana["id"]) == ["PRESELECCIONADO", "ACEPTADO", "PRESELECCIONADO", "RECHAZADO", "PRESELECCIONADO",
                                      "ACEPTADO"]                # repeats and PENDIENTE send nothing, as before


def test_accepting_and_preselecting_at_the_same_moment_never_ends_preselected_after_accepted(pg):
    for _ in range(12):
        applicant = pg.applied()
        barrier, answers = threading.Barrier(2), {}

        def go(estado):
            barrier.wait()
            answers[estado] = pg.review(applicant["id"], estado)

        threads = [threading.Thread(target=go, args=(e,)) for e in ("ACEPTADO", "PRESELECCIONADO")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert answers["ACEPTADO"].status_code == 200
        assert answers["PRESELECCIONADO"].status_code in (200, 409)
        assert row(pg, applicant["id"])[0] == "ACEPTADO"         # whatever the order, the last word is the acceptance
        sent = notices(pg, applicant["id"])
        if answers["PRESELECCIONADO"].status_code == 409:
            assert answers["PRESELECCIONADO"].json() == {"detail": REFUSED} and sent == ["ACEPTADO"]
        else:
            assert sent == ["PRESELECCIONADO", "ACEPTADO"]       # it went first: both steps, in order
        assert pg.review(applicant["id"], "RECHAZADO").status_code == 200   # frees the cupo for the next round


def test_many_preselections_racing_an_acceptance_leave_one_consistent_state(pg):
    applicant = pg.applied()
    assert pg.review(applicant["id"], "ACEPTADO").status_code == 200
    barrier, codes = threading.Barrier(6), []

    def go():
        barrier.wait()
        codes.append(pg.review(applicant["id"], "PRESELECCIONADO").status_code)

    threads = [threading.Thread(target=go) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert codes == [409] * 6 and row(pg, applicant["id"])[0] == "ACEPTADO"
    assert notices(pg, applicant["id"]) == ["ACEPTADO"]
