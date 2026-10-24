"""G4: channels of a club (announcements to groups of members) and their inactivity check."""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from test_club_admin import token  # also sets JWT_SECRET before the app is imported
from test_consultas import CLUBS_PATH, Consultas, count_repository_calls, postular

from app.repositories.base import CanalInactividad
from app.services import canal_inactividad
from app.services.canal_inactividad import InactividadWorker, decidir, revisar

REVIEW_PATH = "/api/v1/clubs/announcements/applications"


class Canales(Consultas):
    """The club of Consultas, with two accepted athletes (Ana T. is the
    Consultas athlete, now ACEPTADO, and Bea R.) and one pending (Caro P.)."""

    def __init__(self) -> None:
        super().__init__()
        self.repository.set_postulacion_estado(self.postulacion, "ACEPTADO")
        self.bea = uuid4()
        self.bea_postulacion = self.postular(self.bea, nombre="Bea", apellido_inicial="R.", estado="ACEPTADO")
        self.bea_headers = token(self.bea, "player")
        self.caro = uuid4()
        self.postular(self.caro, nombre="Caro", apellido_inicial="P.")
        self.caro_headers = token(self.caro, "player")

    # club side
    def club_url(self, canal_id: object = None, suffix: str = "", club_id: object = None) -> str:
        url = f"{CLUBS_PATH}/{club_id or self.club_id}/canales"
        return url + (f"/{canal_id}" if canal_id else "") + suffix

    def create(self, nombre: str = "Plantel Sub-17", headers: dict | None = None, **fields):
        return self.client.post(
            self.club_url(), headers=headers or self.principal_headers, json={"nombre": nombre, **fields}
        )

    def canal(self, **fields) -> dict:
        response = self.create(**fields)
        assert response.status_code == 201, response.text
        return response.json()

    def club_get(self, canal_id: object, suffix: str, headers: dict | None = None, **params):
        return self.client.get(
            self.club_url(canal_id, suffix), headers=headers or self.principal_headers, params=params
        )

    def club_send(self, canal_id: object, texto: str = "Entrenamiento a las 18:00.",
                  headers: dict | None = None, client_message_id: object = None):
        return self.client.post(
            self.club_url(canal_id, "/messages"), headers=headers or self.principal_headers,
            json={"texto": texto, "client_message_id": str(client_message_id or uuid4())},
        )

    def put_member(self, canal_id: object, usuario_id: object, headers: dict | None = None):
        return self.client.put(
            self.club_url(canal_id, f"/miembros/{usuario_id}"), headers=headers or self.principal_headers
        )

    def decide(self, canal_id: object, decision: str, headers: dict | None = None):
        return self.client.post(
            self.club_url(canal_id, "/inactividad"), headers=headers or self.principal_headers,
            json={"decision": decision},
        )

    # athlete side
    def mine(self, headers: dict | None = None):
        return self.client.get(f"{CLUBS_PATH}/canales/me", headers=headers or self.athlete_headers)

    def member_messages(self, canal_id: object, headers: dict | None = None, **params):
        return self.client.get(
            f"{CLUBS_PATH}/canales/{canal_id}/messages", headers=headers or self.athlete_headers,
            params=params,
        )

    def member_send(self, canal_id: object, texto: str = "¡Ahí estaré!", headers: dict | None = None):
        return self.client.post(
            f"{CLUBS_PATH}/canales/{canal_id}/messages", headers=headers or self.athlete_headers,
            json={"texto": texto, "client_message_id": str(uuid4())},
        )

    def mute(self, canal_id: object, silenciado: bool, headers: dict | None = None):
        return self.client.put(
            f"{CLUBS_PATH}/canales/{canal_id}/mute", headers=headers or self.athlete_headers,
            json={"silenciado": silenciado},
        )

    def member_read(self, canal_id: object, hasta_id: int, headers: dict | None = None):
        return self.client.post(
            f"{CLUBS_PATH}/canales/{canal_id}/read", headers=headers or self.athlete_headers,
            json={"hasta_id": hasta_id},
        )


def names(response) -> list[str]:
    assert response.status_code == 200, response.text
    return [m["nombre"] for m in response.json()]


# -- creating and managing channels ------------------------------------------------------------


def test_new_channel_starts_with_the_accepted_athletes():
    setup = Canales()
    response = setup.create("  Plantel   Sub-17 ", headers=setup.admin_headers)
    assert response.status_code == 201, response.text
    canal = response.json()
    assert set(canal) == {
        "id", "club_id", "nombre", "miembros_escriben", "miembros", "ultimo", "no_leidos",
        "aviso_inactividad_en", "mantener_hasta", "fecha_creacion",
    }
    assert canal["nombre"] == "Plantel Sub-17" and canal["club_id"] == str(setup.club_id)
    assert canal["miembros_escriben"] is False and canal["miembros"] == 2
    assert canal["ultimo"] is None and canal["no_leidos"] == 0
    assert canal["aviso_inactividad_en"] is None and canal["mantener_hasta"] is None

    miembros = setup.club_get(canal["id"], "/miembros").json()
    assert [(m["nombre"], m["usuario_id"]) for m in miembros] == [
        ("Ana T.", str(setup.athlete)), ("Bea R.", str(setup.bea)),
    ]
    assert all(m["agregado_en"] for m in miembros)
    # The pending postulante is a candidate, not a member.
    candidatos = setup.club_get(canal["id"], "/candidatos").json()
    assert candidatos == [{"usuario_id": str(setup.caro), "nombre": "Caro P.", "agregado_en": None}]


def test_a_club_has_at_most_20_channels():
    setup = Canales()
    for i in range(20):
        assert setup.create(f"Canal {i}").status_code == 201
    response = setup.create("Uno más")
    assert response.status_code == 409 and "20" in response.json()["detail"]
    # Another club is not affected.
    other_id, other_headers = setup.other_club()
    assert setup.client.post(
        setup.club_url(club_id=other_id), headers=other_headers, json={"nombre": "Propio"}
    ).status_code == 201


@pytest.mark.parametrize(
    "body",
    [{"nombre": ""}, {"nombre": "   "}, {"nombre": "x" * 61}, {}, {"nombre": "A", "club_id": str(uuid4())},
     {"nombre": "A", "miembros_escriben": "quizás"}],
)
def test_invalid_channel_bodies_are_400(body):
    setup = Canales()
    assert setup.client.post(setup.club_url(), headers=setup.principal_headers, json=body).status_code == 400


def test_a_name_of_60_characters_is_accepted():
    setup = Canales()
    assert setup.create("x" * 60).status_code == 201


def test_channels_are_listed_by_latest_activity_and_can_be_edited_and_deleted():
    setup = Canales()
    first = setup.canal(nombre="Primero")
    second = setup.canal(nombre="Segundo")
    assert names(setup.client.get(setup.club_url(), headers=setup.principal_headers)) == ["Segundo", "Primero"]
    setup.club_send(first["id"])
    assert names(setup.client.get(setup.club_url(), headers=setup.admin_headers)) == ["Primero", "Segundo"]

    patched = setup.client.patch(
        setup.club_url(second["id"]), headers=setup.principal_headers,
        json={"nombre": "Apoderados", "miembros_escriben": True},
    )
    assert patched.status_code == 200
    assert patched.json()["nombre"] == "Apoderados" and patched.json()["miembros_escriben"] is True
    assert setup.client.patch(
        setup.club_url(second["id"]), headers=setup.principal_headers, json={}
    ).status_code == 400

    assert setup.client.delete(setup.club_url(first["id"]), headers=setup.principal_headers).status_code == 204
    assert setup.client.delete(setup.club_url(first["id"]), headers=setup.principal_headers).status_code == 404
    assert names(setup.client.get(setup.club_url(), headers=setup.principal_headers)) == ["Apoderados"]
    assert setup.member_messages(first["id"]).status_code == 404


def test_another_club_never_sees_a_channel_and_gets_404():
    setup = Canales()
    canal = setup.canal()
    other_id, other_headers = setup.other_club()
    cid = canal["id"]
    attempts = [
        setup.client.get(setup.club_url(), headers=other_headers),
        setup.create(headers=other_headers),
        setup.client.patch(setup.club_url(cid), headers=other_headers, json={"nombre": "X"}),
        setup.client.delete(setup.club_url(cid), headers=other_headers),
        setup.club_get(cid, "/miembros", headers=other_headers),
        setup.club_get(cid, "/candidatos", headers=other_headers),
        setup.put_member(cid, setup.caro, headers=other_headers),
        setup.client.delete(setup.club_url(cid, f"/miembros/{setup.athlete}"), headers=other_headers),
        setup.club_get(cid, "/messages", headers=other_headers),
        setup.club_send(cid, headers=other_headers),
        setup.client.post(setup.club_url(cid, "/read"), headers=other_headers, json={"hasta_id": 1}),
        setup.decide(cid, "eliminar", headers=other_headers),
    ]
    assert [r.status_code for r in attempts] == [404] * len(attempts)
    # Nor through its own club path.
    own = [
        setup.client.get(setup.club_url(cid, club_id=other_id), headers=other_headers),
        setup.client.delete(setup.club_url(cid, club_id=other_id), headers=other_headers),
        setup.client.get(setup.club_url(cid, "/messages", club_id=other_id), headers=other_headers),
    ]
    assert [r.status_code for r in own] == [405, 404, 404]
    assert setup.club_get(cid, "/miembros").status_code == 200


def test_unknown_club_or_channel_is_404_and_athletes_cannot_use_club_routes():
    setup = Canales()
    canal = setup.canal()
    assert setup.client.get(setup.club_url(club_id=uuid4()), headers=setup.principal_headers).status_code == 404
    assert setup.club_get(uuid4(), "/miembros").status_code == 404
    assert setup.client.get(setup.club_url(), headers=setup.athlete_headers).status_code == 403
    assert setup.club_send(canal["id"], headers=setup.athlete_headers).status_code == 403
    # And a club account cannot use the athlete routes.
    assert setup.mine(setup.principal_headers).status_code == 403


def test_removed_admin_loses_the_channels():
    setup = Canales()
    canal = setup.canal()
    setup.remove(setup.admin, setup.principal_headers)
    assert setup.client.get(setup.club_url(), headers=setup.admin_headers).status_code == 404
    assert setup.club_get(canal["id"], "/messages", headers=setup.admin_headers).status_code == 404


# -- members -------------------------------------------------------------------------------------


def test_only_active_postulantes_can_be_added():
    setup = Canales()
    canal = setup.canal()
    assert setup.put_member(canal["id"], setup.caro).status_code == 204
    assert setup.put_member(canal["id"], setup.caro).status_code == 204  # idempotent
    assert names(setup.club_get(canal["id"], "/miembros")) == ["Ana T.", "Bea R.", "Caro P."]
    assert setup.club_get(canal["id"], "/candidatos").json() == []

    rejected = uuid4()
    setup.postular(rejected, estado="RECHAZADO")
    stranger = uuid4()
    other_id, _ = setup.other_club()
    elsewhere = uuid4()
    postular(setup.repository, other_id, elsewhere)
    for usuario_id in (rejected, stranger, elsewhere):
        response = setup.put_member(canal["id"], usuario_id)
        assert response.status_code == 404
        assert response.json() == {"detail": "Postulante no encontrado."}
    assert setup.put_member(uuid4(), setup.caro).json() == {"detail": "Canal no encontrado."}


def test_a_channel_has_at_most_500_members(monkeypatch):
    monkeypatch.setattr("app.services.canal_service.MAX_MIEMBROS", 2)
    setup = Canales()
    canal = setup.canal()
    response = setup.put_member(canal["id"], setup.caro)
    assert response.status_code == 409
    # Someone already in is still fine.
    assert setup.put_member(canal["id"], setup.athlete).status_code == 204


def test_creation_takes_at_most_500_accepted(monkeypatch):
    monkeypatch.setattr("app.services.canal_service.MAX_MIEMBROS", 1)
    setup = Canales()
    assert setup.canal()["miembros"] == 1


def test_removing_a_member_is_idempotent():
    setup = Canales()
    canal = setup.canal()
    url = setup.club_url(canal["id"], f"/miembros/{setup.bea}")
    assert setup.client.delete(url, headers=setup.principal_headers).status_code == 204
    assert setup.client.delete(url, headers=setup.principal_headers).status_code == 204
    assert names(setup.club_get(canal["id"], "/miembros")) == ["Ana T."]
    assert setup.member_messages(canal["id"], headers=setup.bea_headers).status_code == 404
    assert setup.mine(setup.bea_headers).json() == []


def test_rejection_without_another_active_application_removes_the_membership():
    setup = Canales()
    canal = setup.canal()
    response = setup.client.patch(
        f"{REVIEW_PATH}/{setup.bea_postulacion}", headers=setup.principal_headers, json={"estado": "RECHAZADO"}
    )
    assert response.status_code == 200
    assert names(setup.club_get(canal["id"], "/miembros")) == ["Ana T."]
    assert setup.mine(setup.bea_headers).json() == []


# -- messages ------------------------------------------------------------------------------------


def test_club_message_reaches_the_members_signed_by_the_club_without_text_in_the_notice():
    setup = Canales()
    canal = setup.canal()
    setup.repository.outbox.clear()
    response = setup.club_send(canal["id"], "Partido suspendido por lluvia", headers=setup.admin_headers)
    assert response.status_code == 201, response.text
    mensaje = response.json()
    assert set(mensaje) == {"id", "de", "autor_nombre", "texto", "enviado_en", "propio"}
    assert mensaje["de"] == "club" and mensaje["autor_nombre"] == "Club Ñuñoa" and mensaje["propio"] is True

    assert {e.user_id for e in setup.repository.outbox} == {setup.athlete, setup.bea}
    for event in setup.repository.outbox:
        assert event.kind == "canal"
        assert "lluvia" not in event.title + event.body + str(event.data)
        assert event.data == {"screen": "canal", "clubId": str(setup.club_id), "canalId": canal["id"]}

    page = setup.member_messages(canal["id"]).json()
    assert page["mensajes"] == [{**mensaje, "propio": False}]
    assert set(page["canal"]) == {
        "id", "club_id", "club_nombre", "club_logo", "nombre", "miembros_escriben", "ultimo",
        "no_leidos", "silenciado",
    }
    assert page["canal"]["no_leidos"] == 1 and page["canal"]["club_nombre"] == "Club Ñuñoa"
    # The pending postulante is not a member.
    assert setup.member_messages(canal["id"], headers=setup.caro_headers).status_code == 404


def test_club_message_is_idempotent():
    setup = Canales()
    canal = setup.canal()
    setup.repository.outbox.clear()
    client_message_id = uuid4()
    first = setup.club_send(canal["id"], client_message_id=client_message_id)
    retry = setup.club_send(canal["id"], client_message_id=client_message_id, headers=setup.principal_headers)
    assert first.json() == retry.json()
    assert len(setup.repository.outbox) == 2


def test_members_write_only_when_the_club_allows_it():
    setup = Canales()
    canal = setup.canal()
    denied = setup.member_send(canal["id"])
    assert denied.status_code == 403 and denied.json() == {"detail": "En este canal solo escribe el club."}

    setup.client.patch(setup.club_url(canal["id"]), headers=setup.principal_headers, json={"miembros_escriben": True})
    setup.repository.outbox.clear()
    response = setup.member_send(canal["id"], "Llevo los conos")
    assert response.status_code == 201, response.text
    mensaje = response.json()
    assert mensaje["de"] == "deportista" and mensaje["autor_nombre"] == "Ana T." and mensaje["propio"] is True
    # Every other member is notified, not the author.
    assert [e.user_id for e in setup.repository.outbox] == [setup.bea]

    bea_view = setup.member_messages(canal["id"], headers=setup.bea_headers).json()
    assert bea_view["mensajes"][-1]["propio"] is False and bea_view["mensajes"][-1]["autor_nombre"] == "Ana T."
    club_view = setup.club_get(canal["id"], "/messages").json()
    assert club_view["mensajes"][-1]["propio"] is False
    assert club_view["canal"]["no_leidos"] == 1
    # A non-member cannot write either.
    assert setup.member_send(canal["id"], headers=setup.caro_headers).status_code == 404


@pytest.mark.parametrize(
    "body",
    [{"texto": "", "client_message_id": str(uuid4())}, {"texto": "x" * 1001, "client_message_id": str(uuid4())},
     {"texto": "Hola"}, {"texto": "Hola", "client_message_id": str(uuid4()), "autor_id": str(uuid4())}],
)
def test_invalid_messages_are_400(body):
    setup = Canales()
    canal = setup.canal()
    response = setup.client.post(setup.club_url(canal["id"], "/messages"), headers=setup.principal_headers, json=body)
    assert response.status_code == 400


def test_muted_members_are_not_notified():
    setup = Canales()
    canal = setup.canal()
    assert setup.mute(canal["id"], True).status_code == 204
    assert setup.mine().json()[0]["silenciado"] is True
    setup.repository.outbox.clear()
    setup.club_send(canal["id"])
    assert [e.user_id for e in setup.repository.outbox] == [setup.bea]
    assert setup.mute(canal["id"], False).status_code == 204
    setup.repository.outbox.clear()
    setup.club_send(canal["id"])
    assert {e.user_id for e in setup.repository.outbox} == {setup.athlete, setup.bea}
    assert setup.mute(canal["id"], True, headers=setup.caro_headers).status_code == 404


def test_unread_counts_and_read_marks_on_both_sides():
    setup = Canales()
    canal = setup.canal(miembros_escriben=True)
    first = setup.club_send(canal["id"], "Uno").json()
    setup.club_send(canal["id"], "Dos")
    assert setup.mine().json()[0]["no_leidos"] == 2
    assert setup.member_read(canal["id"], first["id"]).status_code == 204
    assert setup.mine().json()[0]["no_leidos"] == 1
    setup.member_read(canal["id"], 0)  # never back
    assert setup.mine().json()[0]["no_leidos"] == 1
    setup.member_read(canal["id"], 10**9)  # never past the last message
    setup.club_send(canal["id"], "Tres")
    assert setup.mine().json()[0]["no_leidos"] == 1

    reply = setup.member_send(canal["id"], headers=setup.bea_headers).json()
    club_list = setup.client.get(setup.club_url(), headers=setup.principal_headers).json()
    assert club_list[0]["no_leidos"] == 1 and club_list[0]["ultimo"]["id"] == reply["id"]
    read = setup.client.post(
        setup.club_url(canal["id"], "/read"), headers=setup.admin_headers, json={"hasta_id": reply["id"]}
    )
    assert read.status_code == 204
    assert setup.client.get(setup.club_url(), headers=setup.principal_headers).json()[0]["no_leidos"] == 0
    assert setup.member_read(canal["id"], 1, headers=setup.caro_headers).status_code == 404


def test_paging_and_limit_cap():
    setup = Canales()
    canal = setup.canal()
    ids = [setup.club_send(canal["id"], f"m{i}").json()["id"] for i in range(5)]
    got = lambda **p: [m["id"] for m in setup.member_messages(canal["id"], **p).json()["mensajes"]]  # noqa: E731
    assert got() == ids
    assert got(limit=2) == ids[-2:]
    assert got(after_id=ids[1], limit=2) == ids[2:4]
    assert got(before_id=ids[3], limit=2) == ids[1:3]
    assert setup.member_messages(canal["id"], limit=101).status_code == 400
    assert setup.club_get(canal["id"], "/messages", limit=101).status_code == 400


def test_my_channels_by_latest_activity_and_hidden_when_the_club_is_inactive():
    setup = Canales()
    first = setup.canal(nombre="Primero")
    setup.canal(nombre="Segundo")
    assert names(setup.mine()) == ["Segundo", "Primero"]
    setup.club_send(first["id"])
    mine = setup.mine().json()
    assert [c["nombre"] for c in mine] == ["Primero", "Segundo"]
    assert mine[0]["ultimo"]["autor_nombre"] == "Club Ñuñoa" and mine[0]["silenciado"] is False
    setup.repository.update_club(setup.club_id, {"is_active": False})
    assert setup.mine().json() == []
    assert setup.member_messages(first["id"]).status_code == 404
    # Its administrators still read, but neither write nor create.
    assert setup.club_get(first["id"], "/messages").status_code == 200
    assert setup.club_send(first["id"]).status_code == 409
    assert setup.create().status_code == 409


def test_polling_a_channel_is_one_repository_call_and_never_asks_ms_users():
    setup = Canales()
    canal = setup.canal()
    ultimo = setup.club_send(canal["id"]).json()["id"]
    names_before = setup.names.calls
    calls = count_repository_calls(setup)

    assert setup.member_messages(canal["id"], after_id=ultimo).json()["mensajes"] == []
    assert calls == ["mensajes_canal_deportista"]
    calls.clear()
    assert setup.club_get(canal["id"], "/messages", after_id=ultimo).json()["mensajes"] == []
    assert calls == ["mensajes_canal_club"]
    calls.clear()
    setup.member_read(canal["id"], ultimo)
    assert calls == ["mark_read_canal_deportista"]
    calls.clear()
    setup.client.post(setup.club_url(canal["id"], "/read"), headers=setup.principal_headers, json={"hasta_id": ultimo})
    assert calls == ["mark_read_canal_club"]
    assert setup.names.calls == names_before


@pytest.mark.parametrize("path", ["/canales/me", "/{club}/canales"])
def test_channel_routes_require_a_token(path):
    setup = Canales()
    assert setup.client.get(CLUBS_PATH + path.format(club=setup.club_id)).status_code == 401


# -- answering an inactivity notice --------------------------------------------------------------


def test_deciding_without_a_pending_notice_is_409():
    setup = Canales()
    canal = setup.canal()
    response = setup.decide(canal["id"], "mantener")
    assert response.status_code == 409
    assert setup.decide(canal["id"], "borrar").status_code == 400


def test_keep_postpones_the_next_notice_30_days():
    setup = Canales()
    canal = setup.canal()
    aviso = datetime.now(timezone.utc) - timedelta(days=1)
    setup.repository.set_canal_fechas(UUID(canal["id"]), aviso_inactividad_en=aviso)
    assert setup.club_get(canal["id"], "/messages").json()["canal"]["aviso_inactividad_en"] is not None
    response = setup.decide(canal["id"], "mantener", headers=setup.admin_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == canal["id"] and body["aviso_inactividad_en"] is None
    mantener = datetime.fromisoformat(body["mantener_hasta"])
    assert abs(mantener - (datetime.now(timezone.utc) + timedelta(days=30))) < timedelta(minutes=1)
    assert setup.decide(canal["id"], "mantener").status_code == 409


def test_delete_answer_removes_the_channel():
    setup = Canales()
    canal = setup.canal()
    setup.club_send(canal["id"])
    setup.repository.set_canal_fechas(UUID(canal["id"]), aviso_inactividad_en=datetime.now(timezone.utc))
    response = setup.decide(canal["id"], "eliminar")
    assert response.status_code == 204 and response.content == b""
    assert setup.club_get(canal["id"], "/miembros").status_code == 404
    assert setup.mine().json() == []


def test_a_new_message_cancels_the_pending_notice():
    setup = Canales()
    canal = setup.canal()
    setup.repository.set_canal_fechas(
        UUID(canal["id"]), aviso_inactividad_en=datetime.now(timezone.utc),
        mantener_hasta=datetime.now(timezone.utc) + timedelta(days=3),
    )
    setup.club_send(canal["id"])
    canal_now = setup.club_get(canal["id"], "/messages").json()["canal"]
    assert canal_now["aviso_inactividad_en"] is None and canal_now["mantener_hasta"] is None
    assert setup.decide(canal["id"], "eliminar").status_code == 409


# -- the inactivity rule, with the time injected -------------------------------------------------

T0 = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)


def canal_en(ultima=T0, aviso=None, mantener=None) -> CanalInactividad:
    return CanalInactividad(
        id=uuid4(), club_id=uuid4(), nombre="Plantel", ultima_actividad=ultima,
        aviso_inactividad_en=aviso, mantener_hasta=mantener,
    )


@pytest.mark.parametrize(
    ("canal", "ahora", "esperado"),
    [
        (canal_en(), T0 + timedelta(days=29, hours=23), "nada"),
        (canal_en(), T0 + timedelta(days=30), "avisar"),
        (canal_en(), T0 + timedelta(days=90), "avisar"),
        (canal_en(aviso=T0 + timedelta(days=30)), T0 + timedelta(days=36, hours=23), "nada"),
        (canal_en(aviso=T0 + timedelta(days=30)), T0 + timedelta(days=37), "eliminar"),
        (canal_en(mantener=T0 + timedelta(days=61)), T0 + timedelta(days=60), "nada"),
        (canal_en(mantener=T0 + timedelta(days=61)), T0 + timedelta(days=61), "avisar"),
        # A message after the keep: 30 days counted from it.
        (canal_en(ultima=T0 + timedelta(days=50), mantener=T0 + timedelta(days=61)), T0 + timedelta(days=62), "nada"),
    ],
)
def test_decidir(canal, ahora, esperado):
    assert decidir(canal, ahora) == esperado


def test_revisar_notifies_admins_then_deletes_without_an_answer():
    setup = Canales()
    canal_id = UUID(setup.canal()["id"])
    fresh = UUID(setup.canal(nombre="Nuevo")["id"])
    now = datetime.now(timezone.utc)
    setup.repository.set_canal_fechas(fresh, ultima_actividad=now + timedelta(days=20))
    setup.repository.outbox.clear()

    ahora = now + timedelta(days=31)
    assert revisar(setup.repository, ahora) == canal_inactividad.Resultado(avisados=1)
    assert {e.user_id for e in setup.repository.outbox} == {setup.principal, setup.admin}
    for event in setup.repository.outbox:
        assert event.kind == "canal"
        assert event.data == {"screen": "canal_inactivo", "clubId": str(setup.club_id), "canalId": str(canal_id)}
    assert setup.repository.get_canal_club(setup.club_id, canal_id).aviso_inactividad_en == ahora

    # The next hours do nothing new.
    setup.repository.outbox.clear()
    assert revisar(setup.repository, ahora + timedelta(hours=1)) == canal_inactividad.Resultado()
    assert setup.repository.outbox == []

    # Seven days without an answer: deleted.
    assert revisar(setup.repository, ahora + timedelta(days=7)).eliminados == 1
    assert setup.repository.get_canal_club(setup.club_id, canal_id) is None
    assert setup.repository.get_canal_club(setup.club_id, fresh) is not None


def test_revisar_after_keep_notifies_again_30_days_later():
    setup = Canales()
    canal_id = UUID(setup.canal()["id"])
    now = datetime.now(timezone.utc)
    revisar(setup.repository, now + timedelta(days=31))
    assert setup.decide(canal_id, "mantener").status_code == 200
    mantener = setup.repository.get_canal_club(setup.club_id, canal_id).mantener_hasta
    setup.repository.outbox.clear()
    assert revisar(setup.repository, mantener - timedelta(hours=1)) == canal_inactividad.Resultado()
    assert revisar(setup.repository, mantener).avisados == 1
    assert len(setup.repository.outbox) == 2


def test_revisar_skips_a_channel_that_got_a_message_after_being_read():
    setup = Canales()
    canal_id = UUID(setup.canal()["id"])
    ahora = datetime.now(timezone.utc) + timedelta(days=31)
    leido = setup.repository.canales_por_revisar(ahora - canal_inactividad.INACTIVIDAD)
    setup.club_send(canal_id)
    setup.repository.outbox.clear()
    assert setup.repository.avisar_inactividad(leido[0], ahora, canal_inactividad._aviso(leido[0])) is False
    assert setup.repository.outbox == []


def test_revisar_does_nothing_without_the_lock():
    class Locked:
        from contextlib import contextmanager

        @contextmanager
        def inactividad_lock(self):
            yield False

        def canales_por_revisar(self, *_):  # pragma: no cover - must not be called
            raise AssertionError("ran without the lock")

    assert revisar(Locked(), T0) == canal_inactividad.Resultado(bloqueado=True)


def test_worker_logs_failures_and_stops_cleanly(caplog):
    class Broken:
        calls = threading.Event()

        def inactividad_lock(self):
            Broken.calls.set()
            raise RuntimeError("base caída")

    worker = InactividadWorker(Broken(), interval=3600, clock=lambda: T0)
    with caplog.at_level(logging.ERROR, logger="app.services.canal_inactividad"):
        assert worker.run_once() is None
    assert "canales inactivos" in caplog.text

    worker.start()
    assert Broken.calls.wait(5)
    worker.stop(timeout=5)
    assert not worker._thread.is_alive()


def test_worker_can_be_disabled_by_environment(monkeypatch):
    monkeypatch.delenv(canal_inactividad.ENV_ENABLED, raising=False)
    assert canal_inactividad.enabled() is True
    for value in ("false", "0", "OFF", " no "):
        monkeypatch.setenv(canal_inactividad.ENV_ENABLED, value)
        assert canal_inactividad.enabled() is False
    monkeypatch.setenv(canal_inactividad.ENV_ENABLED, "true")
    assert canal_inactividad.enabled() is True
