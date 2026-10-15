from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from test_club_admin import token  # also sets JWT_SECRET before the app is imported
from test_postulaciones import CLUBS_PATH
from test_revision_postulaciones import Review

FEED_PATH = "/api/v1/clubs/notices/me"


class Notices(Review):
    """A club whose convocatoria has one athlete per estado:
    pending, preselected, accepted and rejected."""

    def __init__(self) -> None:
        super().__init__(cupos=10)
        self.pending = self.applied()
        self.preselected = self.applied()
        self.accepted = self.applied()
        self.rejected = self.applied()
        self.review(self.preselected[2]["id"], {"estado": "PRESELECCIONADO"})
        self.review(self.accepted[2]["id"], {"estado": "ACEPTADO"})
        self.review(self.rejected[2]["id"], {"estado": "RECHAZADO"})
        self.repository.outbox.clear()  # only the notices' notifications from here on

    def publish_notice(self, headers: dict | None = None, club_id: object = None, **fields: object):
        body = {
            "titulo": "Cambio de horario",
            "contenido": "La prueba del sábado empieza a las 9:00 en la cancha 2.",
            "audiencia": "TODOS",
            **fields,
        }
        return self.client.post(
            f"{CLUBS_PATH}/{club_id or self.club_id}/notices",
            headers=self.owner_headers if headers is None else headers,
            json={key: value for key, value in body.items() if value is not ...},
        )

    def feed(self, headers: dict, **params: object):
        return self.client.get(FEED_PATH, headers=headers, params=params)

    def recipients(self) -> set[UUID]:
        return {event.user_id for event in self.repository.outbox}


def athlete_id(applied: tuple) -> UUID:
    return applied[0]


# -- publishing and audience --------------------------------------------------------------


@pytest.mark.parametrize(
    ("audiencia", "who"),
    [
        ("POSTULANTES", {"pending", "preselected"}),
        ("MIEMBROS", {"accepted"}),
        ("TODOS", {"pending", "preselected", "accepted"}),
    ],
)
def test_notice_reaches_the_audience_and_notifies_each_recipient(audiencia, who):
    setup = Notices()
    response = setup.publish_notice(audiencia=audiencia, prioridad="URGENTE")
    assert response.status_code == 201, response.text
    notice = response.json()
    assert notice["audiencia"] == audiencia and notice["prioridad"] == "URGENTE"
    assert notice["titulo"] == "Cambio de horario"
    assert notice["club"]["id"] == str(setup.club_id) and notice["club"]["nombre"] == "Club Ñuñoa"
    assert notice["destinatarios"] == len(who)
    expected = {athlete_id(getattr(setup, name)) for name in who}
    assert setup.recipients() == expected
    assert len(setup.repository.outbox) == len(who)  # one notification per athlete


def test_rejected_athletes_never_receive_notices():
    setup = Notices()
    setup.publish_notice(audiencia="TODOS")
    rejected = athlete_id(setup.rejected)
    assert rejected not in setup.recipients()
    assert setup.feed(setup.rejected[1]).json()["total"] == 0


def test_notification_text_and_destination():
    setup = Notices()
    notice = setup.publish_notice(audiencia="MIEMBROS", titulo="Bienvenidos", contenido="Nos vemos el sábado.").json()
    [event] = setup.repository.outbox
    assert event.user_id == athlete_id(setup.accepted)
    assert (event.kind, event.title) == ("comunicado", "Bienvenidos")
    assert event.body == "Club Ñuñoa: Nos vemos el sábado."
    assert event.data == {
        "screen": "comunicado",
        "comunicadoId": notice["id"],
        "clubId": str(setup.club_id),
        "prioridad": "NORMAL",
    }


def test_urgent_notification_is_marked_and_long_text_is_previewed():
    setup = Notices()
    setup.publish_notice(
        audiencia="MIEMBROS", prioridad="URGENTE", titulo="Suspendida",
        contenido="Se suspende la sesión por lluvia. " * 20,
    )
    [event] = setup.repository.outbox
    assert event.title == "Urgente: Suspendida"
    assert event.body.startswith("Club Ñuñoa: Se suspende la sesión por lluvia.")
    assert event.body.endswith("…") and len(event.body) <= len("Club Ñuñoa: ") + 120


def test_notice_without_recipients_is_published_and_notifies_nobody():
    setup = Notices()
    other = Notices()  # a club where nobody was accepted yet
    other.review(other.accepted[2]["id"], {"estado": "RECHAZADO"})
    other.repository.outbox.clear()
    response = other.publish_notice(audiencia="MIEMBROS")
    assert response.status_code == 201 and response.json()["destinatarios"] == 0
    assert other.repository.outbox == []
    assert setup.repository.outbox == []


def test_recipients_are_fixed_when_the_notice_is_published():
    setup = Notices()
    setup.publish_notice(audiencia="POSTULANTES")
    setup.review(setup.pending[2]["id"], {"estado": "ACEPTADO"})  # moves out of POSTULANTES afterwards
    assert setup.feed(setup.pending[1]).json()["total"] == 1  # still sees what it was sent
    late = setup.applied()
    assert setup.feed(late[1]).json()["total"] == 0  # joined after the notice


def test_priority_defaults_to_normal():
    setup = Notices()
    assert setup.publish_notice(prioridad=...).json()["prioridad"] == "NORMAL"


@pytest.mark.parametrize(
    "fields",
    [
        {"titulo": ...},
        {"contenido": ...},
        {"audiencia": ...},
        {"titulo": "Hi"},
        {"titulo": "x" * 121},
        {"contenido": "cort"},
        {"contenido": "x" * 2001},
        {"audiencia": "SEGUIDORES"},
        {"audiencia": "todos"},
        {"prioridad": "ALTA"},
        {"prioridad": "urgente"},
        {"fecha_creacion": "2020-01-01T00:00:00Z"},
        {"destinatarios": 500},
    ],
)
def test_invalid_notices_are_rejected_with_400_and_send_nothing(fields):
    setup = Notices()
    assert setup.publish_notice(**fields).status_code == 400
    assert setup.repository.outbox == []
    assert setup.client.get(f"{CLUBS_PATH}/{setup.club_id}/notices", headers=setup.owner_headers).json()["total"] == 0


# -- permissions ------------------------------------------------------------------------


def test_publishing_requires_a_token():
    setup = Notices()
    body = {"titulo": "Aviso", "contenido": "Contenido del aviso.", "audiencia": "TODOS"}
    assert setup.client.post(f"{CLUBS_PATH}/{setup.club_id}/notices", json=body).status_code == 401
    assert setup.client.get(f"{CLUBS_PATH}/{setup.club_id}/notices").status_code == 401
    assert setup.client.get(FEED_PATH).status_code == 401


@pytest.mark.parametrize(
    ("who", "detail"),
    [
        ("athlete", "Solo un representante de club puede administrar clubes."),
        ("admin", "Solo un representante de club puede administrar clubes."),
        ("another club", "No administras este club."),
    ],
)
def test_only_the_owning_representative_publishes_and_lists(who, detail):
    setup = Notices()
    if who == "athlete":
        headers = setup.pending[1]
    elif who == "admin":
        headers = token(uuid4(), "admin")
    else:
        headers = token(uuid4())
        assert setup.client.post(CLUBS_PATH, headers=headers, json={"nombre": "Otro club"}).status_code == 201
    published = setup.publish_notice(headers)
    listed = setup.client.get(f"{CLUBS_PATH}/{setup.club_id}/notices", headers=headers)
    assert (published.status_code, listed.status_code) == (403, 403)
    assert published.json() == listed.json() == {"detail": detail}
    assert setup.repository.outbox == []


def test_unknown_club_is_404_and_malformed_id_is_400():
    setup = Notices()
    assert setup.publish_notice(club_id=uuid4()).status_code == 404
    assert setup.client.get(f"{CLUBS_PATH}/{uuid4()}/notices", headers=setup.owner_headers).status_code == 404
    assert setup.publish_notice(club_id="no-es-uuid").status_code == 400


def test_inactive_club_cannot_publish_but_can_still_list():
    setup = Notices()
    setup.publish_notice()
    setup.repository._clubs[setup.club_id].is_active = False  # noqa: SLF001 - no endpoint deactivates clubs
    response = setup.publish_notice()
    assert response.status_code == 409
    assert response.json() == {"detail": "El club está inactivo: no puede publicar comunicados."}
    listed = setup.client.get(f"{CLUBS_PATH}/{setup.club_id}/notices", headers=setup.owner_headers)
    assert listed.json()["total"] == 1


# -- athlete feed ---------------------------------------------------------------------------


def test_athlete_reads_only_notices_addressed_to_them_newest_first():
    setup = Notices()
    first = setup.publish_notice(audiencia="TODOS", titulo="Primero").json()
    second = setup.publish_notice(audiencia="MIEMBROS", titulo="Segundo").json()
    third = setup.publish_notice(audiencia="POSTULANTES", titulo="Tercero").json()

    accepted_feed = setup.feed(setup.accepted[1]).json()
    assert [n["id"] for n in accepted_feed["items"]] == [second["id"], first["id"]]
    pending_feed = setup.feed(setup.pending[1]).json()
    assert [n["id"] for n in pending_feed["items"]] == [third["id"], first["id"]]

    item = accepted_feed["items"][0]
    assert item["club"] == {"id": str(setup.club_id), "nombre": "Club Ñuñoa", "logo": None}
    assert item["contenido"].startswith("La prueba del sábado")
    assert "destinatarios" not in item  # the audience size is for the club


def test_feed_filters_and_pagination():
    setup = Notices()
    normal = setup.publish_notice(titulo="Normal uno").json()
    urgent = setup.publish_notice(titulo="Urgente uno", prioridad="URGENTE").json()
    setup.publish_notice(titulo="Normal dos")
    headers = setup.pending[1]
    assert [n["id"] for n in setup.feed(headers, prioridad="URGENTE").json()["items"]] == [urgent["id"]]
    assert setup.feed(headers, club_id=str(uuid4())).json()["total"] == 0
    assert setup.feed(headers, club_id=str(setup.club_id)).json()["total"] == 3
    paged = setup.feed(headers, page=2, page_size=2).json()
    assert paged["total"] == 3 and [n["id"] for n in paged["items"]] == [normal["id"]]


@pytest.mark.parametrize(
    "params", [{"prioridad": "ALTA"}, {"club_id": "club"}, {"page": 0}, {"page_size": 51}, {"audiencia": "TODOS"}]
)
def test_invalid_feed_filters_are_400(params):
    setup = Notices()
    assert setup.feed(setup.pending[1], **params).status_code == 400


@pytest.mark.parametrize("role", ["club_admin", "admin"])
def test_only_athletes_have_a_feed(role):
    setup = Notices()
    response = setup.feed(token(uuid4(), role))
    assert response.status_code == 403
    assert response.json() == {"detail": "Solo los deportistas pueden postular a convocatorias."}


def test_athlete_without_notices_gets_an_empty_feed():
    setup = Notices()
    _, headers = setup.athlete()
    assert setup.feed(headers).json() == {"items": [], "total": 0, "page": 1, "page_size": 20}


def test_club_lists_its_notices_with_audience_size_and_filters():
    setup = Notices()
    setup.publish_notice(audiencia="TODOS", titulo="Para todos")
    setup.publish_notice(audiencia="MIEMBROS", titulo="Para miembros", prioridad="URGENTE")
    path = f"{CLUBS_PATH}/{setup.club_id}/notices"
    page = setup.client.get(path, headers=setup.owner_headers).json()
    assert [(n["titulo"], n["destinatarios"]) for n in page["items"]] == [("Para miembros", 1), ("Para todos", 3)]
    only = setup.client.get(path, headers=setup.owner_headers, params={"audiencia": "TODOS"}).json()
    assert [n["titulo"] for n in only["items"]] == ["Para todos"]
    urgent = setup.client.get(path, headers=setup.owner_headers, params={"prioridad": "URGENTE"}).json()
    assert [n["titulo"] for n in urgent["items"]] == ["Para miembros"]


def test_a_club_does_not_see_the_notices_of_another():
    setup = Notices()
    setup.publish_notice(titulo="De este club")
    other = Notices()
    listed = other.client.get(f"{CLUBS_PATH}/{other.club_id}/notices", headers=other.owner_headers).json()
    assert listed["total"] == 0
