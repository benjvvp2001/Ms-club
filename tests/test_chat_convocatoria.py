from __future__ import annotations

from uuid import UUID, uuid4

from test_club_admin import nuevo_rut, token  # also sets JWT_SECRET before the app is imported
from test_postulaciones import CALLS_PATH, CLUBS_PATH, MY_PATH, Setup

CHAT_OFF = "El club no permite escribir en el chat de esta convocatoria: solo puedes leer."


class Chat(Setup):
    """A club with a convocatoria, and helpers for the applicants' chat (migration 011)."""

    def __init__(self, **convocatoria: object) -> None:
        super().__init__()
        self.call = self.convocatoria(**convocatoria)

    def apply_to(self, headers: dict, convocatoria_id: object = None) -> dict:
        response = self.apply(convocatoria_id or self.call["id"], headers, {"mensaje": "Hola."})
        assert response.status_code == 201, response.text
        return response.json()

    def write_athlete(self, headers: dict, texto: str = "¿Cuándo es la prueba?"):
        return self.client.post(
            f"{CLUBS_PATH}/{self.club_id}/consultas/me/messages",
            headers=headers, json={"texto": texto, "client_message_id": str(uuid4())},
        )

    def write_club(self, usuario_id: UUID, texto: str = "Mañana a las 10:00."):
        return self.client.post(
            f"{CLUBS_PATH}/{self.club_id}/consultas/postulantes/{usuario_id}/messages",
            headers=self.owner_headers, json={"texto": texto, "client_message_id": str(uuid4())},
        )

    def reply_club(self, hilo_id: str, texto: str = "Gracias."):
        return self.client.post(
            f"{CLUBS_PATH}/{self.club_id}/consultas/{hilo_id}/messages",
            headers=self.owner_headers, json={"texto": texto, "client_message_id": str(uuid4())},
        )

    def read_athlete(self, headers: dict):
        return self.client.get(f"{CLUBS_PATH}/{self.club_id}/consultas/me/messages", headers=headers)

    def set_chat(self, habilitado: object, *, headers: dict | None = None, convocatoria_id: object = None,
                 club_id: object = None):
        return self.client.patch(
            f"{CLUBS_PATH}/{club_id or self.club_id}/announcements/{convocatoria_id or self.call['id']}/chat",
            headers=headers or self.owner_headers,
            json={"chat_postulantes_habilitado": habilitado},
        )

    def review(self, postulacion_id: object, estado: str):
        return self.client.patch(
            f"{CALLS_PATH}/applications/{postulacion_id}", headers=self.owner_headers, json={"estado": estado},
        )


# -- the switch on the convocatoria ----------------------------------------------------------


def test_chat_is_enabled_by_default_and_shown_in_the_convocatoria():
    setup = Chat()
    assert setup.call["chat_postulantes_habilitado"] is True
    assert setup.client.get(f"{CALLS_PATH}/{setup.call['id']}").json()["chat_postulantes_habilitado"] is True
    _, headers = setup.athlete()
    setup.apply_to(headers)
    mine = setup.client.get(MY_PATH, headers=headers).json()
    assert mine["items"][0]["convocatoria"]["chat_postulantes_habilitado"] is True


def test_club_can_publish_with_the_chat_off():
    setup = Chat(chat_postulantes_habilitado=False)
    assert setup.call["chat_postulantes_habilitado"] is False
    assert setup.client.get(f"{CALLS_PATH}/{setup.call['id']}").json()["chat_postulantes_habilitado"] is False


def test_only_the_owning_club_changes_the_switch():
    setup = Chat()
    _, athlete_headers = setup.athlete()
    setup.apply_to(athlete_headers)
    other = token(uuid4())
    assert setup.client.post(CLUBS_PATH, headers=other, json={"nombre": "Club Vecino", "rut": nuevo_rut()}).status_code == 201
    assert setup.set_chat(False, headers=athlete_headers).status_code == 403
    assert setup.set_chat(False, headers=other).status_code == 403
    anonymous = setup.client.patch(
        f"{CLUBS_PATH}/{setup.club_id}/announcements/{setup.call['id']}/chat",
        json={"chat_postulantes_habilitado": False},
    )
    assert anonymous.status_code == 401
    assert setup.client.get(f"{CALLS_PATH}/{setup.call['id']}").json()["chat_postulantes_habilitado"] is True


def test_switch_through_another_club_or_unknown_convocatoria():
    setup = Chat()
    other = token(uuid4())
    other_club = setup.client.post(CLUBS_PATH, headers=other, json={"nombre": "Club Vecino", "rut": nuevo_rut()}).json()["id"]
    # The owner of this club does not manage the other one.
    assert setup.set_chat(False, club_id=other_club).status_code == 403
    response = setup.set_chat(False, convocatoria_id=uuid4())
    assert response.status_code == 404 and response.json() == {"detail": "Convocatoria no encontrada."}


def test_switch_rejects_a_value_that_is_not_a_boolean():
    setup = Chat()
    assert setup.set_chat("quizas").status_code == 400
    assert setup.set_chat(None).status_code == 400
    assert setup.client.get(f"{CALLS_PATH}/{setup.call['id']}").json()["chat_postulantes_habilitado"] is True


# -- applicants' writes ----------------------------------------------------------------------


def test_with_the_chat_off_the_applicant_reads_but_cannot_write():
    setup = Chat(chat_postulantes_habilitado=False)
    athlete_id, headers = setup.athlete()
    setup.apply_to(headers)
    assert setup.write_club(athlete_id).status_code == 201
    sent = len(setup.repository.outbox)

    read = setup.read_athlete(headers)
    assert read.status_code == 200 and len(read.json()["mensajes"]) == 1

    blocked = setup.write_athlete(headers)
    assert blocked.status_code == 403
    assert blocked.json() == {"detail": CHAT_OFF}
    assert len(setup.read_athlete(headers).json()["mensajes"]) == 1  # nothing was stored
    assert len(setup.repository.outbox) == sent  # and the club was not notified


def test_the_club_always_writes_even_with_the_chat_off():
    setup = Chat(chat_postulantes_habilitado=False)
    athlete_id, headers = setup.athlete()
    setup.apply_to(headers)
    opening = setup.write_club(athlete_id)
    assert setup.reply_club(opening.json()["hilo_id"]).status_code == 201
    assert setup.write_club(athlete_id).status_code == 201
    assert len(setup.read_athlete(headers).json()["mensajes"]) == 3


def test_switching_the_chat_back_on_allows_writing_again():
    setup = Chat(chat_postulantes_habilitado=False)
    _, headers = setup.athlete()
    setup.apply_to(headers)
    assert setup.write_athlete(headers).status_code == 403
    assert setup.set_chat(True).json()["chat_postulantes_habilitado"] is True
    assert setup.write_athlete(headers).status_code == 201


def test_switching_off_stops_an_applicant_who_was_already_talking():
    setup = Chat()
    _, headers = setup.athlete()
    setup.apply_to(headers)
    assert setup.write_athlete(headers).status_code == 201
    assert setup.set_chat(False).status_code == 200
    assert setup.write_athlete(headers).status_code == 403
    hilo_id = setup.client.get(f"{CLUBS_PATH}/{setup.club_id}/consultas", headers=setup.owner_headers).json()[0]["hilo_id"]
    assert setup.reply_club(hilo_id).status_code == 201


def test_an_applicant_with_one_switched_off_convocatoria_cannot_write_to_the_club():
    setup = Chat()
    _, headers = setup.athlete()
    setup.apply_to(headers)  # the convocatoria with the chat on
    closed = setup.convocatoria(chat_postulantes_habilitado=False)
    setup.apply_to(headers, closed["id"])  # and one with it off
    assert setup.write_athlete(headers).status_code == 403


def test_rejecting_the_switched_off_application_restores_writing():
    setup = Chat()
    _, headers = setup.athlete()
    setup.apply_to(headers)
    closed = setup.convocatoria(chat_postulantes_habilitado=False)
    off = setup.apply_to(headers, closed["id"])
    assert setup.write_athlete(headers).status_code == 403
    assert setup.review(off["id"], "RECHAZADO").status_code == 200
    assert setup.write_athlete(headers).status_code == 201


def test_athlete_without_application_still_cannot_write_to_the_club():
    setup = Chat()
    _, outsider = setup.athlete()
    response = setup.write_athlete(outsider)
    assert response.status_code == 403
    assert response.json() == {"detail": "Solo puedes escribir a clubes a los que postulaste."}
