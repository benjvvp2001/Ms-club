from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

os.environ["JWT_SECRET"] = "test-only-secret-that-is-long-enough"

import jwt
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.repositories.base import ClubSport, StoredClub
from app.repositories.mock_club_repository import MockClubRepository

CLUBS_PATH = "/api/v1/clubs"
FUTBOL = ClubSport(1, "Fútbol")
TENIS = ClubSport(2, "Tenis")
CURLING_INACTIVO = ClubSport(3, "Curling", is_active=False)


def token(user_id: UUID, role: str = "club_admin", **overrides: object) -> dict[str, str]:
    """Access token shaped like the ones Ms_Users signs."""
    now = datetime.now(timezone.utc)
    claims = {
        "sub": str(user_id), "iss": "sportmatch-auth", "aud": "sportmatch-mobile",
        "iat": now, "exp": now + timedelta(minutes=30), "token_type": "access",
        "roles": [role], **overrides,
    }
    encoded = jwt.encode(claims, os.environ["JWT_SECRET"], algorithm="HS256")
    return {"Authorization": f"Bearer {encoded}"}


def make_client() -> tuple[TestClient, MockClubRepository]:
    repository = MockClubRepository(sports=[FUTBOL, TENIS, CURLING_INACTIVO])
    return TestClient(create_app(repository=repository)), repository


def created_club(client: TestClient, owner: UUID, **fields: object) -> dict:
    response = client.post(CLUBS_PATH, headers=token(owner), json={"nombre": "Club Ñuñoa", **fields})
    assert response.status_code == 201, response.text
    return response.json()


# -- creation ----------------------------------------------------------------


def test_representative_creates_their_club_with_full_profile():
    client, _ = make_client()
    owner = uuid4()
    body = {
        "nombre": "Club Deportivo Ñuñoa",
        "descripcion": "Fútbol y tenis para todas las edades.",
        "historia": "Fundado en 1965 por vecinos del barrio.",
        "direccion": "Av. Grecia 2001",
        "comuna": "Ñuñoa",
        "provincia": "Santiago",
        "latitud": -33.4569,
        "longitud": -70.5989,
        "telefono": "+56 2 2345 6789",
        "email_contacto": "contacto@cdnunoa.cl",
        "sitio_web": "https://cdnunoa.cl",
        "logo": "https://cdn.example.com/logo.png",
        "portada": "https://cdn.example.com/portada.jpg",
        "redes_sociales": {"instagram": "https://instagram.com/cdnunoa"},
    }
    response = client.post(CLUBS_PATH, headers=token(owner), json=body)
    assert response.status_code == 201, response.text
    club = response.json()
    assert club["nombre"] == "Club Deportivo Ñuñoa"
    assert club["ubicacion"]["comuna"] == "Ñuñoa" and club["ubicacion"]["provincia"] == "Santiago"
    assert club["redes_sociales"] == {"instagram": "https://instagram.com/cdnunoa"}
    assert club["estado_verificacion"] == "pendiente" and club["verificado"] is False
    assert club["is_active"] is True and club["deportes"] == []
    assert "usuario_id" not in club

    mine = client.get(f"{CLUBS_PATH}/me", headers=token(owner))
    assert mine.status_code == 200 and mine.json()["id"] == club["id"]
    public = client.get(f"{CLUBS_PATH}/{club['id']}").json()
    assert public["historia"] == "Fundado en 1965 por vecinos del barrio."
    assert "estado_verificacion" not in public


def test_a_representative_manages_a_single_club():
    client, _ = make_client()
    owner = uuid4()
    created_club(client, owner)
    second = client.post(CLUBS_PATH, headers=token(owner), json={"nombre": "Otro club"})
    assert second.status_code == 409
    assert second.json() == {"detail": "Ya administras un club."}


@pytest.mark.parametrize("role", ["player", "usuario", "admin"])
def test_only_club_representatives_create_clubs(role):
    client, _ = make_client()
    response = client.post(CLUBS_PATH, headers=token(uuid4(), role), json={"nombre": "Mi club"})
    assert response.status_code == 403


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"nombre": "A"},
        {"nombre": "Club", "estado_verificacion": "verificado"},
        {"nombre": "Club", "usuario_id": str(uuid4())},
        {"nombre": "Club", "latitud": -33.4},
        {"nombre": "Club", "comuna": "Ñuñoa; DROP TABLE club"},
        {"nombre": "Club", "telefono": "llámame"},
        {"nombre": "Club", "email_contacto": "no-es-correo"},
        {"nombre": "Club", "logo": "http://inseguro.example/logo.png"},
        {"nombre": "Club", "sitio_web": "javascript:alert(1)"},
        {"nombre": "Club", "redes_sociales": {"myspace": "https://myspace.com/x"}},
        {"nombre": "Club", "descripcion": "x" * 501},
    ],
)
def test_invalid_club_data_is_rejected_with_400(body):
    client, _ = make_client()
    assert client.post(CLUBS_PATH, headers=token(uuid4()), json=body).status_code == 400


def test_my_club_is_404_before_creating_one():
    client, _ = make_client()
    response = client.get(f"{CLUBS_PATH}/me", headers=token(uuid4()))
    assert response.status_code == 404


# -- edition ------------------------------------------------------------------


def test_owner_edits_only_the_fields_sent():
    client, _ = make_client()
    owner = uuid4()
    club = created_club(client, owner, descripcion="Antes", telefono="+56 9 1234 5678")
    response = client.patch(
        f"{CLUBS_PATH}/{club['id']}",
        headers=token(owner),
        json={"descripcion": "Después", "comuna": "Providencia", "telefono": None},
    )
    assert response.status_code == 200, response.text
    edited = response.json()
    assert edited["descripcion"] == "Después"
    assert edited["ubicacion"]["comuna"] == "Providencia"
    assert edited["telefono"] is None
    assert edited["nombre"] == "Club Ñuñoa"


def test_social_links_are_replaced_as_a_whole_and_null_clears_them():
    client, _ = make_client()
    owner = uuid4()
    club = created_club(client, owner, redes_sociales={"instagram": "https://instagram.com/a"})
    path = f"{CLUBS_PATH}/{club['id']}"
    replaced = client.patch(path, headers=token(owner), json={"redes_sociales": {"x": "https://x.com/a"}})
    assert replaced.json()["redes_sociales"] == {"x": "https://x.com/a"}
    cleared = client.patch(path, headers=token(owner), json={"redes_sociales": None})
    assert cleared.json()["redes_sociales"] == {}


@pytest.mark.parametrize(
    "body",
    [{}, {"nombre": None}, {"latitud": -33.4}, {"estado_verificacion": "verificado"}],
)
def test_invalid_edits_are_rejected_with_400(body):
    client, _ = make_client()
    owner = uuid4()
    club = created_club(client, owner)
    response = client.patch(f"{CLUBS_PATH}/{club['id']}", headers=token(owner), json=body)
    assert response.status_code == 400


def test_coordinates_must_stay_complete_after_a_partial_edit():
    client, _ = make_client()
    owner = uuid4()
    club = created_club(client, owner, latitud=-33.45, longitud=-70.6)
    path = f"{CLUBS_PATH}/{club['id']}"
    assert client.patch(path, headers=token(owner), json={"latitud": None}).status_code == 400
    moved = client.patch(path, headers=token(owner), json={"latitud": -33.5, "longitud": -70.7})
    assert moved.json()["ubicacion"]["latitud"] == -33.5


@pytest.mark.parametrize(
    "who",
    ["another representative", "owner without the role", "player", "admin"],
)
def test_edition_panel_is_exclusive_to_the_owning_representative(who):
    client, _ = make_client()
    owner = uuid4()
    club = created_club(client, owner)
    headers = {
        "another representative": token(uuid4()),
        "owner without the role": token(owner, "player"),
        "player": token(uuid4(), "player"),
        "admin": token(uuid4(), "admin"),
    }[who]
    path = f"{CLUBS_PATH}/{club['id']}"
    response = client.patch(path, headers=headers, json={"descripcion": "Hackeado"})
    assert response.status_code == 403
    assert client.get(path).json()["descripcion"] is None


def test_role_is_checked_before_reaching_the_club():
    client, _ = make_client()
    response = client.patch(f"{CLUBS_PATH}/{uuid4()}", headers=token(uuid4(), "player"), json={"descripcion": "x"})
    assert response.status_code == 403
    assert response.json() == {"detail": "Solo un representante de club puede administrar clubes."}
    assert client.get(f"{CLUBS_PATH}/me", headers=token(uuid4(), "player")).status_code == 403


def test_another_representative_gets_a_clear_403():
    client, _ = make_client()
    club = created_club(client, uuid4())
    response = client.patch(f"{CLUBS_PATH}/{club['id']}", headers=token(uuid4()), json={"descripcion": "x"})
    assert response.json() == {"detail": "No administras este club."}


def test_owner_can_still_manage_an_inactive_club():
    client, repository = make_client()
    owner = uuid4()
    club = repository.add_club(StoredClub(id=uuid4(), nombre="Pausado", usuario_id=owner, is_active=False))
    assert client.get(f"{CLUBS_PATH}/{club.id}").status_code == 404
    response = client.patch(f"{CLUBS_PATH}/{club.id}", headers=token(owner), json={"descripcion": "Volvemos"})
    assert response.status_code == 200 and response.json()["is_active"] is False


def test_editing_a_missing_club_is_404():
    client, _ = make_client()
    response = client.patch(f"{CLUBS_PATH}/{uuid4()}", headers=token(uuid4()), json={"descripcion": "x"})
    assert response.status_code == 404


# -- sports -------------------------------------------------------------------


def test_sports_catalog_is_public_and_lists_active_sports():
    client, _ = make_client()
    response = client.get(f"{CLUBS_PATH}/deportes")
    assert response.status_code == 200
    assert [d["nombre"] for d in response.json()] == ["Fútbol", "Tenis"]


def test_owner_adds_and_removes_sports_idempotently():
    client, _ = make_client()
    owner = uuid4()
    club = created_club(client, owner)
    path = f"{CLUBS_PATH}/{club['id']}/deportes"
    assert [d["id"] for d in client.put(f"{path}/2", headers=token(owner)).json()] == [2]
    assert [d["id"] for d in client.put(f"{path}/1", headers=token(owner)).json()] == [1, 2]
    assert [d["id"] for d in client.put(f"{path}/1", headers=token(owner)).json()] == [1, 2]
    assert [d["id"] for d in client.delete(f"{path}/2", headers=token(owner)).json()] == [1]
    assert [d["id"] for d in client.delete(f"{path}/2", headers=token(owner)).json()] == [1]
    public = client.get(f"{CLUBS_PATH}/{club['id']}").json()
    assert [d["nombre"] for d in public["deportes"]] == ["Fútbol"]
    assert client.get(CLUBS_PATH, params={"deporte": "futbol"}).json()["total"] == 1


@pytest.mark.parametrize(("deporte_id", "expected"), [(3, 404), (99, 404), (0, 400)])
def test_unknown_inactive_or_invalid_sport_cannot_be_added(deporte_id, expected):
    client, _ = make_client()
    owner = uuid4()
    club = created_club(client, owner)
    response = client.put(f"{CLUBS_PATH}/{club['id']}/deportes/{deporte_id}", headers=token(owner))
    assert response.status_code == expected


@pytest.mark.parametrize("role", ["club_admin", "admin", "player"])
def test_only_the_owning_representative_manages_sports(role):
    client, _ = make_client()
    club = created_club(client, uuid4())
    path = f"{CLUBS_PATH}/{club['id']}/deportes/1"
    assert client.put(path, headers=token(uuid4(), role)).status_code == 403
    assert client.delete(path, headers=token(uuid4(), role)).status_code == 403


def test_create_with_offered_sports():
    client, _ = make_client()
    club = created_club(client, uuid4(), deporte_ids=[2, 1])
    assert [d["nombre"] for d in club["deportes"]] == ["Fútbol", "Tenis"]


def test_edit_replaces_offered_sports_together_with_the_profile():
    client, _ = make_client()
    owner = uuid4()
    club = created_club(client, owner, deporte_ids=[1])
    path = f"{CLUBS_PATH}/{club['id']}"
    edited = client.patch(
        path,
        headers=token(owner),
        json={
            "logo": "https://cdn.example.com/nuevo-logo.png",
            "descripcion": "Ahora también tenis",
            "redes_sociales": {"instagram": "https://instagram.com/club", "facebook": "https://facebook.com/club"},
            "sitio_web": "https://club.cl",
            "telefono": "+56 9 8765 4321",
            "email_contacto": "hola@club.cl",
            "deporte_ids": [2],
        },
    ).json()
    assert edited["logo"] == "https://cdn.example.com/nuevo-logo.png"
    assert edited["descripcion"] == "Ahora también tenis"
    assert edited["redes_sociales"] == {
        "instagram": "https://instagram.com/club", "facebook": "https://facebook.com/club",
    }
    assert (edited["sitio_web"], edited["telefono"], edited["email_contacto"]) == (
        "https://club.cl", "+56 9 8765 4321", "hola@club.cl",
    )
    assert [d["id"] for d in edited["deportes"]] == [2]
    public = client.get(path).json()
    assert public["logo"] == edited["logo"] and [d["id"] for d in public["deportes"]] == [2]

    only_sports = client.patch(path, headers=token(owner), json={"deporte_ids": []}).json()
    assert only_sports["deportes"] == [] and only_sports["descripcion"] == "Ahora también tenis"


def test_sports_not_sent_are_left_as_they_are():
    client, _ = make_client()
    owner = uuid4()
    club = created_club(client, owner, deporte_ids=[1, 2])
    edited = client.patch(f"{CLUBS_PATH}/{club['id']}", headers=token(owner), json={"descripcion": "x"})
    assert [d["id"] for d in edited.json()["deportes"]] == [1, 2]


@pytest.mark.parametrize(
    "deporte_ids",
    [[3], [99], [1, 99], [1, 1], [0], ["futbol"], None, list(range(1, 32))],
)
def test_invalid_offered_sports_are_rejected_with_400(deporte_ids):
    client, _ = make_client()
    owner = uuid4()
    club = created_club(client, owner, deporte_ids=[1])
    response = client.patch(f"{CLUBS_PATH}/{club['id']}", headers=token(owner), json={"deporte_ids": deporte_ids})
    assert response.status_code == 400
    assert [d["id"] for d in client.get(f"{CLUBS_PATH}/me", headers=token(owner)).json()["deportes"]] == [1]


def test_invalid_sports_on_creation_create_nothing():
    client, _ = make_client()
    owner = uuid4()
    response = client.post(CLUBS_PATH, headers=token(owner), json={"nombre": "Club", "deporte_ids": [99]})
    assert response.status_code == 400
    assert client.get(f"{CLUBS_PATH}/me", headers=token(owner)).status_code == 404


# -- authentication -------------------------------------------------------------


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Basic abc"},
        {"Authorization": "Bearer not-a-jwt"},
        token(uuid4(), exp=datetime.now(timezone.utc) - timedelta(minutes=1)),
        token(uuid4(), token_type="refresh"),
        token(uuid4(), aud="otra-app"),
        token(uuid4(), roles="club_admin"),
    ],
)
def test_management_routes_require_a_valid_access_token(headers):
    client, _ = make_client()
    owner = uuid4()
    club = created_club(client, owner)
    attempts = [
        client.post(CLUBS_PATH, headers=headers, json={"nombre": "Club"}),
        client.get(f"{CLUBS_PATH}/me", headers=headers),
        client.patch(f"{CLUBS_PATH}/{club['id']}", headers=headers, json={"descripcion": "x"}),
        client.put(f"{CLUBS_PATH}/{club['id']}/deportes/1", headers=headers),
        client.delete(f"{CLUBS_PATH}/{club['id']}/deportes/1", headers=headers),
    ]
    assert [response.status_code for response in attempts] == [401] * 5


def test_token_signed_with_another_secret_is_rejected():
    client, _ = make_client()
    forged = jwt.encode(
        {
            "sub": str(uuid4()), "iss": "sportmatch-auth", "aud": "sportmatch-mobile",
            "iat": datetime.now(timezone.utc),
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
            "token_type": "access", "roles": ["admin"],
        },
        "another-secret-that-is-also-long-enough",
        algorithm="HS256",
    )
    response = client.get(f"{CLUBS_PATH}/me", headers={"Authorization": f"Bearer {forged}"})
    assert response.status_code == 401
