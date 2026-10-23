from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from test_club_admin import nuevo_rut, token  # also sets JWT_SECRET before the app is imported
from test_convocatorias import payload

from app.core.security import Actor
from app.main import create_app
from app.repositories.base import ClubSport, StoredClub
from app.repositories.mock_club_repository import MockClubRepository
from app.services.rut import normalizar_rut

CLUBS_PATH = "/api/v1/clubs"
ME_PATH = "/api/v1/clubs/me"
PRINCIPAL_EMAIL = "paula.rojas@correo.cl"


class FakeNames:
    """Ms_Users stand-in for display names; None or an error means unknown."""

    def __init__(self) -> None:
        self.names: dict[UUID, str] = {}
        self.fail = False
        self.calls = 0

    def display_name(self, actor: Actor) -> str | None:
        self.calls += 1
        if self.fail:
            raise RuntimeError("Ms_Users caído")
        return self.names.get(actor.id)


def correo() -> str:
    """A different email on every call."""
    return f"admin.{uuid4().hex[:12]}@correo.cl"


class Admins:
    """A club created through the API by its PRINCIPAL."""

    def __init__(self) -> None:
        self.repository = MockClubRepository(sports=[ClubSport(1, "Fútbol")])
        self.names = FakeNames()
        self.client = TestClient(create_app(repository=self.repository, users=self.names))
        self.principal = uuid4()
        self.names.names[self.principal] = "Paula Rojas"
        self.principal_headers = token(self.principal, email=PRINCIPAL_EMAIL)
        response = self.client.post(
            CLUBS_PATH, headers=self.principal_headers,
            json={"nombre": "Club Ñuñoa", "rut": nuevo_rut(), "deporte_ids": [1]},
        )
        assert response.status_code == 201, response.text
        self.club_id = UUID(response.json()["id"])

    def other_club(self) -> tuple[UUID, dict]:
        """A second club in the same service, with its own PRINCIPAL."""
        headers = token(uuid4(), email=correo())
        response = self.client.post(
            CLUBS_PATH, headers=headers, json={"nombre": "Club Vecino", "rut": nuevo_rut()}
        )
        assert response.status_code == 201, response.text
        return UUID(response.json()["id"]), headers

    def assign(self, email: str, headers: dict | None = None, club_id: object = None):
        return self.client.post(
            f"{CLUBS_PATH}/{club_id or self.club_id}/admins/assignments",
            headers=headers or self.principal_headers, json={"email": email},
        )

    def assignments(self, headers: dict | None = None, club_id: object = None):
        return self.client.get(
            f"{CLUBS_PATH}/{club_id or self.club_id}/admins/assignments",
            headers=headers or self.principal_headers,
        )

    def revoke(self, asignacion_id: object, headers: dict | None = None, club_id: object = None):
        return self.client.delete(
            f"{CLUBS_PATH}/{club_id or self.club_id}/admins/assignments/{asignacion_id}",
            headers=headers or self.principal_headers,
        )

    def me(self, headers: dict):
        return self.client.get(ME_PATH, headers=headers)

    def new_admin(self, nombre: str = "Andrés Soto") -> tuple[UUID, dict]:
        """An account that joined the club through an assigned email."""
        usuario, email = uuid4(), correo()
        self.names.names[usuario] = nombre
        assert self.assign(email).status_code == 201
        headers = token(usuario, email=email)
        assert self.me(headers).status_code == 200
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


# -- token email claim -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("claim", "expected"),
    [
        ("  Ana.Soto@Correo.CL ", "ana.soto@correo.cl"),
        ("ana@correo.cl", "ana@correo.cl"),
        (None, None),
        (123, None),
        (["ana@correo.cl"], None),
        ("", None),
        ("sin-arroba", None),
        ("ana soto@correo.cl", None),
        ("a" * 250 + "@correo.cl", None),
    ],
)
def test_email_claim_is_optional_and_never_breaks_the_token(claim, expected):
    from app.core.security import authenticate

    extra = {} if claim is None else {"email": claim}
    bearer = token(uuid4(), **extra)["Authorization"].removeprefix("Bearer ")
    assert authenticate(bearer).email == expected


# -- assigning emails (sección E) ------------------------------------------------------------


def test_principal_assigns_an_email_that_stays_pending():
    setup = Admins()
    response = setup.assign("  Andres.Soto@Correo.CL ")
    assert response.status_code == 201, response.text
    created = response.json()
    assert set(created) == {"id", "email", "asignada_por", "fecha_creacion"}
    assert created["email"] == "andres.soto@correo.cl"
    assert created["asignada_por"] == str(setup.principal)
    assert setup.assignments().json() == [created]
    # Nobody is an administrator until that person opens the app.
    assert [a["rol"] for a in setup.admins().json()] == ["PRINCIPAL"]


def test_the_answer_never_reveals_whether_an_account_exists():
    setup = Admins()
    # An account that exists, with a verified email: it created another club.
    existing = correo()
    created = setup.client.post(
        CLUBS_PATH, headers=token(uuid4(), email=existing), json={"nombre": "Otro", "rut": nuevo_rut()}
    )
    assert created.status_code == 201
    answers = [setup.assign(existing), setup.assign(correo())]
    assert [r.status_code for r in answers] == [201, 201]
    shapes = [
        {k: v for k, v in r.json().items() if k not in ("id", "email", "fecha_creacion")}
        for r in answers
    ]
    assert shapes[0] == shapes[1]
    assert len(setup.assignments().json()) == 2


def test_an_admin_can_neither_assign_nor_revoke():
    setup = Admins()
    _, headers = setup.new_admin()
    pending = setup.assign(correo()).json()
    response = setup.assign(correo(), headers)
    assert response.status_code == 403
    assert response.json() == {"detail": "Solo el administrador principal puede asignar administradores."}
    assert setup.revoke(pending["id"], headers).status_code == 403
    # Any administrator sees the pending ones.
    listed = setup.assignments(headers)
    assert listed.status_code == 200 and [a["id"] for a in listed.json()] == [pending["id"]]


def test_an_email_already_pending_in_the_club_is_a_409():
    setup = Admins()
    assert setup.assign("andres@correo.cl").status_code == 201
    again = setup.assign("ANDRES@correo.cl")
    assert again.status_code == 409
    assert again.json() == {"detail": "Ese correo ya tiene una asignación pendiente en este club."}
    assert len(setup.assignments().json()) == 1
    # Another club may assign the same email: it stays pending in both.
    other_id, other_headers = setup.other_club()
    assert setup.assign("andres@correo.cl", other_headers, other_id).status_code == 201


def test_assigning_your_own_email_is_a_409():
    setup = Admins()
    response = setup.assign(PRINCIPAL_EMAIL.upper())
    assert response.status_code == 409
    assert "propio correo" in response.json()["detail"]
    assert setup.assignments().json() == []


def test_the_email_of_a_current_admin_is_a_409():
    setup = Admins()
    usuario, email = uuid4(), correo()
    setup.assign(email)
    assert setup.me(token(usuario, email=email)).status_code == 200
    response = setup.assign(email)
    assert response.status_code == 409
    assert response.json() == {"detail": "Ese correo ya administra este club."}
    # Once removed, the email can be assigned again to give the access back.
    assert setup.remove(usuario, setup.principal_headers).status_code == 204
    assert setup.assign(email).status_code == 201
    assert setup.me(token(usuario, email=email)).json()["mi_rol"] == "ADMIN"


def test_maximum_of_admins_counts_pending_assignments(monkeypatch):
    monkeypatch.setenv("CLUB_MAX_ADMINS", "3")
    setup = Admins()
    setup.new_admin()  # 2 admins
    pending = setup.assign(correo())  # + 1 pending = 3
    assert pending.status_code == 201
    full = setup.assign(correo())
    assert full.status_code == 409
    assert "máximo de 3" in full.json()["detail"]
    # Revoking the pending one frees the place.
    assert setup.revoke(pending.json()["id"]).status_code == 204
    assert setup.assign(correo()).status_code == 201


@pytest.mark.parametrize("value", ["2", "abc", "1000"])
def test_wrong_max_admins_configuration_is_refused(monkeypatch, value):
    from app.core.config import get_club_max_admins

    monkeypatch.setenv("CLUB_MAX_ADMINS", value)
    with pytest.raises(RuntimeError):
        get_club_max_admins()


@pytest.mark.parametrize(
    "email",
    [
        "no-es-correo",
        "ana@localhost",
        "ana@",
        "@correo.cl",
        "ana soto@correo.cl",
        "a" * 64 + "@" + "b" * 63 + "." + "c" * 63 + "." + "d" * 63 + ".cl",
    ],
)
def test_invalid_email_is_a_422(email):
    setup = Admins()
    response = setup.assign(email)
    assert response.status_code == 422
    assert response.json()["detail"].startswith("Correo inválido")
    assert setup.assignments().json() == []


@pytest.mark.parametrize(
    "body",
    [{}, {"email": 123}, {"email": ""}, {"email": "   "},
     {"email": "ana@correo.cl", "usuario_id": str(uuid4())}],
)
def test_malformed_body_is_rejected_like_any_other_input(body):
    setup = Admins()
    response = setup.client.post(
        f"{CLUBS_PATH}/{setup.club_id}/admins/assignments", headers=setup.principal_headers, json=body
    )
    assert response.status_code == 400
    assert setup.assignments().json() == []


def test_list_shows_only_pending_assignments_newest_first():
    setup = Admins()
    accepted_email = correo()
    setup.assign(accepted_email)
    setup.me(token(uuid4(), email=accepted_email))
    revoked = setup.assign(correo()).json()
    setup.revoke(revoked["id"])
    first = setup.assign(correo()).json()
    second = setup.assign(correo()).json()
    assert [a["id"] for a in setup.assignments().json()] == [second["id"], first["id"]]


def test_revoking_only_works_once_and_only_on_pending_assignments():
    setup = Admins()
    pending = setup.assign(correo()).json()
    assert setup.revoke(pending["id"]).status_code == 204
    again = setup.revoke(pending["id"])
    assert again.status_code == 404
    assert again.json() == {"detail": "Asignación no encontrada o ya no está pendiente."}

    email = correo()
    accepted = setup.assign(email).json()
    setup.me(token(uuid4(), email=email))
    assert setup.revoke(accepted["id"]).status_code == 404
    assert setup.revoke(uuid4()).status_code == 404


def test_another_clubs_assignment_cannot_be_revoked():
    setup = Admins()
    other_id, other_headers = setup.other_club()
    email = correo()
    asignacion = setup.assign(email, other_headers, other_id).json()
    assert setup.revoke(asignacion["id"]).status_code == 404
    joined = setup.me(token(uuid4(), email=email))
    assert joined.status_code == 200 and joined.json()["id"] == str(other_id)


@pytest.mark.parametrize("who", ["another club", "player", "no token"])
def test_outsiders_cannot_see_or_manage_admins(who):
    setup = Admins()
    pending = setup.assign(correo()).json()
    headers = {
        "another club": setup.other_club()[1],
        "player": token(uuid4(), "player"),
        "no token": {},
    }[who]
    expected = {"another club": 403, "player": 403, "no token": 401}[who]
    path = f"{CLUBS_PATH}/{setup.club_id}/admins"
    attempts = [
        setup.client.get(path, headers=headers),
        setup.client.post(f"{path}/assignments", headers=headers, json={"email": correo()}),
        setup.client.get(f"{path}/assignments", headers=headers),
        setup.client.delete(f"{path}/assignments/{pending['id']}", headers=headers),
        setup.client.delete(f"{path}/{setup.principal}", headers=headers),
    ]
    assert [r.status_code for r in attempts] == [expected] * 5
    assert [a.id for a in setup.repository.list_asignaciones(setup.club_id)] == [UUID(pending["id"])]


def test_invitation_routes_no_longer_exist():
    setup = Admins()
    path = f"{CLUBS_PATH}/{setup.club_id}/admins/invitations"
    assert setup.client.post(path, headers=setup.principal_headers).status_code in (404, 405)
    assert setup.client.get(path, headers=setup.principal_headers).status_code in (404, 405)
    accept = setup.client.post(
        f"{CLUBS_PATH}/admins/invitations/accept", headers=setup.principal_headers,
        json={"codigo": "ABCDEFGHJK"},
    )
    assert accept.status_code in (404, 405)


# -- activation on GET /clubs/me --------------------------------------------------------------


def test_a_club_admin_with_the_assigned_email_becomes_admin_on_me():
    setup = Admins()
    usuario, email = uuid4(), "andres.soto@correo.cl"
    setup.names.names[usuario] = "Andrés Soto"
    setup.assign("Andres.Soto@correo.cl")
    response = setup.me(token(usuario, email=email))
    assert response.status_code == 200, response.text
    assert response.json()["id"] == str(setup.club_id) and response.json()["mi_rol"] == "ADMIN"
    admins = setup.admins().json()
    assert [(a["nombre"], a["rol"]) for a in admins] == [("Paula Rojas", "PRINCIPAL"), ("Andrés Soto", "ADMIN")]
    assert setup.repository.get_admin(setup.club_id, usuario).agregado_por == setup.principal
    assert setup.assignments().json() == []
    # Later calls just read the club.
    assert setup.me(token(usuario, email=email)).json()["mi_rol"] == "ADMIN"


def test_the_token_email_claim_is_compared_in_lower_case():
    setup = Admins()
    setup.assign("ana@correo.cl")
    assert setup.me(token(uuid4(), email="ANA@Correo.cl")).status_code == 200


def test_without_email_claim_nothing_is_activated():
    setup = Admins()
    setup.assign("ana@correo.cl")
    response = setup.me(token(uuid4()))
    assert response.status_code == 404
    assert response.json() == {"detail": "Aún no tienes un club."}
    assert len(setup.assignments().json()) == 1


def test_an_athlete_with_the_assigned_email_never_activates_it():
    from app.core.security import ATHLETE_ROLES
    from app.services.club_service import ClubService

    setup = Admins()
    setup.assign("ana@correo.cl")
    # The route only lets club_admin accounts in.
    assert setup.me(token(uuid4(), "player", email="ana@correo.cl")).status_code == 403
    # And the service checks the role again.
    service = ClubService(setup.repository, setup.names)
    for role in ATHLETE_ROLES:
        actor = Actor(id=uuid4(), roles=frozenset({role}), email="ana@correo.cl")
        with pytest.raises(Exception) as error:
            service.my_club(actor)
        assert getattr(error.value, "status_code", None) == 404
    assert len(setup.assignments().json()) == 1


def test_an_account_that_already_administers_a_club_does_not_activate_it():
    setup = Admins()
    other_id, _ = setup.other_club()
    other_principal = setup.repository.list_admins(other_id)[0].usuario_id
    email = correo()
    setup.assign(email)
    mine = setup.me(token(other_principal, email=email))
    assert mine.status_code == 200 and mine.json()["id"] == str(other_id)
    # It stays pending: one person, one club.
    assert [a["email"] for a in setup.assignments().json()] == [email]
    assert len(setup.admins().json()) == 1


def test_a_revoked_assignment_is_not_activated():
    setup = Admins()
    email = correo()
    setup.revoke(setup.assign(email).json()["id"])
    assert setup.me(token(uuid4(), email=email)).status_code == 404
    assert len(setup.admins().json()) == 1


def test_an_assignment_of_an_inactive_club_is_not_activated():
    setup = Admins()
    email = correo()
    setup.assign(email)
    setup.repository.update_club(setup.club_id, {"is_active": False})
    assert setup.me(token(uuid4(), email=email)).status_code == 404


def test_with_two_clubs_pending_the_oldest_assignment_wins():
    setup = Admins()
    other_id, other_headers = setup.other_club()
    email = correo()
    assert setup.assign(email, other_headers, other_id).status_code == 201  # older
    assert setup.assign(email).status_code == 201
    joined = setup.me(token(uuid4(), email=email))
    assert joined.json()["id"] == str(other_id)
    assert [a["email"] for a in setup.assignments().json()] == [email]
    assert setup.assignments(other_headers, other_id).json() == []


def test_ms_users_is_asked_for_the_name_only_when_something_is_activated():
    setup = Admins()
    calls = setup.names.calls
    assert setup.me(token(uuid4(), email=correo())).status_code == 404
    assert setup.me(setup.principal_headers).status_code == 200
    assert setup.names.calls == calls
    email = correo()
    setup.assign(email)
    setup.names.fail = True
    usuario = uuid4()
    assert setup.me(token(usuario, email=email)).status_code == 200
    assert setup.names.calls == calls + 1
    # Ms_Users failing never blocks the activation: a neutral name is used.
    assert setup.repository.get_admin(setup.club_id, usuario).nombre == "Administrador del club"


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
    stranger, email = uuid4(), correo()
    setup.assign(email, other_headers, other_id)
    assert setup.me(token(stranger, email=email)).status_code == 200
    assert setup.remove(stranger, setup.principal_headers).status_code == 404
    other_admins = setup.client.get(f"{CLUBS_PATH}/{other_id}/admins", headers=other_headers).json()
    assert len(other_admins) == 2


def test_after_leaving_the_account_may_join_another_club():
    setup = Admins()
    usuario, email = uuid4(), correo()
    setup.assign(email)
    headers = token(usuario, email=email)
    assert setup.me(headers).status_code == 200
    assert setup.remove(usuario, headers).status_code == 204
    # The accepted assignment is not activated again.
    assert setup.me(headers).status_code == 404
    other_id, other_headers = setup.other_club()
    setup.assign(email, other_headers, other_id)
    joined = setup.me(headers)
    assert joined.status_code == 200 and joined.json()["id"] == str(other_id)
