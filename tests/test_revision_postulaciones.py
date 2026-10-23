from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from test_club_admin import nuevo_rut, token  # also sets JWT_SECRET before the app is imported
from test_postulaciones import CALLS_PATH, Setup

REVIEW_PATH = "/api/v1/clubs/announcements/applications"


class Review(Setup):
    """A club with a convocatoria and helpers to apply and review."""

    def __init__(self, **convocatoria: object) -> None:
        super().__init__()
        self.call = self.convocatoria(**convocatoria)

    def applied(self, **profile: object) -> tuple[UUID, dict, dict]:
        """An athlete who applied; returns (athlete id, their headers, the postulación)."""
        athlete_id, headers = self.athlete(**profile)
        response = self.apply(self.call["id"], headers, {"mensaje": "Juego de arquero."})
        assert response.status_code == 201, response.text
        return athlete_id, headers, response.json()

    def candidates(self, headers: dict | None = None, **params: object):
        return self.client.get(
            f"{CALLS_PATH}/{self.call['id']}/applications",
            headers=self.owner_headers if headers is None else headers,
            params=params,
        )

    def review(self, postulacion_id: object, body: dict, headers: dict | None = None):
        return self.client.patch(
            f"{REVIEW_PATH}/{postulacion_id}",
            headers=self.owner_headers if headers is None else headers,
            json=body,
        )


# -- candidates list ----------------------------------------------------------------------


def test_club_lists_candidates_with_profile_and_application_date():
    setup = Review(cupos=5)
    ana_id, _, ana = setup.applied(nombre="Ana", edad=16)
    _, _, bea = setup.applied(nombre="Bea", edad=15)
    response = setup.candidates()
    assert response.status_code == 200, response.text
    page = response.json()
    assert page["convocatoria"]["id"] == setup.call["id"]
    assert page["total"] == 2
    assert [c["id"] for c in page["items"]] == [ana["id"], bea["id"]]  # oldest first
    first = page["items"][0]
    assert first["usuario_id"] == str(ana_id)
    assert first["estado"] == "PENDIENTE"
    assert first["perfil_deportivo"]["nombre"] == "Ana" and first["perfil_deportivo"]["edad"] == 16
    assert first["mensaje"] == "Juego de arquero."
    assert datetime.fromisoformat(first["fecha_postulacion"]) <= datetime.now(timezone.utc)
    assert page["conteo_por_estado"] == {"PENDIENTE": 2, "PRESELECCIONADO": 0, "ACEPTADO": 0, "RECHAZADO": 0}


def test_candidates_filter_by_estado_keeps_overall_counts():
    setup = Review(cupos=5)
    _, _, ana = setup.applied()
    _, _, bea = setup.applied()
    setup.applied()
    setup.review(ana["id"], {"estado": "PRESELECCIONADO"})
    setup.review(bea["id"], {"estado": "RECHAZADO"})
    for estado, expected in [("PRESELECCIONADO", [ana["id"]]), ("RECHAZADO", [bea["id"]]), ("ACEPTADO", [])]:
        page = setup.candidates(estado=estado).json()
        assert [c["id"] for c in page["items"]] == expected
        assert page["total"] == len(expected)
        assert page["conteo_por_estado"] == {"PENDIENTE": 1, "PRESELECCIONADO": 1, "ACEPTADO": 0, "RECHAZADO": 1}
    paged = setup.candidates(page=2, page_size=2).json()
    assert len(paged["items"]) == 1 and paged["total"] == 3


@pytest.mark.parametrize(
    "params", [{"estado": "Pendiente"}, {"estado": "EN_REVISION"}, {"page": 0}, {"page_size": 51}, {"orden": "x"}]
)
def test_invalid_candidate_filters_are_400(params):
    setup = Review()
    assert setup.candidates(**params).status_code == 400


def test_candidates_of_unknown_convocatoria_is_404():
    setup = Review()
    response = setup.client.get(f"{CALLS_PATH}/{uuid4()}/applications", headers=setup.owner_headers)
    assert response.status_code == 404


# -- status changes and notifications ------------------------------------------------------


@pytest.mark.parametrize(
    ("estado", "title", "body"),
    [
        ("PRESELECCIONADO", "Quedaste preseleccionado", "Club Ñuñoa te preseleccionó en «Prueba Sub-17»."),
        ("ACEPTADO", "¡Te seleccionaron!", "Club Ñuñoa confirmó tu selección en «Prueba Sub-17»."),
        ("RECHAZADO", "Postulación no seleccionada", "Club Ñuñoa no continuará con tu postulación a «Prueba Sub-17»."),
    ],
)
def test_status_change_notifies_the_athlete(estado, title, body):
    setup = Review()
    athlete_id, athlete_headers, postulacion = setup.applied()
    response = setup.review(postulacion["id"], {"estado": estado, "comentario": "Gracias por venir."})
    assert response.status_code == 200, response.text
    assert response.json()["estado"] == estado
    assert response.json()["comentario_club"] == "Gracias por venir."

    [evento] = setup.repository.outbox
    assert evento.user_id == athlete_id
    assert (evento.kind, evento.title, evento.body) == ("convocatoria", title, body)
    assert evento.data == {
        "screen": "convocatoria",
        "convocatoriaId": setup.call["id"],
        "postulacionId": postulacion["id"],
        "estado": estado,
    }
    assert "Gracias" not in evento.body  # the comment stays in the app, not on the lock screen

    mine = setup.client.get(f"{CALLS_PATH}/applications/me", headers=athlete_headers).json()["items"][0]
    assert mine["estado"] == estado and mine["comentario_club"] == "Gracias por venir."


def test_notification_names_the_tryout_when_it_has_no_title():
    setup = Review(titulo=...)
    _, _, postulacion = setup.applied()
    setup.review(postulacion["id"], {"estado": "ACEPTADO"})
    assert setup.repository.outbox[0].body == "Club Ñuñoa confirmó tu selección en la prueba de Fútbol Sub-17."


def test_repeating_the_same_estado_does_not_notify_again():
    setup = Review()
    _, _, postulacion = setup.applied()
    setup.review(postulacion["id"], {"estado": "PRESELECCIONADO"})
    again = setup.review(postulacion["id"], {"estado": "PRESELECCIONADO", "comentario": "Te esperamos el sábado."})
    assert again.status_code == 200
    assert again.json()["comentario_club"] == "Te esperamos el sábado."
    assert len(setup.repository.outbox) == 1


def test_comment_left_out_is_kept_and_null_removes_it():
    setup = Review()
    _, _, postulacion = setup.applied()
    setup.review(postulacion["id"], {"estado": "PRESELECCIONADO", "comentario": "Buen nivel."})
    kept = setup.review(postulacion["id"], {"estado": "ACEPTADO"}).json()
    assert kept["comentario_club"] == "Buen nivel."
    removed = setup.review(postulacion["id"], {"estado": "ACEPTADO", "comentario": None}).json()
    assert removed["comentario_club"] is None


def test_full_flow_preselect_then_accept_notifies_each_step():
    setup = Review()
    _, _, postulacion = setup.applied()
    for estado in ("PRESELECCIONADO", "ACEPTADO"):
        assert setup.review(postulacion["id"], {"estado": estado}).status_code == 200
    assert [e.data["estado"] for e in setup.repository.outbox] == ["PRESELECCIONADO", "ACEPTADO"]


def test_rejection_frees_a_cupo_and_reconsidering_needs_one():
    setup = Review(cupos=1)
    _, _, ana = setup.applied()
    assert setup.client.get(f"{CALLS_PATH}/{setup.call['id']}").json()["cupos_disponibles"] == 0
    setup.review(ana["id"], {"estado": "RECHAZADO"})
    assert setup.client.get(f"{CALLS_PATH}/{setup.call['id']}").json()["cupos_disponibles"] == 1

    _, _, bea = setup.applied()  # takes the freed cupo
    back = setup.review(ana["id"], {"estado": "ACEPTADO"})
    assert back.status_code == 409
    assert back.json() == {"detail": "La convocatoria no tiene cupos para volver a considerar esta postulación."}
    assert setup.candidates(estado="RECHAZADO").json()["items"][0]["id"] == ana["id"]
    assert len(setup.repository.outbox) == 1  # only the rejection

    setup.review(bea["id"], {"estado": "RECHAZADO"})
    assert setup.review(ana["id"], {"estado": "ACEPTADO"}).status_code == 200


def test_selection_can_be_confirmed_after_the_tryout_date():
    setup = Review()
    _, _, postulacion = setup.applied()
    setup.repository._convocatorias[UUID(setup.call["id"])]["fecha_prueba"] = (  # noqa: SLF001
        datetime.now(timezone.utc) - timedelta(days=1)
    )
    assert setup.candidates().json()["convocatoria"]["estado"] == "FINALIZADA"
    assert setup.review(postulacion["id"], {"estado": "ACEPTADO"}).status_code == 200


def test_owner_of_an_inactive_club_still_reviews():
    setup = Review()
    _, _, postulacion = setup.applied()
    setup.repository._clubs[setup.club_id].is_active = False  # noqa: SLF001 - no endpoint deactivates clubs
    assert setup.candidates().status_code == 200
    assert setup.review(postulacion["id"], {"estado": "RECHAZADO"}).status_code == 200


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"estado": "PENDIENTE"},
        {"estado": "EN_REVISION"},
        {"estado": "aceptado"},
        {"estado": "ACEPTADO", "comentario": "x" * 1001},
        {"estado": "ACEPTADO", "usuario_id": str(uuid4())},
    ],
)
def test_invalid_review_is_400_and_changes_nothing(body):
    setup = Review()
    _, _, postulacion = setup.applied()
    assert setup.review(postulacion["id"], body).status_code == 400
    assert setup.candidates().json()["items"][0]["estado"] == "PENDIENTE"
    assert setup.repository.outbox == []


def test_review_of_unknown_or_malformed_postulacion():
    setup = Review()
    missing = setup.review(uuid4(), {"estado": "ACEPTADO"})
    assert missing.status_code == 404 and missing.json() == {"detail": "Postulación no encontrada."}
    assert setup.review("no-es-uuid", {"estado": "ACEPTADO"}).status_code == 400


# -- access control ----------------------------------------------------------------------------


def test_review_routes_require_a_token():
    setup = Review()
    _, _, postulacion = setup.applied()
    assert setup.client.get(f"{CALLS_PATH}/{setup.call['id']}/applications").status_code == 401
    assert setup.client.patch(f"{REVIEW_PATH}/{postulacion['id']}", json={"estado": "ACEPTADO"}).status_code == 401


@pytest.mark.parametrize(
    ("who", "detail"),
    [
        ("athlete", "Solo un representante de club puede administrar clubes."),
        ("admin", "Solo un representante de club puede administrar clubes."),
        ("another club", "No administras este club."),
    ],
)
def test_only_the_owning_club_lists_and_reviews(who, detail):
    setup = Review()
    _, athlete_headers, postulacion = setup.applied()
    if who == "athlete":
        headers = athlete_headers
    elif who == "admin":
        headers = token(uuid4(), "admin")
    else:
        headers = token(uuid4())
        assert setup.client.post("/api/v1/clubs", headers=headers, json={"nombre": "Otro club", "rut": nuevo_rut()}).status_code == 201
    listed = setup.candidates(headers)
    reviewed = setup.review(postulacion["id"], {"estado": "ACEPTADO"}, headers)
    assert (listed.status_code, reviewed.status_code) == (403, 403)
    assert listed.json() == reviewed.json() == {"detail": detail}
    assert setup.candidates().json()["items"][0]["estado"] == "PENDIENTE"
    assert setup.repository.outbox == []
