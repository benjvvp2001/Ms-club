"""HU-67: quien ya está ACEPTADO no se puede preseleccionar. El resto de las transiciones queda igual."""
from __future__ import annotations

from uuid import uuid4

import pytest

from test_club_admin import nuevo_rut, token  # also sets JWT_SECRET before the app is imported
from test_postulaciones import CALLS_PATH
from test_revision_postulaciones import REVIEW_PATH, Review

REFUSED = {"detail": "Esta postulación ya fue aceptada; no se puede preseleccionar."}


def accepted(setup: Review, **kw):
    athlete_id, headers, postulacion = setup.applied(**kw)
    assert setup.review(postulacion["id"], {"estado": "ACEPTADO"}).status_code == 200
    return athlete_id, headers, postulacion


def stored(setup: Review, postulacion_id: str) -> dict:
    return next(c for c in setup.candidates().json()["items"] if c["id"] == postulacion_id)


def test_preselecting_a_pending_applicant_still_works_and_notifies():
    setup = Review()
    _, _, postulacion = setup.applied()
    response = setup.review(postulacion["id"], {"estado": "PRESELECCIONADO"})
    assert response.status_code == 200 and response.json()["estado"] == "PRESELECCIONADO"
    assert [e.data["estado"] for e in setup.repository.outbox] == ["PRESELECCIONADO"]


def test_accepting_a_preselected_applicant_still_works():
    setup = Review()
    _, _, postulacion = setup.applied()
    setup.review(postulacion["id"], {"estado": "PRESELECCIONADO"})
    response = setup.review(postulacion["id"], {"estado": "ACEPTADO"})
    assert response.status_code == 200 and response.json()["estado"] == "ACEPTADO"
    assert [e.data["estado"] for e in setup.repository.outbox] == ["PRESELECCIONADO", "ACEPTADO"]


def test_preselecting_an_accepted_applicant_is_409_and_changes_nothing():
    setup = Review(cupos=3)
    _, _, postulacion = accepted(setup)
    setup.review(postulacion["id"], {"estado": "ACEPTADO", "comentario": "Bienvenido"})
    before = stored(setup, postulacion["id"])
    notices = len(setup.repository.outbox)
    counts = setup.candidates().json()["conteo_por_estado"]
    available = setup.client.get(f"{CALLS_PATH}/{setup.call['id']}").json()["cupos_disponibles"]

    response = setup.review(postulacion["id"], {"estado": "PRESELECCIONADO", "comentario": "no debe guardarse"})
    assert response.status_code == 409 and response.json() == REFUSED

    assert stored(setup, postulacion["id"]) == before                      # state, comment and dates untouched
    assert before["estado"] == "ACEPTADO" and before["comentario_club"] == "Bienvenido"
    assert len(setup.repository.outbox) == notices                        # no notice
    assert setup.candidates().json()["conteo_por_estado"] == counts      # same count per estado
    assert setup.client.get(f"{CALLS_PATH}/{setup.call['id']}").json()["cupos_disponibles"] == available


def test_the_refusal_does_not_depend_on_the_comment_or_how_many_times_it_is_asked():
    setup = Review()
    _, _, postulacion = accepted(setup)
    for body in ({"estado": "PRESELECCIONADO"}, {"estado": "PRESELECCIONADO", "comentario": None},
                 {"estado": "PRESELECCIONADO", "comentario": "x"}):
        assert setup.review(postulacion["id"], body).status_code == 409
    assert stored(setup, postulacion["id"])["estado"] == "ACEPTADO"


def test_preselecting_twice_behaves_as_before():
    setup = Review()
    _, _, postulacion = setup.applied()
    first = setup.review(postulacion["id"], {"estado": "PRESELECCIONADO"})
    second = setup.review(postulacion["id"], {"estado": "PRESELECCIONADO", "comentario": "Nota"})
    assert first.status_code == second.status_code == 200
    assert second.json()["comentario_club"] == "Nota"                     # the comment may still change
    assert [e.data["estado"] for e in setup.repository.outbox] == ["PRESELECCIONADO"]   # one notice only


def test_preselecting_a_rejected_applicant_keeps_the_current_behaviour():
    setup = Review(cupos=1)
    _, _, ana = setup.applied()
    setup.review(ana["id"], {"estado": "RECHAZADO"})
    assert setup.review(ana["id"], {"estado": "PRESELECCIONADO"}).json()["estado"] == "PRESELECCIONADO"  # a cupo is free

    setup.review(ana["id"], {"estado": "RECHAZADO"})
    setup.applied()                                                        # somebody takes the freed cupo
    full = setup.review(ana["id"], {"estado": "PRESELECCIONADO"})
    assert full.status_code == 409
    assert full.json() == {"detail": "La convocatoria no tiene cupos para volver a considerar esta postulación."}


@pytest.mark.parametrize("estado", ["PENDIENTE", "RECHAZADO", "ACEPTADO"])
def test_every_other_transition_out_of_accepted_is_unchanged(estado):
    setup = Review()
    _, _, postulacion = accepted(setup)
    response = setup.review(postulacion["id"], {"estado": estado})
    assert response.status_code == 200 and response.json()["estado"] == estado


def test_an_applicant_can_be_preselected_again_after_leaving_accepted():
    setup = Review()
    _, _, postulacion = accepted(setup)
    setup.review(postulacion["id"], {"estado": "PENDIENTE"})
    assert setup.review(postulacion["id"], {"estado": "PRESELECCIONADO"}).status_code == 200


def test_the_other_applicants_are_not_affected():
    setup = Review(cupos=3)
    _, _, ana = accepted(setup)
    _, _, bea = setup.applied()
    assert setup.review(ana["id"], {"estado": "PRESELECCIONADO"}).status_code == 409
    assert setup.review(bea["id"], {"estado": "PRESELECCIONADO"}).status_code == 200


def test_who_may_try_it_follows_the_usual_pattern():
    setup = Review()
    _, athlete_headers, postulacion = accepted(setup)
    body = {"estado": "PRESELECCIONADO"}
    assert setup.client.patch(f"{REVIEW_PATH}/{postulacion['id']}", json=body).status_code == 401
    assert setup.review(postulacion["id"], body, athlete_headers).status_code == 403
    other = token(uuid4())
    assert setup.client.post("/api/v1/clubs", headers=other, json={"nombre": "Otro club", "rut": nuevo_rut()}).status_code == 201
    assert setup.review(postulacion["id"], body, other).status_code == 403          # another club: never reaches the rule
    assert stored(setup, postulacion["id"])["estado"] == "ACEPTADO"


def test_the_whole_convocatoria_flow_is_the_same_including_the_edit_and_the_close():
    setup = Review(cupos=2)
    _, _, ana = setup.applied()
    _, _, bea = setup.applied()
    assert setup.review(ana["id"], {"estado": "PRESELECCIONADO"}).status_code == 200
    assert setup.review(ana["id"], {"estado": "ACEPTADO"}).status_code == 200
    assert setup.review(ana["id"], {"estado": "PRESELECCIONADO"}).status_code == 409
    assert setup.review(bea["id"], {"estado": "RECHAZADO"}).status_code == 200
    path = f"/api/v1/clubs/{setup.club_id}/announcements/{setup.call['id']}"
    assert setup.client.patch(path, headers=setup.owner_headers, json={"cupos": 1}).status_code == 200     # HU-68: not below accepted
    assert setup.client.patch(path, headers=setup.owner_headers, json={"cupos": 0}).status_code == 400
    assert setup.client.post(f"{path}/close", headers=setup.owner_headers).status_code == 200
    assert stored(setup, ana["id"])["estado"] == "ACEPTADO"
