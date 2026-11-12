"""Defecto 2 de la verificación de HU-61: el club veía la foto que el deportista tenía al postular (el archivo se borra al
reemplazarla). Ahora ve siempre la foto vigente, pedida a Ms_Users por lote; la copia guardada no se usa."""
from __future__ import annotations

import json
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from test_revision_postulaciones import CALLS_PATH, Review

from app.main import create_app
from app.services.account_photos import InternalAccountPhotos

KEY = "0123456789abcdef0123456789abcdef"
NUEVA = f"/api/v1/users/photos/{KEY}.jpg"
OTRA = "/api/v1/users/photos/fedcba9876543210fedcba9876543210.png"


class FakePhotos:
    """Ms_Users stand-in: the photo each account has NOW (none = no entry)."""

    def __init__(self) -> None:
        self.now: dict[UUID, str] = {}
        self.calls: list[list[UUID]] = []
        self.error: Exception | None = None

    def photos(self, ids: list[UUID]) -> dict[UUID, str]:
        self.calls.append(list(ids))
        if self.error is not None:
            raise self.error
        return {i: self.now[i] for i in ids if i in self.now}


class Fotos(Review):
    def __init__(self) -> None:
        super().__init__(cupos=10)
        self.photos = FakePhotos()
        self.client = TestClient(create_app(repository=self.repository, users=self.users, photos=self.photos))


def test_the_club_sees_the_current_photo_not_the_one_saved_when_applying():
    setup = Fotos()
    ana_id, _, ana = setup.applied(nombre="Ana")
    assert ana["perfil_deportivo"]["foto_perfil"] == "https://cdn.example.com/ana.jpg"       # la copia del día que postuló
    setup.photos.now[ana_id] = NUEVA
    [item] = setup.candidates().json()["items"]
    assert item["perfil_deportivo"]["foto_perfil"] == NUEVA


def test_a_person_who_removed_their_photo_has_none():
    setup = Fotos()
    _, _, _ = setup.applied(nombre="Ana")                  # Ms_Users no devuelve foto para ella
    [item] = setup.candidates().json()["items"]
    assert item["perfil_deportivo"]["foto_perfil"] is None


def test_one_single_request_for_the_whole_page():
    setup = Fotos()
    ids = []
    for nombre in ("Ana", "Bea", "Carla", "Dani"):
        athlete_id, _, _ = setup.applied(nombre=nombre)
        ids.append(athlete_id)
    setup.photos.now.update({ids[0]: NUEVA, ids[2]: OTRA})
    items = setup.candidates().json()["items"]
    assert len(setup.photos.calls) == 1 and sorted(setup.photos.calls[0]) == sorted(ids)
    assert [i["perfil_deportivo"]["foto_perfil"] for i in items] == [NUEVA, None, OTRA, None]


def test_an_empty_list_does_not_call_users():
    setup = Fotos()
    assert setup.candidates().json()["items"] == []
    assert setup.photos.calls == []


@pytest.mark.parametrize("error", [RuntimeError("Ms_Users down"), httpx.ConnectError("refused"), httpx.ReadTimeout("slow")])
def test_if_users_does_not_answer_the_list_still_goes_out_without_photos(error):
    setup = Fotos()
    setup.applied(nombre="Ana"); setup.applied(nombre="Bea")
    setup.photos.error = error
    response = setup.candidates()
    assert response.status_code == 200, response.text
    page = response.json()
    assert page["total"] == 2 and all(i["perfil_deportivo"]["foto_perfil"] is None for i in page["items"])
    assert all(i["perfil_deportivo"]["nombre"] for i in page["items"])           # el resto del perfil sigue igual


def test_without_a_photos_source_the_candidates_have_no_photo_and_never_the_old_copy():
    setup = Review(cupos=5)                                                       # sin `photos`
    setup.applied(nombre="Ana")
    [item] = setup.candidates().json()["items"]
    assert item["perfil_deportivo"]["foto_perfil"] is None


def test_reviewing_a_candidate_also_answers_with_the_current_photo():
    setup = Fotos()
    ana_id, _, ana = setup.applied(nombre="Ana")
    setup.photos.now[ana_id] = NUEVA
    response = setup.review(ana["id"], {"estado": "PRESELECCIONADO"})
    assert response.status_code == 200, response.text
    assert response.json()["perfil_deportivo"]["foto_perfil"] == NUEVA


def test_the_saved_copy_is_not_changed_and_the_athlete_still_sees_their_own_data():
    setup = Fotos()
    ana_id, headers, ana = setup.applied(nombre="Ana")
    setup.photos.now[ana_id] = NUEVA
    setup.candidates()
    assert setup.repository.get_postulacion(UUID(ana["id"])).perfil_deportivo["foto_perfil"] == "https://cdn.example.com/ana.jpg"


# -- el cliente HTTP hacia Ms_Users ------------------------------------------------------------------------------------

TOKEN = "service-to-service-secret-with-32-bytes-min"


def client_for(handler, token=TOKEN):
    return InternalAccountPhotos(httpx.Client(base_url="http://ms_users:8001", transport=httpx.MockTransport(handler)), token)


def test_the_client_sends_only_the_ids_with_the_service_secret_and_no_user_token():
    seen = []
    a, b = uuid4(), uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"photos": {str(a): NUEVA}})

    result = client_for(handler).photos([a, b, a])
    assert result == {a: NUEVA}
    [request] = seen
    assert request.method == "POST" and request.url.path == "/internal/v1/accounts/photos"
    assert request.headers["x-internal-service-token"] == TOKEN and "authorization" not in request.headers
    assert json.loads(request.content) == {"ids": [str(a), str(b)]}              # sin repetidos y nada más


def test_the_client_ignores_anything_that_is_not_a_photo_of_an_account_it_asked_for():
    a, ajena = uuid4(), uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"photos": {str(a): "https://evil.example.com/x.png", str(ajena): NUEVA, "no-uuid": NUEVA},
                                         "email": "ana@example.com"})

    assert client_for(handler).photos([a]) == {}


def test_the_client_splits_big_lists_in_batches_of_100():
    ids = [uuid4() for _ in range(230)]
    sizes = []

    def handler(request: httpx.Request) -> httpx.Response:
        sizes.append(len(json.loads(request.content)["ids"]))
        return httpx.Response(200, json={"photos": {}})

    client_for(handler).photos(ids)
    assert sizes == [100, 100, 30]


@pytest.mark.parametrize("status", [401, 403, 500, 503])
def test_the_client_raises_when_users_refuses(status):
    with pytest.raises(RuntimeError):
        client_for(lambda request: httpx.Response(status)).photos([uuid4()])


def test_the_client_without_a_secret_does_not_call_users():
    calls = []
    with pytest.raises(RuntimeError):
        client_for(lambda request: calls.append(request) or httpx.Response(200, json={"photos": {}}), token=None).photos([uuid4()])
    assert calls == []
