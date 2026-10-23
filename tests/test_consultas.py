from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from test_administradores import Admins
from test_club_admin import token  # also sets JWT_SECRET before the app is imported

from app.repositories.base import StoredClub

CLUBS_PATH = "/api/v1/clubs"
MY_THREADS = "/api/v1/clubs/consultas/me"


class Consultas(Admins):
    """A club with a PRINCIPAL and one ADMIN, and an athlete."""

    def __init__(self) -> None:
        super().__init__()
        self.admin, self.admin_headers = self.new_admin()
        self.athlete = uuid4()
        self.names.names[self.athlete] = "Ana Torres"
        self.athlete_headers = token(self.athlete, "player")

    # athlete side
    def write(self, texto: str = "¿Cuándo son las pruebas Sub-17?", headers: dict | None = None,
              club_id: object = None, client_message_id: object = None):
        return self.client.post(
            f"{CLUBS_PATH}/{club_id or self.club_id}/consultas/me/messages",
            headers=headers or self.athlete_headers,
            json={"texto": texto, "client_message_id": str(client_message_id or uuid4())},
        )

    def my_messages(self, headers: dict | None = None, club_id: object = None, **params: object):
        return self.client.get(
            f"{CLUBS_PATH}/{club_id or self.club_id}/consultas/me/messages",
            headers=headers or self.athlete_headers, params=params,
        )

    def my_threads(self, headers: dict | None = None):
        return self.client.get(MY_THREADS, headers=headers or self.athlete_headers)

    # club side
    def inbox(self, headers: dict | None = None, club_id: object = None):
        return self.client.get(
            f"{CLUBS_PATH}/{club_id or self.club_id}/consultas", headers=headers or self.principal_headers
        )

    def reply(self, hilo_id: str, texto: str = "El sábado a las 9:00.", headers: dict | None = None,
              club_id: object = None, client_message_id: object = None):
        return self.client.post(
            f"{CLUBS_PATH}/{club_id or self.club_id}/consultas/{hilo_id}/messages",
            headers=headers or self.principal_headers,
            json={"texto": texto, "client_message_id": str(client_message_id or uuid4())},
        )

    def thread_messages(self, hilo_id: str, headers: dict | None = None, club_id: object = None, **params):
        return self.client.get(
            f"{CLUBS_PATH}/{club_id or self.club_id}/consultas/{hilo_id}/messages",
            headers=headers or self.principal_headers, params=params,
        )

    def hilo_id(self) -> str:
        return self.my_messages().json()["hilo_id"]


# -- athlete writes --------------------------------------------------------------------------


def test_athlete_first_message_creates_the_thread():
    setup = Consultas()
    assert setup.my_messages().json() == {"hilo_id": None, "mensajes": []}
    response = setup.write()
    assert response.status_code == 201, response.text
    mensaje = response.json()
    assert mensaje["de"] == "deportista" and mensaje["propio"] is True
    assert mensaje["texto"] == "¿Cuándo son las pruebas Sub-17?"
    assert isinstance(mensaje["id"], int) and mensaje["enviado_en"]
    assert set(mensaje) == {"id", "de", "texto", "enviado_en", "propio"}

    page = setup.my_messages().json()
    assert page["hilo_id"] is not None and page["mensajes"] == [mensaje]

    threads = setup.my_threads().json()
    assert len(threads) == 1
    assert threads[0]["club_id"] == str(setup.club_id) and threads[0]["club_nombre"] == "Club Ñuñoa"
    assert threads[0]["ultimo"] == mensaje and threads[0]["no_leidos"] == 0
    assert set(threads[0]) == {"hilo_id", "club_id", "club_nombre", "club_logo", "ultimo", "no_leidos"}


def test_retrying_with_the_same_client_message_id_returns_the_same_message():
    setup = Consultas()
    client_message_id = uuid4()
    first = setup.write(client_message_id=client_message_id)
    retry = setup.write(client_message_id=client_message_id)
    assert first.status_code == retry.status_code == 201
    assert retry.json() == first.json()
    assert len(setup.my_messages().json()["mensajes"]) == 1
    # Notified once: to each of the two administrators.
    assert len(setup.repository.outbox) == 2


def test_athlete_message_notifies_every_admin_without_the_text():
    setup = Consultas()
    setup.write("Mensaje privado sobre mi lesión")
    events = setup.repository.outbox
    assert {e.user_id for e in events} == {setup.principal, setup.admin}
    for event in events:
        assert event.kind == "consulta"
        assert "lesión" not in event.title + event.body + str(event.data)
        assert event.data == {"screen": "consulta_club", "clubId": str(setup.club_id), "hiloId": setup.hilo_id()}
        assert "Ana Torres" in event.body


def test_athlete_name_falls_back_when_ms_users_fails():
    setup = Consultas()
    setup.names.fail = True
    setup.write()
    assert setup.inbox().json()[0]["deportista_nombre"] == "Deportista"


@pytest.mark.parametrize(
    "body",
    [
        {"texto": "", "client_message_id": str(uuid4())},
        {"texto": "   ", "client_message_id": str(uuid4())},
        {"texto": "x" * 1001, "client_message_id": str(uuid4())},
        {"texto": "Hola"},
        {"texto": "Hola", "client_message_id": "no-es-uuid"},
        {"texto": "Hola", "client_message_id": str(uuid4()), "autor_id": str(uuid4())},
        {"texto": "Hola", "client_message_id": str(uuid4()), "de": "club"},
    ],
)
def test_invalid_messages_are_rejected(body):
    setup = Consultas()
    response = setup.client.post(
        f"{CLUBS_PATH}/{setup.club_id}/consultas/me/messages", headers=setup.athlete_headers, json=body
    )
    assert response.status_code == 400
    assert setup.repository.outbox == []


def test_a_message_of_1000_characters_is_accepted():
    setup = Consultas()
    assert setup.write("x" * 1000).status_code == 201


def test_missing_or_inactive_club_is_404():
    setup = Consultas()
    assert setup.write(club_id=uuid4()).status_code == 404
    inactive = setup.repository.add_club(StoredClub(id=uuid4(), nombre="Cerrado", is_active=False))
    assert setup.write(club_id=inactive.id).status_code == 404
    assert setup.my_messages(club_id=inactive.id).status_code == 404


def test_deactivated_club_disappears_from_the_athletes_list():
    setup = Consultas()
    setup.write()
    setup.repository.update_club(setup.club_id, {"is_active": False})
    assert setup.my_threads().json() == []
    assert setup.write().status_code == 404


# -- club replies ------------------------------------------------------------------------------


def test_club_inbox_and_reply_signed_by_the_club():
    setup = Consultas()
    question = setup.write().json()
    inbox = setup.inbox().json()
    assert len(inbox) == 1
    hilo = inbox[0]
    assert hilo["deportista_id"] == str(setup.athlete) and hilo["deportista_nombre"] == "Ana Torres"
    assert hilo["no_leidos"] == 1 and hilo["ultimo"]["propio"] is False
    assert set(hilo) == {"hilo_id", "deportista_id", "deportista_nombre", "ultimo", "no_leidos"}

    setup.repository.outbox.clear()
    response = setup.reply(hilo["hilo_id"], headers=setup.admin_headers)
    assert response.status_code == 201, response.text
    answer = response.json()
    assert answer["de"] == "club" and answer["propio"] is True

    # Any administrator sees the club's messages as their own.
    page = setup.thread_messages(hilo["hilo_id"]).json()
    assert page["hilo_id"] == hilo["hilo_id"]
    assert [(m["id"], m["de"], m["propio"]) for m in page["mensajes"]] == [
        (question["id"], "deportista", False), (answer["id"], "club", True),
    ]
    # The athlete sees it from the club, not from a person.
    mine = setup.my_messages().json()["mensajes"]
    assert mine[-1] == {**answer, "propio": False}
    assert "autor_id" not in mine[-1]

    # Only the athlete is notified, without the text.
    assert [e.user_id for e in setup.repository.outbox] == [setup.athlete]
    event = setup.repository.outbox[0]
    assert "sábado" not in event.title + event.body
    assert event.data == {"screen": "consulta", "clubId": str(setup.club_id), "hiloId": hilo["hilo_id"]}


def test_club_reply_is_idempotent():
    setup = Consultas()
    setup.write()
    hilo_id = setup.hilo_id()
    setup.repository.outbox.clear()
    client_message_id = uuid4()
    first = setup.reply(hilo_id, client_message_id=client_message_id)
    retry = setup.reply(hilo_id, client_message_id=client_message_id)
    assert retry.status_code == 201 and retry.json() == first.json()
    assert len(setup.repository.outbox) == 1


def test_unread_counts_and_read_marks():
    setup = Consultas()
    setup.write("Uno")
    setup.write("Dos")
    hilo_id = setup.hilo_id()
    assert setup.inbox().json()[0]["no_leidos"] == 2

    last = setup.thread_messages(hilo_id).json()["mensajes"][-1]["id"]
    read = setup.client.post(
        f"{CLUBS_PATH}/{setup.club_id}/consultas/{hilo_id}/read",
        headers=setup.admin_headers, json={"hasta_id": last},
    )
    assert read.status_code == 204
    # Shared inbox: read by one administrator, read for all.
    assert setup.inbox().json()[0]["no_leidos"] == 0

    first = setup.reply(hilo_id, "R1").json()
    setup.reply(hilo_id, "R2")
    assert setup.my_threads().json()[0]["no_leidos"] == 2
    mark = setup.client.post(
        f"{CLUBS_PATH}/{setup.club_id}/consultas/me/read", headers=setup.athlete_headers,
        json={"hasta_id": first["id"]},
    )
    assert mark.status_code == 204
    assert setup.my_threads().json()[0]["no_leidos"] == 1
    # The mark never goes back.
    setup.client.post(
        f"{CLUBS_PATH}/{setup.club_id}/consultas/me/read", headers=setup.athlete_headers, json={"hasta_id": 0}
    )
    assert setup.my_threads().json()[0]["no_leidos"] == 1
    # Nor beyond the last message: a later reply is still unread.
    setup.client.post(
        f"{CLUBS_PATH}/{setup.club_id}/consultas/me/read", headers=setup.athlete_headers,
        json={"hasta_id": 10**9},
    )
    setup.reply(hilo_id, "R3")
    assert setup.my_threads().json()[0]["no_leidos"] == 1


def test_threads_are_ordered_by_last_message():
    setup = Consultas()
    other_athlete = token(uuid4(), "usuario")
    setup.write()
    setup.write(headers=other_athlete)
    first_hilo = setup.hilo_id()
    assert [h["deportista_nombre"] for h in setup.inbox().json()][1] == "Ana Torres"
    setup.reply(first_hilo)
    assert setup.inbox().json()[0]["hilo_id"] == first_hilo


def test_paging_with_after_and_before_id():
    setup = Consultas()
    ids = [setup.write(f"m{i}").json()["id"] for i in range(5)]
    def got(**params):
        response = setup.my_messages(**params)
        assert response.status_code == 200, response.text
        return [m["id"] for m in response.json()["mensajes"]]
    assert got() == ids
    assert got(limit=2) == ids[-2:]
    assert got(after_id=ids[1], limit=2) == ids[2:4]
    assert got(before_id=ids[3], limit=2) == ids[1:3]
    assert got(after_id=ids[0], before_id=ids[4]) == ids[1:4]
    hilo_id = setup.hilo_id()
    assert [m["id"] for m in setup.thread_messages(hilo_id, after_id=ids[3]).json()["mensajes"]] == ids[4:]


@pytest.mark.parametrize(
    "params", [{"limit": 101}, {"limit": 0}, {"after_id": -1}, {"before_id": "x"}, {"after_id": 5, "before_id": 3}]
)
def test_invalid_paging_is_400(params):
    setup = Consultas()
    assert setup.my_messages(**params).status_code == 400


# -- isolation and roles ----------------------------------------------------------------------


def test_another_club_cannot_see_nor_answer_the_thread():
    setup = Consultas()
    setup.write()
    hilo_id = setup.hilo_id()
    other_id, other_headers = setup.other_club()
    attempts = [
        setup.thread_messages(hilo_id, headers=other_headers, club_id=other_id),
        setup.reply(hilo_id, headers=other_headers, club_id=other_id),
        setup.client.post(
            f"{CLUBS_PATH}/{other_id}/consultas/{hilo_id}/read", headers=other_headers, json={"hasta_id": 1}
        ),
    ]
    assert [r.status_code for r in attempts] == [404, 404, 404]
    assert attempts[0].json() == {"detail": "Consulta no encontrada."}
    assert setup.inbox(other_headers, club_id=other_id).json() == []
    # And through the right club path it is not theirs either.
    assert setup.inbox(other_headers).status_code == 403


def test_unknown_thread_is_404():
    setup = Consultas()
    assert setup.thread_messages(str(uuid4())).status_code == 404


def test_athlete_cannot_use_the_club_routes():
    setup = Consultas()
    setup.write()
    hilo_id = setup.hilo_id()
    attempts = [
        setup.inbox(setup.athlete_headers),
        setup.thread_messages(hilo_id, headers=setup.athlete_headers),
        setup.reply(hilo_id, headers=setup.athlete_headers),
    ]
    assert [r.status_code for r in attempts] == [403, 403, 403]


def test_club_admin_cannot_use_the_athlete_routes():
    setup = Consultas()
    assert setup.write(headers=setup.principal_headers).status_code == 403
    assert setup.my_threads(setup.principal_headers).status_code == 403
    assert setup.my_messages(headers=setup.principal_headers).status_code == 403


def test_athletes_only_see_their_own_thread():
    setup = Consultas()
    setup.write("Mía")
    other = token(uuid4(), "player")
    assert setup.my_messages(headers=other).json() == {"hilo_id": None, "mensajes": []}
    assert setup.my_threads(other).json() == []


@pytest.mark.parametrize("path", ["/consultas/me", "/{club}/consultas", "/{club}/consultas/me/messages"])
def test_consulta_routes_require_a_token(path):
    setup = Consultas()
    response = setup.client.get(CLUBS_PATH + path.format(club=setup.club_id))
    assert response.status_code == 401


def test_removed_admin_no_longer_reads_the_inbox():
    setup = Consultas()
    setup.write()
    assert setup.remove(setup.admin, setup.principal_headers).status_code == 204
    assert setup.inbox(setup.admin_headers).status_code == 403
    setup.repository.outbox.clear()
    setup.write("Otra")
    assert {e.user_id for e in setup.repository.outbox} == {setup.principal}


def test_inactive_club_reads_but_cannot_reply():
    setup = Consultas()
    setup.write()
    hilo_id = setup.hilo_id()
    setup.repository.update_club(setup.club_id, {"is_active": False})
    assert setup.inbox().status_code == 200
    assert setup.reply(hilo_id).status_code == 409


def test_hilo_id_is_a_uuid():
    setup = Consultas()
    setup.write()
    assert UUID(setup.hilo_id())
