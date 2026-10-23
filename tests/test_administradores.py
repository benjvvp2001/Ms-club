from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from test_club_admin import nuevo_rut, token  # also sets JWT_SECRET before the app is imported
from test_convocatorias import payload

from app.core.security import Actor
from app.main import create_app
from app.repositories.base import ClubSport, StoredClub
from app.repositories.mock_club_repository import MockClubRepository
from app.services.administrador_service import CODIGO_ALFABETO, CODIGO_LARGO
from app.services.rut import normalizar_rut

CLUBS_PATH = "/api/v1/clubs"
ACCEPT_PATH = "/api/v1/clubs/admins/invitations/accept"


class FakeNames:
    """Ms_Users stand-in for display names; None or an error means unknown."""

    def __init__(self) -> None:
        self.names: dict[UUID, str] = {}
        self.fail = False

    def display_name(self, actor: Actor) -> str | None:
        if self.fail:
            raise RuntimeError("Ms_Users caído")
        return self.names.get(actor.id)


class Admins:
    """A club created through the API by its PRINCIPAL."""

    def __init__(self) -> None:
        self.repository = MockClubRepository(sports=[ClubSport(1, "Fútbol")])
        self.names = FakeNames()
        self.client = TestClient(create_app(repository=self.repository, users=self.names))
        self.principal = uuid4()
        self.names.names[self.principal] = "Paula Rojas"
        self.principal_headers = token(self.principal)
        response = self.client.post(
            CLUBS_PATH, headers=self.principal_headers,
            json={"nombre": "Club Ñuñoa", "rut": nuevo_rut(), "deporte_ids": [1]},
        )
        assert response.status_code == 201, response.text
        self.club_id = UUID(response.json()["id"])

    def other_club(self) -> tuple[UUID, dict]:
        """A second club in the same service, with its own PRINCIPAL."""
        headers = token(uuid4())
        response = self.client.post(
            CLUBS_PATH, headers=headers, json={"nombre": "Club Vecino", "rut": nuevo_rut()}
        )
        assert response.status_code == 201, response.text
        return UUID(response.json()["id"]), headers

    def invite(self, headers: dict | None = None, club_id: object = None):
        return self.client.post(
            f"{CLUBS_PATH}/{club_id or self.club_id}/admins/invitations",
            headers=headers or self.principal_headers,
        )

    def accept(self, codigo: str, headers: dict):
        return self.client.post(ACCEPT_PATH, headers=headers, json={"codigo": codigo})

    def new_admin(self, nombre: str = "Andrés Soto") -> tuple[UUID, dict]:
        """An account that joined the club with an invitation."""
        usuario = uuid4()
        self.names.names[usuario] = nombre
        codigo = self.invite().json()["codigo"]
        headers = token(usuario)
        assert self.accept(codigo, headers).status_code == 200
        return usuario, headers

    def admins(self, headers: dict | None = None):
        return self.client.get(
            f"{CLUBS_PATH}/{self.club_id}/admins", headers=headers or self.principal_headers
        )

    def remove(self, usuario_id: UUID, headers: dict):
        return self.client.delete(f"{CLUBS_PATH}/{self.club_id}/admins/{usuario_id}", headers=headers)


# -- RUT --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rut", "expected"),
    [
        ("76.123.456-0", "76123456-0"),
        ("76123456-0", "76123456-0"),
        ("96.790.240-3", "96790240-3"),
        ("77777777-7", "77777777-7"),
        (" 76.123.456-0 ", "76123456-0"),
        ("76123456-1", None),  # wrong check digit
        ("12.345.678-5", None),  # valid, but a natural person (< 50.000.000)
        ("7612345-6", None),  # 7 digits: always a natural person
        ("761234560", None),  # no dash
        ("76 123 456-0", None),
        ("76,123,456-0", None),
        ("abc", None),
        ("٧٦١٢٣٤٥٦-0", None),  # other Unicode digits
    ],
)
def test_rut_is_normalized_and_validated(rut, expected):
    assert normalizar_rut(rut) == expected


def test_rut_with_k_check_digit_is_stored_in_upper_case():
    cuerpo = next(c for c in range(50_000_000, 50_001_000) if normalizar_rut(f"{c}-K"))
    assert normalizar_rut(f"{cuerpo}-k") == f"{cuerpo}-K"


@pytest.mark.parametrize("rut", ["76123456-1", "12345678-5", "76.123.456", "no-es-rut"])
def test_invalid_rut_is_a_422_and_creates_nothing(rut):
    setup = Admins()
    owner = uuid4()
    response = setup.client.post(CLUBS_PATH, headers=token(owner), json={"nombre": "Club", "rut": rut})
    assert response.status_code == 422
    assert "RUT inválido" in response.json()["detail"]
    assert setup.client.get(f"{CLUBS_PATH}/me", headers=token(owner)).status_code == 404


def test_missing_rut_is_rejected():
    setup = Admins()
    response = setup.client.post(CLUBS_PATH, headers=token(uuid4()), json={"nombre": "Club"})
    assert response.status_code == 400


def test_duplicated_rut_is_a_409_even_written_differently():
    setup = Admins()
    rut = nuevo_rut()
    cuerpo, dv = rut.split("-")
    first = setup.client.post(CLUBS_PATH, headers=token(uuid4()), json={"nombre": "Uno", "rut": rut})
    assert first.status_code == 201
    dotted = f"{cuerpo[:2]}.{cuerpo[2:5]}.{cuerpo[5:]}-{dv}"
    second = setup.client.post(CLUBS_PATH, headers=token(uuid4()), json={"nombre": "Dos", "rut": dotted})
    assert second.status_code == 409
    assert second.json() == {"detail": "Ya existe un club con ese RUT."}


def test_rut_cannot_be_edited():
    setup = Admins()
    response = setup.client.patch(
        f"{CLUBS_PATH}/{setup.club_id}", headers=setup.principal_headers, json={"rut": nuevo_rut()}
    )
    assert response.status_code == 400


# -- creating leaves a PRINCIPAL ------------------------------------------------------------


def test_creating_a_club_makes_the_creator_its_principal_with_their_name():
    setup = Admins()
    mine = setup.client.get(f"{CLUBS_PATH}/me", headers=setup.principal_headers).json()
    assert mine["id"] == str(setup.club_id) and mine["mi_rol"] == "PRINCIPAL"
    admins = setup.admins().json()
    assert len(admins) == 1
    assert admins[0]["usuario_id"] == str(setup.principal)
    assert admins[0]["nombre"] == "Paula Rojas" and admins[0]["rol"] == "PRINCIPAL"
    assert admins[0]["fecha_alta"] is not None
    # club.usuario_id is kept as historical data.
    assert setup.repository.get_club(setup.club_id).usuario_id == setup.principal


@pytest.mark.parametrize("fail", [True, False])
def test_ms_users_failing_never_blocks_the_creation(fail):
    setup = Admins()
    setup.names.fail = fail  # False: Ms_Users does not know the name
    owner = uuid4()
    response = setup.client.post(CLUBS_PATH, headers=token(owner), json={"nombre": "Club", "rut": nuevo_rut()})
    assert response.status_code == 201
    admins = setup.client.get(f"{CLUBS_PATH}/{response.json()['id']}/admins", headers=token(owner)).json()
    assert admins[0]["nombre"] == "Administrador del club"


def test_an_admin_of_a_club_cannot_create_another():
    setup = Admins()
    _, headers = setup.new_admin()
    response = setup.client.post(CLUBS_PATH, headers=headers, json={"nombre": "Otro", "rut": nuevo_rut()})
    assert response.status_code == 409
    assert response.json() == {"detail": "Ya administras un club."}


def test_owner_from_before_the_migration_is_principal():
    """A club loaded with usuario_id (as migration 007's backfill does)."""
    setup = Admins()
    owner = uuid4()
    club = setup.repository.add_club(StoredClub(id=uuid4(), nombre="Antiguo", usuario_id=owner))
    mine = setup.client.get(f"{CLUBS_PATH}/me", headers=token(owner)).json()
    assert mine["id"] == str(club.id) and mine["mi_rol"] == "PRINCIPAL" and mine["rut"] is None


# -- an ADMIN manages the club ------------------------------------------------------------


def test_an_admin_manages_profile_convocatorias_and_comunicados():
    setup = Admins()
    _, headers = setup.new_admin()
    mine = setup.client.get(f"{CLUBS_PATH}/me", headers=headers).json()
    assert mine["id"] == str(setup.club_id) and mine["mi_rol"] == "ADMIN"

    edited = setup.client.patch(f"{CLUBS_PATH}/{setup.club_id}", headers=headers, json={"descripcion": "Nueva"})
    assert edited.status_code == 200 and edited.json()["descripcion"] == "Nueva"

    published = setup.client.post(f"{CLUBS_PATH}/{setup.club_id}/announcements", headers=headers, json=payload())
    assert published.status_code == 201, published.text
    closed = setup.client.post(
        f"{CLUBS_PATH}/{setup.club_id}/announcements/{published.json()['id']}/close", headers=headers
    )
    assert closed.status_code == 200 and closed.json()["estado"] == "CERRADA"

    notice = setup.client.post(
        f"{CLUBS_PATH}/{setup.club_id}/notices", headers=headers,
        json={"titulo": "Horario", "contenido": "Entrenamos a las 9:00.", "audiencia": "TODOS"},
    )
    assert notice.status_code == 201
    listed = setup.client.get(f"{CLUBS_PATH}/{setup.club_id}/notices", headers=headers)
    assert listed.status_code == 200 and listed.json()["total"] == 1

    candidatos = setup.client.get(
        f"{CLUBS_PATH}/announcements/{published.json()['id']}/applications", headers=headers
    )
    assert candidatos.status_code == 200


def test_after_leaving_an_admin_no_longer_manages_the_club():
    setup = Admins()
    usuario, headers = setup.new_admin()
    assert setup.remove(usuario, headers).status_code == 204
    response = setup.client.patch(f"{CLUBS_PATH}/{setup.club_id}", headers=headers, json={"descripcion": "x"})
    assert response.status_code == 403
    assert setup.client.get(f"{CLUBS_PATH}/me", headers=headers).status_code == 404


# -- invitations ----------------------------------------------------------------------------


def test_invitation_code_is_shown_once_and_only_its_hash_is_stored():
    setup = Admins()
    response = setup.invite()
    assert response.status_code == 201, response.text
    created = response.json()
    codigo = created["codigo"]
    assert len(codigo) == CODIGO_LARGO and set(codigo) <= set(CODIGO_ALFABETO)
    expira = datetime.fromisoformat(created["expira_en"])
    assert timedelta(hours=71) < expira - datetime.now(timezone.utc) <= timedelta(hours=72)

    stored = setup.repository.list_invitaciones(setup.club_id)[0]
    assert stored.codigo_hash == hashlib.sha256(codigo.encode()).hexdigest()
    assert codigo not in repr(stored)

    listed = setup.client.get(f"{CLUBS_PATH}/{setup.club_id}/admins/invitations", headers=setup.principal_headers)
    assert listed.status_code == 200
    assert listed.json() == [{
        "id": created["id"], "creada_por": str(setup.principal),
        "fecha_creacion": listed.json()[0]["fecha_creacion"], "expira_en": created["expira_en"],
    }]
    assert "codigo" not in listed.text and codigo not in listed.text


def test_accepting_an_invitation_joins_the_club_as_admin():
    setup = Admins()
    usuario = uuid4()
    setup.names.names[usuario] = "Andrés Soto"
    codigo = setup.invite().json()["codigo"]
    # Typed in lower case and with a dash, it still matches.
    response = setup.accept(f"{codigo[:5].lower()}-{codigo[5:]}", token(usuario))
    assert response.status_code == 200, response.text
    assert response.json()["id"] == str(setup.club_id)
    admins = setup.admins().json()
    assert [(a["nombre"], a["rol"]) for a in admins] == [("Paula Rojas", "PRINCIPAL"), ("Andrés Soto", "ADMIN")]
    # A used invitation is no longer pending.
    assert setup.client.get(
        f"{CLUBS_PATH}/{setup.club_id}/admins/invitations", headers=setup.principal_headers
    ).json() == []


def test_used_expired_revoked_or_unknown_codes_get_the_same_404():
    setup = Admins()
    used = setup.invite().json()["codigo"]
    assert setup.accept(used, token(uuid4())).status_code == 200

    expired = setup.invite().json()
    setup.repository.set_invitacion_expira(
        UUID(expired["id"]), datetime.now(timezone.utc) - timedelta(seconds=1)
    )

    revoked = setup.invite().json()
    revoke = setup.client.delete(
        f"{CLUBS_PATH}/{setup.club_id}/admins/invitations/{revoked['id']}", headers=setup.principal_headers
    )
    assert revoke.status_code == 204

    answers = [
        setup.accept(codigo, token(uuid4()))
        for codigo in (used, expired["codigo"], revoked["codigo"], "ABCDEFGHJK")
    ]
    assert {r.status_code for r in answers} == {404}
    assert len({r.text for r in answers}) == 1
    assert answers[0].json() == {"detail": "Código de invitación inválido o vencido."}
    assert len(setup.admins().json()) == 2


def test_an_account_that_already_administers_a_club_cannot_accept():
    setup = Admins()
    _, other_headers = setup.other_club()
    codigo = setup.invite().json()["codigo"]
    response = setup.accept(codigo, other_headers)
    assert response.status_code == 409
    # The invitation is still usable by someone else.
    assert setup.accept(codigo, token(uuid4())).status_code == 200


@pytest.mark.parametrize("role", ["player", "usuario", "admin"])
def test_only_club_admin_accounts_accept_invitations(role):
    setup = Admins()
    codigo = setup.invite().json()["codigo"]
    assert setup.accept(codigo, token(uuid4(), role)).status_code == 403


def test_maximum_of_admins_counts_pending_invitations(monkeypatch):
    monkeypatch.setenv("CLUB_MAX_ADMINS", "3")
    setup = Admins()
    setup.new_admin()  # 2 admins
    pending = setup.invite()  # + 1 pending = 3
    assert pending.status_code == 201
    full = setup.invite()
    assert full.status_code == 409
    assert "máximo de 3" in full.json()["detail"]
    # Revoking the pending one frees the place.
    setup.client.delete(
        f"{CLUBS_PATH}/{setup.club_id}/admins/invitations/{pending.json()['id']}",
        headers=setup.principal_headers,
    )
    assert setup.invite().status_code == 201


def test_expired_invitations_do_not_count_for_the_maximum(monkeypatch):
    monkeypatch.setenv("CLUB_MAX_ADMINS", "3")
    setup = Admins()
    first, second = setup.invite().json(), setup.invite().json()
    assert setup.invite().status_code == 409
    setup.repository.set_invitacion_expira(UUID(first["id"]), datetime.now(timezone.utc) - timedelta(minutes=1))
    assert setup.invite().status_code == 201
    assert second["id"] != first["id"]


@pytest.mark.parametrize("value", ["2", "abc", "1000"])
def test_wrong_max_admins_configuration_is_refused(monkeypatch, value):
    from app.core.config import get_club_max_admins

    monkeypatch.setenv("CLUB_MAX_ADMINS", value)
    with pytest.raises(RuntimeError):
        get_club_max_admins()


def test_any_admin_invites_and_revokes():
    setup = Admins()
    _, headers = setup.new_admin()
    created = setup.invite(headers)
    assert created.status_code == 201
    revoke = setup.client.delete(
        f"{CLUBS_PATH}/{setup.club_id}/admins/invitations/{created.json()['id']}", headers=headers
    )
    assert revoke.status_code == 204
    again = setup.client.delete(
        f"{CLUBS_PATH}/{setup.club_id}/admins/invitations/{created.json()['id']}", headers=headers
    )
    assert again.status_code == 404


def test_another_clubs_invitation_cannot_be_revoked():
    setup = Admins()
    other_id, other_headers = setup.other_club()
    invitacion = setup.invite(other_headers, club_id=other_id).json()
    response = setup.client.delete(
        f"{CLUBS_PATH}/{setup.club_id}/admins/invitations/{invitacion['id']}", headers=setup.principal_headers
    )
    assert response.status_code == 404
    joined = setup.accept(invitacion["codigo"], token(uuid4()))
    assert joined.status_code == 200 and joined.json()["id"] == str(other_id)


@pytest.mark.parametrize("who", ["another club", "player", "no token"])
def test_outsiders_cannot_see_or_manage_admins(who):
    setup = Admins()
    headers = {
        "another club": setup.other_club()[1],
        "player": token(uuid4(), "player"),
        "no token": {},
    }[who]
    expected = {"another club": 403, "player": 403, "no token": 401}[who]
    path = f"{CLUBS_PATH}/{setup.club_id}/admins"
    attempts = [
        setup.client.get(path, headers=headers),
        setup.client.post(f"{path}/invitations", headers=headers),
        setup.client.get(f"{path}/invitations", headers=headers),
        setup.client.delete(f"{path}/{setup.principal}", headers=headers),
    ]
    assert [r.status_code for r in attempts] == [expected] * 4
    assert setup.repository.list_invitaciones(setup.club_id) == []


# -- removing and leaving -------------------------------------------------------------------


def test_principal_removes_an_admin():
    setup = Admins()
    usuario, _ = setup.new_admin()
    assert setup.remove(usuario, setup.principal_headers).status_code == 204
    assert [a["rol"] for a in setup.admins().json()] == ["PRINCIPAL"]
    assert setup.remove(usuario, setup.principal_headers).status_code == 404


def test_an_admin_cannot_remove_another_admin_nor_the_principal():
    setup = Admins()
    first, first_headers = setup.new_admin("Uno")
    second, _ = setup.new_admin("Dos")
    assert setup.remove(second, first_headers).status_code == 403
    assert setup.remove(setup.principal, first_headers).status_code == 403
    assert len(setup.admins().json()) == 3


def test_principal_can_neither_leave_nor_be_removed():
    setup = Admins()
    response = setup.remove(setup.principal, setup.principal_headers)
    assert response.status_code == 409
    assert setup.admins().json()[0]["rol"] == "PRINCIPAL"


def test_principal_cannot_remove_someone_of_another_club():
    setup = Admins()
    other_id, other_headers = setup.other_club()
    stranger = uuid4()
    codigo = setup.invite(other_headers, club_id=other_id).json()["codigo"]
    assert setup.accept(codigo, token(stranger)).status_code == 200
    assert setup.remove(stranger, setup.principal_headers).status_code == 404
    other_admins = setup.client.get(f"{CLUBS_PATH}/{other_id}/admins", headers=other_headers).json()
    assert len(other_admins) == 2


def test_after_leaving_the_account_may_join_another_club():
    setup = Admins()
    usuario, headers = setup.new_admin()
    assert setup.remove(usuario, headers).status_code == 204
    other_id, other_headers = setup.other_club()
    codigo = setup.invite(other_headers, club_id=other_id).json()["codigo"]
    joined = setup.accept(codigo, headers)
    assert joined.status_code == 200 and joined.json()["id"] == str(other_id)
