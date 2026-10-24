"""G3: consultas only with active postulantes of the club."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from test_club_admin import token  # also sets JWT_SECRET before the app is imported
from test_consultas import CLUBS_PATH, Consultas, postular

from app.repositories.base import HiloEliminado

NOT_POSTULANTE = {"detail": "Solo puedes escribir a clubes a los que postulaste."}
REVIEW_PATH = "/api/v1/clubs/announcements/applications"


class Postulantes(Consultas):
    def start(self, usuario_id: object, texto: str = "Hola, ¿sigues interesada?",
              headers: dict | None = None, club_id: object = None, client_message_id: object = None,
              **extra: object):
        return self.client.post(
            f"{CLUBS_PATH}/{club_id or self.club_id}/consultas/postulantes/{usuario_id}/messages",
            headers=headers or self.principal_headers,
            json={"texto": texto, "client_message_id": str(client_message_id or uuid4()), **extra},
        )

    def review(self, postulacion_id: object, estado: str, headers: dict | None = None):
        return self.client.patch(
            f"{REVIEW_PATH}/{postulacion_id}",
            headers=headers or self.principal_headers, json={"estado": estado},
        )


# -- the athlete only takes part while being an active postulante -------------------------


def test_athlete_who_never_applied_cannot_read_nor_write():
    setup = Postulantes()
    stranger_id = uuid4()
    stranger = token(stranger_id, "player")
    write = setup.write(headers=stranger)
    assert write.status_code == 403 and write.json() == NOT_POSTULANTE
    read = setup.my_messages(headers=stranger)
    assert read.status_code == 403 and read.json() == NOT_POSTULANTE
    mark = setup.client.post(
        f"{CLUBS_PATH}/{setup.club_id}/consultas/me/read", headers=stranger, json={"hasta_id": 1}
    )
    assert mark.status_code == 403
    assert setup.my_threads(stranger).json() == []
    assert setup.repository.outbox == []
    # Without a thread created on the way.
    assert setup.repository.get_hilo_deportista(setup.club_id, stranger_id) is None


def test_missing_club_is_still_404_before_the_postulante_check():
    setup = Postulantes()
    assert setup.write(club_id=uuid4()).status_code == 404
    assert setup.my_messages(club_id=uuid4()).status_code == 404


def test_an_application_to_another_club_does_not_count():
    setup = Postulantes()
    other_id, _ = setup.other_club()
    athlete_id = uuid4()
    postular(setup.repository, other_id, athlete_id)
    assert setup.write(headers=token(athlete_id, "player")).status_code == 403
    assert setup.write(headers=token(athlete_id, "player"), club_id=other_id).status_code == 201


@pytest.mark.parametrize("estado", ["PENDIENTE", "PRESELECCIONADO", "ACEPTADO"])
def test_every_state_but_rechazado_is_an_active_postulante(estado):
    setup = Postulantes()
    athlete_id = uuid4()
    setup.postular(athlete_id, estado=estado)
    assert setup.write(headers=token(athlete_id, "player")).status_code == 201


def test_only_rejected_applications_are_not_enough():
    setup = Postulantes()
    athlete_id = uuid4()
    setup.postular(athlete_id, estado="RECHAZADO")
    assert setup.write(headers=token(athlete_id, "player")).json() == NOT_POSTULANTE


# -- the club writes first -----------------------------------------------------------------------


def test_club_starts_the_conversation_with_a_postulante():
    setup = Postulantes()
    athlete_id = uuid4()
    setup.postular(athlete_id, nombre="Bea", apellido_inicial="R.")
    response = setup.start(athlete_id, "Te esperamos el sábado.", headers=setup.admin_headers)
    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == {"hilo_id", "mensaje"} and UUID(body["hilo_id"])
    assert body["mensaje"]["de"] == "club" and body["mensaje"]["propio"] is True
    assert body["mensaje"]["texto"] == "Te esperamos el sábado."

    # The name is the one the club sees in the postulación, not Ms_Users'.
    inbox = setup.inbox().json()
    hilo = next(h for h in inbox if h["hilo_id"] == body["hilo_id"])
    assert hilo["deportista_id"] == str(athlete_id) and hilo["deportista_nombre"] == "Bea R."

    athlete = token(athlete_id, "player")
    threads = setup.my_threads(athlete).json()
    assert [t["hilo_id"] for t in threads] == [body["hilo_id"]] and threads[0]["no_leidos"] == 1
    mine = setup.my_messages(headers=athlete).json()
    assert mine["hilo_id"] == body["hilo_id"]
    assert mine["mensajes"] == [{**body["mensaje"], "propio": False}]

    # Only the athlete is notified, without the text.
    assert [e.user_id for e in setup.repository.outbox] == [athlete_id]
    event = setup.repository.outbox[0]
    assert "sábado" not in event.title + event.body + str(event.data)
    assert event.kind == "consulta"
    assert event.data == {"screen": "consulta", "clubId": str(setup.club_id), "hiloId": body["hilo_id"]}


def test_club_start_is_idempotent_and_reuses_the_existing_thread():
    setup = Postulantes()
    setup.write("Hola club")
    hilo_id = setup.hilo_id()
    client_message_id = uuid4()
    first = setup.start(setup.athlete, client_message_id=client_message_id)
    retry = setup.start(setup.athlete, client_message_id=client_message_id)
    assert first.status_code == retry.status_code == 201
    assert first.json() == retry.json() and first.json()["hilo_id"] == hilo_id
    # The thread keeps the name it was created with (the athlete wrote first).
    assert setup.inbox().json()[0]["deportista_nombre"] == "Ana Torres"
    assert len(setup.my_messages().json()["mensajes"]) == 2


def test_club_cannot_start_with_someone_who_is_not_an_active_postulante():
    setup = Postulantes()
    rejected = uuid4()
    setup.postular(rejected, estado="RECHAZADO")
    for usuario_id in (uuid4(), rejected):
        response = setup.start(usuario_id)
        assert response.status_code == 404
        assert setup.repository.get_hilo_deportista(setup.club_id, usuario_id) is None
    assert setup.repository.outbox == []


def test_the_name_from_the_client_is_never_accepted():
    setup = Postulantes()
    response = setup.start(setup.athlete, deportista_nombre="Otro nombre")
    assert response.status_code == 400


def test_another_club_or_an_athlete_cannot_start():
    setup = Postulantes()
    _, other_headers = setup.other_club()
    assert setup.start(setup.athlete, headers=other_headers).status_code == 403
    assert setup.start(setup.athlete, headers=setup.athlete_headers).status_code == 403
    assert setup.start(setup.athlete, club_id=uuid4()).status_code == 404
    assert setup.repository.outbox == []


def test_inactive_club_cannot_start():
    setup = Postulantes()
    setup.repository.update_club(setup.club_id, {"is_active": False})
    assert setup.start(setup.athlete).status_code == 409


# -- rejection deletes the thread ------------------------------------------------------------------


def test_rejection_without_another_active_application_deletes_the_thread():
    setup = Postulantes()
    setup.write("Mi consulta")
    hilo_id = setup.hilo_id()
    setup.reply(hilo_id, "Respuesta")
    setup.repository.outbox.clear()

    assert setup.review(setup.postulacion, "RECHAZADO").status_code == 200
    # The rejection notice is still sent.
    assert [e.kind for e in setup.repository.outbox] == ["convocatoria"]

    assert setup.repository.get_hilo(UUID(hilo_id)) is None
    assert setup.repository.list_mensajes(UUID(hilo_id), after_id=None, before_id=None, limit=100) == []
    assert setup.inbox().json() == []
    assert setup.thread_messages(hilo_id).status_code == 404
    assert setup.reply(hilo_id).status_code == 404
    assert setup.my_threads().json() == []
    assert setup.write().json() == NOT_POSTULANTE
    assert setup.start(setup.athlete).status_code == 404


def test_rejection_keeps_the_thread_while_another_application_is_active():
    setup = Postulantes()
    setup.postular(setup.athlete)  # a second convocatoria of the same club
    setup.write()
    hilo_id = setup.hilo_id()
    setup.review(setup.postulacion, "RECHAZADO")
    assert setup.repository.get_hilo(UUID(hilo_id)) is not None
    assert setup.write("Sigo en la otra").status_code == 201


@pytest.mark.parametrize("estado", ["ACEPTADO", "PRESELECCIONADO"])
def test_accepting_keeps_the_thread_open(estado):
    setup = Postulantes()
    setup.write()
    hilo_id = setup.hilo_id()
    setup.review(setup.postulacion, estado)
    assert setup.inbox().json()[0]["hilo_id"] == hilo_id
    assert setup.write("Gracias").status_code == 201


def test_rejection_in_another_club_does_not_touch_this_thread():
    setup = Postulantes()
    other_id, other_headers = setup.other_club()
    other_postulacion = postular(setup.repository, other_id, setup.athlete)
    setup.write()
    hilo_id = setup.hilo_id()
    assert setup.review(other_postulacion, "RECHAZADO", headers=other_headers).status_code == 200
    assert setup.repository.get_hilo(UUID(hilo_id)) is not None


def test_applying_again_after_a_rejection_starts_from_scratch():
    setup = Postulantes()
    old = setup.write("Antes").json()
    setup.review(setup.postulacion, "RECHAZADO")
    setup.postular(setup.athlete)
    page = setup.my_messages().json()
    assert page == {"hilo_id": None, "mensajes": []}
    new = setup.write("Después").json()
    # Message ids are never reused.
    assert new["id"] > old["id"]
    assert [m["texto"] for m in setup.my_messages().json()["mensajes"]] == ["Después"]


def test_writing_to_a_thread_deleted_meanwhile_is_handled():
    """The thread was read, then a rejection deleted it before the insert."""
    setup = Postulantes()
    setup.write()
    hilo_id = setup.hilo_id()

    def deleted(hilo_id, *args, **kwargs):
        raise HiloEliminado(hilo_id)

    setup.repository.add_mensaje = deleted
    assert setup.write().json() == NOT_POSTULANTE
    assert setup.reply(hilo_id).status_code == 404
    assert setup.start(setup.athlete).status_code == 404
