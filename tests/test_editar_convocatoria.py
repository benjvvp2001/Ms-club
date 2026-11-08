"""HU-68: editar una convocatoria de club (repositorio simulado, sin base de datos)."""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from uuid import uuid4

import pytest

from test_club_admin import nuevo_rut, token  # also sets JWT_SECRET before the app is imported
from test_postulaciones import CALLS_PATH, CHILE, CLUBS_PATH
from test_revision_postulaciones import Review


def en_dias(days: int, hora: int = 10) -> str:
    return datetime.combine(date.today() + timedelta(days=days), time(hora), tzinfo=CHILE).isoformat()


class Edit(Review):
    def __init__(self, **convocatoria: object) -> None:
        super().__init__(**convocatoria)

    @property
    def path(self) -> str:
        return f"{CLUBS_PATH}/{self.club_id}/announcements/{self.call['id']}"

    def patch(self, body: dict, headers: dict | None = None, path: str | None = None):
        return self.client.patch(path or self.path, headers=self.owner_headers if headers is None else headers, json=body)

    def avisos(self) -> list:
        return [e for e in self.repository.outbox if e.data.get("screen") == "convocatoria" and "cambios" in e.data]

    def accept(self, postulacion_id: str) -> None:
        assert self.review(postulacion_id, {"estado": "ACEPTADO"}).status_code == 200


# -- edición correcta -----------------------------------------------------------------------------


def test_the_owner_edits_only_the_fields_sent():
    setup = Edit()
    response = setup.patch({"titulo": "Prueba Sub-17 (nueva)", "cupos": 30})
    assert response.status_code == 200, response.text
    call = response.json()
    assert call["titulo"] == "Prueba Sub-17 (nueva)" and call["cupos"] == 30
    assert call["direccion"] == setup.call["direccion"] and call["fecha_prueba"] == setup.call["fecha_prueba"]
    assert setup.client.get(f"{CALLS_PATH}/{setup.call['id']}").json() == call


def test_null_clears_the_optional_fields():
    setup = Edit()
    call = setup.patch({"titulo": None, "edad_minima": None, "edad_maxima": None}).json()
    assert call["titulo"] is None and call["edad_minima"] is None and call["edad_maxima"] is None


def test_every_field_that_can_be_created_can_be_edited():
    setup = Edit()
    body = {"deporte_id": 1, "categoria_edad": "Sub-19", "edad_minima": 17, "edad_maxima": 19,
            "fecha_prueba": en_dias(20), "requisitos": ["Cédula"], "cupos": 12, "direccion": "Calle Nueva 123",
            "comuna": "Macul", "titulo": "Otro título", "descripcion": "Otra descripción",
            "chat_postulantes_habilitado": False}
    call = setup.patch(body).json()
    assert call["categoria_edad"] == "Sub-19" and call["requisitos"] == ["Cédula"] and call["comuna"] == "Macul"
    assert call["chat_postulantes_habilitado"] is False and call["cupos"] == 12


# -- quién puede -----------------------------------------------------------------------------------


def test_no_session_is_401():
    setup = Edit()
    assert setup.client.patch(setup.path, json={"cupos": 3}).status_code == 401


def test_an_athlete_is_403():
    setup = Edit()
    _, headers = setup.athlete()
    assert setup.patch({"cupos": 3}, headers).status_code == 403


def test_another_clubs_representative_is_403_on_the_club_and_404_on_a_convocatoria_of_his_own_club():
    setup = Edit()
    other_headers = token(uuid4())
    created = setup.client.post(CLUBS_PATH, headers=other_headers,
                                json={"nombre": "Otro Club", "rut": nuevo_rut(), "deporte_ids": [1]})
    other_club = created.json()["id"]
    # through the owner's club: not an administrator of it
    assert setup.patch({"cupos": 3}, other_headers).status_code == 403
    # through his own club, pointing to the owner's convocatoria: the same 404 as a missing one
    stolen = setup.patch({"cupos": 3}, other_headers, f"{CLUBS_PATH}/{other_club}/announcements/{setup.call['id']}")
    missing = setup.patch({"cupos": 3}, other_headers, f"{CLUBS_PATH}/{other_club}/announcements/{uuid4()}")
    assert stolen.status_code == missing.status_code == 404
    assert stolen.json() == missing.json()
    assert setup.client.get(f"{CALLS_PATH}/{setup.call['id']}").json()["cupos"] == setup.call["cupos"]


def test_a_platform_admin_without_a_club_is_403():
    setup = Edit()
    assert setup.patch({"cupos": 3}, token(uuid4(), "admin")).status_code == 403


def test_unknown_convocatoria_is_404():
    setup = Edit()
    assert setup.patch({"cupos": 3}, path=f"{CLUBS_PATH}/{setup.club_id}/announcements/{uuid4()}").status_code == 404


# -- validaciones ----------------------------------------------------------------------------------


@pytest.mark.parametrize("body", [
    {},                                                    # nothing to change
    {"cupos": None}, {"fecha_prueba": None}, {"direccion": None}, {"requisitos": None},   # not nullable
    {"cupos": 0}, {"cupos": 1001}, {"cupos": "x"},
    {"direccion": "abc"}, {"categoria_edad": "x"}, {"titulo": "ab"}, {"descripcion": "x" * 2001},
    {"requisitos": []}, {"requisitos": ["x"]}, {"requisitos": ["Ficha médica"] * 16},
    {"edad_minima": 3}, {"edad_maxima": 100}, {"edad_minima": 18, "edad_maxima": 16},
    {"comuna": "Ñ<script>"}, {"deporte_id": 0},
    {"estado": "CERRADA"}, {"club_id": str(uuid4())}, {"cerrada_en": "2026-01-01T00:00:00Z"}, {"id": str(uuid4())},
    {"cupos_disponibles": 3}, {"cualquier_cosa": 1},      # not editable / unknown
])
def test_invalid_data_is_400_and_changes_nothing(body):
    setup = Edit()
    response = setup.patch(body)
    assert response.status_code == 400, response.text
    assert setup.client.get(f"{CALLS_PATH}/{setup.call['id']}").json() == setup.call


@pytest.mark.parametrize("fecha", [
    (datetime.now(CHILE) - timedelta(days=1)).isoformat(),   # past
    (datetime.now(CHILE) + timedelta(days=400)).isoformat(),  # too far
    "2030-01-01T10:00:00",                                    # no time zone
    "mañana",
])
def test_the_date_must_be_future_within_a_year_and_carry_its_zone(fecha):
    setup = Edit()
    assert setup.patch({"fecha_prueba": fecha}).status_code == 400
    assert setup.avisos() == []


def test_the_age_range_is_checked_against_the_stored_value():
    setup = Edit()                       # stored 15-17
    assert setup.patch({"edad_minima": 18}).status_code == 400
    assert setup.patch({"edad_maxima": 14}).status_code == 400
    assert setup.patch({"edad_minima": 16}).status_code == 200


def test_the_sport_must_be_one_the_club_offers():
    setup = Edit()
    assert setup.patch({"deporte_id": 999}).status_code == 400


# -- cupos -----------------------------------------------------------------------------------------


def test_cupos_cannot_go_below_the_accepted_athletes_but_can_equal_them():
    setup = Edit(cupos=5)
    for _ in range(3):
        setup.accept(setup.applied()[2]["id"])
    below = setup.patch({"cupos": 2})
    assert below.status_code == 409
    assert "3 postulantes aceptados" in below.json()["detail"]
    assert setup.client.get(f"{CALLS_PATH}/{setup.call['id']}").json()["cupos"] == 5
    assert setup.patch({"cupos": 3}).status_code == 200                       # equal: allowed
    assert setup.client.get(f"{CALLS_PATH}/{setup.call['id']}").json()["cupos"] == 3


def test_only_accepted_athletes_count_for_the_floor():
    setup = Edit(cupos=5)
    setup.accept(setup.applied()[2]["id"])
    _, _, pendiente = setup.applied()
    _, _, rechazada = setup.applied()
    setup.review(rechazada["id"], {"estado": "RECHAZADO"})
    setup.review(pendiente["id"], {"estado": "PRESELECCIONADO"})
    assert setup.patch({"cupos": 1}).status_code == 200


# -- estados ---------------------------------------------------------------------------------------


def test_a_closed_convocatoria_cannot_be_edited():
    setup = Edit()
    assert setup.client.post(f"{setup.path}/close", headers=setup.owner_headers).status_code == 200
    response = setup.patch({"titulo": "Nuevo título"})
    assert response.status_code == 409 and "cerrada" in response.json()["detail"]


def test_a_finished_convocatoria_cannot_be_edited():
    setup = Edit()
    setup.repository._convocatorias[__import__("uuid").UUID(setup.call["id"])]["fecha_prueba"] = (
        datetime.now(CHILE) - timedelta(hours=1))
    response = setup.patch({"titulo": "Nuevo título"})
    assert response.status_code == 409 and "finalizó" in response.json()["detail"]


# -- avisos ----------------------------------------------------------------------------------------


def test_changing_the_date_warns_every_applicant_who_is_not_rejected_once():
    setup = Edit(cupos=10)
    aceptada = setup.applied()
    pendiente = setup.applied()
    preseleccion = setup.applied()
    rechazada = setup.applied()
    setup.accept(aceptada[2]["id"])
    setup.review(preseleccion[2]["id"], {"estado": "PRESELECCIONADO"})
    setup.review(rechazada[2]["id"], {"estado": "RECHAZADO"})
    before = len(setup.repository.outbox)
    nueva = en_dias(15, 18)
    assert setup.patch({"fecha_prueba": nueva}).status_code == 200
    nuevos = setup.repository.outbox[before:]
    assert {e.user_id for e in nuevos} == {aceptada[0], pendiente[0], preseleccion[0]}
    assert len(nuevos) == 3
    aviso = nuevos[0]
    assert aviso.kind == "convocatoria" and aviso.data["cambios"] == ["fecha"]
    assert aviso.data["convocatoriaId"] == setup.call["id"] and aviso.data["screen"] == "convocatoria"
    assert "18:00" in aviso.body and "Club Ñuñoa" in aviso.body


def test_changing_the_place_warns():
    setup = Edit()
    athlete = setup.applied()
    assert setup.patch({"direccion": "Av. Grecia 2999"}).status_code == 200
    assert [e.user_id for e in setup.avisos()] == [athlete[0]]
    assert setup.avisos()[0].data["cambios"] == ["lugar"] and "Av. Grecia 2999" in setup.avisos()[0].body
    assert setup.patch({"comuna": "Macul"}).status_code == 200
    assert len(setup.avisos()) == 2


def test_date_and_place_together_send_one_notice_per_applicant():
    setup = Edit(cupos=5)
    first, second = setup.applied(), setup.applied()
    assert setup.patch({"fecha_prueba": en_dias(12), "direccion": "Otra calle 55", "comuna": "Macul"}).status_code == 200
    assert len(setup.avisos()) == 2
    assert {e.user_id for e in setup.avisos()} == {first[0], second[0]}
    assert setup.avisos()[0].data["cambios"] == ["fecha", "lugar"]


def test_minor_changes_warn_nobody():
    setup = Edit()
    setup.applied()
    minor = {"descripcion": "Nueva descripción", "titulo": "Otro título", "cupos": 30, "categoria_edad": "Sub-18",
             "edad_minima": 16, "chat_postulantes_habilitado": False}
    assert setup.patch(minor).status_code == 200
    assert setup.avisos() == []


def test_requisitos_and_sport_are_important_too():
    setup = Edit()
    setup.applied()
    assert setup.patch({"requisitos": ["Cédula de identidad"]}).status_code == 200
    assert setup.avisos()[-1].data["cambios"] == ["requisitos"]


def test_sending_the_same_values_again_changes_nothing_and_warns_nobody():
    setup = Edit()
    setup.applied()
    body = {"fecha_prueba": en_dias(15, 18), "direccion": "Av. Nueva 77"}
    assert setup.patch(body).status_code == 200
    assert len(setup.avisos()) == 1 and len(setup.repository.historial) == 1
    assert setup.patch(body).status_code == 200                      # the identical request again
    assert setup.patch({"direccion": "Av. Nueva 77"}).status_code == 200
    assert len(setup.avisos()) == 1 and len(setup.repository.historial) == 1


def test_the_same_instant_in_another_offset_is_not_a_change():
    setup = Edit()
    setup.applied()
    same_instant = datetime.fromisoformat(setup.call["fecha_prueba"]).astimezone(
        __import__("datetime").timezone.utc).isoformat()
    assert setup.patch({"fecha_prueba": same_instant}).status_code == 200
    assert setup.avisos() == [] and setup.repository.historial == []


def test_no_applicants_means_no_notices_but_the_edit_is_saved():
    setup = Edit()
    assert setup.patch({"direccion": "Av. Nueva 77"}).status_code == 200
    assert setup.avisos() == [] and len(setup.repository.historial) == 1


def test_a_failing_notice_does_not_lose_the_edit_or_answer_an_error(caplog):
    setup = Edit()
    setup.applied()
    setup.repository.fail_outbox = True
    with caplog.at_level(logging.ERROR):
        response = setup.patch({"direccion": "Av. Nueva 77"})
    assert response.status_code == 200, response.text
    assert response.json()["direccion"] == "Av. Nueva 77"
    assert setup.client.get(f"{CALLS_PATH}/{setup.call['id']}").json()["direccion"] == "Av. Nueva 77"
    assert setup.avisos() == []
    assert any("notice could not be written" in r.getMessage() for r in caplog.records)
    assert setup.repository.historial[-1]["avisados"] == 0


# -- historial -------------------------------------------------------------------------------------


def test_the_edit_is_recorded_with_who_what_and_when():
    setup = Edit()
    setup.applied()
    before = datetime.fromisoformat(setup.call["fecha_prueba"])
    assert setup.patch({"direccion": "Av. Nueva 77", "titulo": "Otro título"}).status_code == 200
    (entry,) = setup.repository.historial
    assert str(entry["convocatoria_id"]) == setup.call["id"]
    assert entry["usuario_id"] is not None and entry["fecha"] is not None
    assert entry["cambios"] == {
        "direccion": {"antes": "Av. Grecia 2001", "despues": "Av. Nueva 77"},
        "titulo": {"antes": "Prueba Sub-17", "despues": "Otro título"},
    }
    assert entry["avisados"] == 1
    assert before is not None


def test_a_refused_edit_leaves_no_history():
    setup = Edit(cupos=5)
    setup.accept(setup.applied()[2]["id"])
    assert setup.patch({"cupos": 0}).status_code == 400
    assert setup.patch({"cupos": 1, "direccion": "Av. Nueva 77"}).status_code == 200
    assert len(setup.repository.historial) == 1


def test_after_lowering_the_cupos_accepting_one_more_cannot_exceed_them():
    setup = Edit(cupos=5)
    first, second, third = setup.applied(), setup.applied(), setup.applied()
    setup.accept(first[2]["id"])
    assert setup.patch({"cupos": 1}).status_code == 200            # accepted (1) == cupos (1): allowed
    refused = setup.review(second[2]["id"], {"estado": "ACEPTADO"})
    assert refused.status_code == 409
    assert setup.review(third[2]["id"], {"estado": "PRESELECCIONADO"}).status_code == 200
    accepted = setup.candidates(estado="ACEPTADO").json()
    assert accepted["total"] == 1
