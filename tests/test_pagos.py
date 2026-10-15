from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from test_club_admin import token  # also sets JWT_SECRET before the app is imported

from app.core.config import WebpaySettings
from app.main import create_app
from app.repositories.base import ClubSport, StoredPlan
from app.repositories.mock_club_repository import MockClubRepository
from app.services.webpay import MockWebpay, WebpayCommit, WebpayError

CLUBS_PATH = "/api/v1/clubs"
PAY = "/api/v1/payments"
SETTINGS = WebpaySettings(
    mock_mode=True,
    environment="INTEGRATION",
    commerce_code=None,
    public_url="http://svc.test",
    frontend_return_url="http://app.test/pago/resultado",
)

PLAN_USUARIO = StoredPlan(1, "Deportista Mensual", "USUARIO", 3990, 30, "Para deportistas")
PLAN_USUARIO_ANUAL = StoredPlan(2, "Deportista Anual", "USUARIO", 39990, 365)
PLAN_CLUB = StoredPlan(3, "Club Mensual", "CLUB", 14990, 30)
PLAN_INACTIVO = StoredPlan(4, "Plan retirado", "USUARIO", 1000, 30, is_active=False)


class Pagos:
    def __init__(self, webpay: MockWebpay | None = None) -> None:
        self.repository = MockClubRepository(
            sports=[ClubSport(1, "Fútbol")],
            planes=[PLAN_USUARIO, PLAN_USUARIO_ANUAL, PLAN_CLUB, PLAN_INACTIVO],
        )
        self.webpay = webpay or MockWebpay(SETTINGS.public_url)
        self.client = TestClient(
            create_app(repository=self.repository, webpay=self.webpay, webpay_settings=SETTINGS)
        )

    def athlete(self) -> tuple[UUID, dict]:
        athlete_id = uuid4()
        return athlete_id, token(athlete_id, "player")

    def club_admin(self, with_club: bool = True) -> tuple[UUID, dict, UUID | None]:
        owner = uuid4()
        headers = token(owner)
        club_id = None
        if with_club:
            response = self.client.post(CLUBS_PATH, headers=headers, json={"nombre": "Club Ñuñoa"})
            assert response.status_code == 201, response.text
            club_id = UUID(response.json()["id"])
        return owner, headers, club_id

    def initiate(self, headers: dict, plan_id: int, **extra: object):
        return self.client.post(f"{PAY}/webpay/initiate", headers=headers, json={"plan_id": plan_id, **extra})

    def commit(self, headers: dict, **body: object):
        return self.client.post(f"{PAY}/webpay/commit", headers=headers, json=body)

    def started(self, headers: dict, plan_id: int) -> dict:
        response = self.initiate(headers, plan_id)
        assert response.status_code == 201, response.text
        return response.json()

    def pay(self, headers: dict, plan_id: int, accion: str = "aprobar"):
        """The whole flow: initiate, the person acts on the Webpay page, commit."""
        started = self.started(headers, plan_id)
        self.webpay.decide(started["token_ws"], accion)
        return started, self.commit(headers, token_ws=started["token_ws"])

    def subscription(self, headers: dict):
        return self.client.get(f"{PAY}/subscription/me", headers=headers)


# -- plans ------------------------------------------------------------------------------------


def test_plans_are_public_active_only_and_cheapest_first():
    pagos = Pagos()
    response = pagos.client.get(f"{PAY}/plans")
    assert response.status_code == 200
    assert [(p["id"], p["precio"]) for p in response.json()] == [(1, 3990), (3, 14990), (2, 39990)]
    assert response.json()[0] == {
        "id": 1, "nombre": "Deportista Mensual", "tipo": "USUARIO",
        "descripcion": "Para deportistas", "precio": 3990, "duracion_dias": 30,
    }
    only_club = pagos.client.get(f"{PAY}/plans", params={"tipo": "CLUB"}).json()
    assert [p["id"] for p in only_club] == [3]
    assert pagos.client.get(f"{PAY}/plans", params={"tipo": "ADMIN"}).status_code == 400


# -- initiating ---------------------------------------------------------------------------------


def test_athlete_starts_a_payment_and_gets_token_and_webpay_url():
    pagos = Pagos()
    athlete_id, headers = pagos.athlete()
    response = pagos.initiate(headers, 1)
    assert response.status_code == 201, response.text
    started = response.json()
    assert started["status"] == "PENDING" and started["amount"] == 3990
    assert started["plan"]["id"] == 1
    assert started["url"] == "http://svc.test/api/v1/payments/webpay/mock"
    assert len(started["token_ws"]) == 64
    assert started["buy_order"].startswith("SM") and len(started["buy_order"]) <= 26
    assert started["session_id"] == f"user:{athlete_id}" and len(started["session_id"]) <= 61

    stored = pagos.repository.get_transaccion(token_ws=started["token_ws"])
    assert stored.status == "PENDING" and stored.usuario_id == athlete_id and stored.club_id is None
    assert stored.iniciada_por == athlete_id and stored.amount == 3990
    assert pagos.subscription(headers).status_code == 404  # nothing activated yet


def test_every_payment_gets_its_own_order_and_token():
    pagos = Pagos()
    _, headers = pagos.athlete()
    first, second = pagos.started(headers, 1), pagos.started(headers, 1)
    assert first["buy_order"] != second["buy_order"] and first["token_ws"] != second["token_ws"]


def test_the_amount_and_the_buyer_cannot_be_chosen_by_the_client():
    pagos = Pagos()
    _, headers = pagos.athlete()
    for extra in ({"amount": 1}, {"usuario_id": str(uuid4())}, {"club_id": str(uuid4())}, {"status": "APPROVED"}):
        assert pagos.initiate(headers, 1, **extra).status_code == 400


def test_unknown_or_retired_plan_is_404():
    pagos = Pagos()
    _, headers = pagos.athlete()
    for plan_id in (99, 4):
        response = pagos.initiate(headers, plan_id)
        assert response.status_code == 404 and response.json() == {"detail": "Plan no encontrado."}
    assert pagos.initiate(headers, 0).status_code == 400
    assert pagos.client.post(f"{PAY}/webpay/initiate", headers=headers, json={}).status_code == 400


def test_plan_type_decides_who_can_buy_it():
    pagos = Pagos()
    _, athlete = pagos.athlete()
    _, club_headers, _ = pagos.club_admin()
    assert pagos.initiate(athlete, 3).status_code == 403  # club plan, athlete
    assert pagos.initiate(club_headers, 1).status_code == 403  # athlete plan, representative
    assert pagos.initiate(token(uuid4(), "admin"), 1).status_code == 403
    assert pagos.initiate(token(uuid4(), "admin"), 3).status_code == 403


def test_representative_without_a_club_cannot_buy_a_club_plan():
    pagos = Pagos()
    _, headers, _ = pagos.club_admin(with_club=False)
    response = pagos.initiate(headers, 3)
    assert response.status_code == 409 and response.json() == {"detail": "Primero crea el perfil de tu club."}


def test_club_plan_is_bought_for_the_representatives_own_club():
    pagos = Pagos()
    owner, headers, club_id = pagos.club_admin()
    started = pagos.started(headers, 3)
    assert started["session_id"] == f"club:{club_id}"
    stored = pagos.repository.get_transaccion(token_ws=started["token_ws"])
    assert stored.club_id == club_id and stored.usuario_id is None and stored.iniciada_por == owner


def test_payment_routes_require_a_token():
    pagos = Pagos()
    assert pagos.client.post(f"{PAY}/webpay/initiate", json={"plan_id": 1}).status_code == 401
    assert pagos.client.post(f"{PAY}/webpay/commit", json={"token_ws": "a" * 64}).status_code == 401
    assert pagos.client.get(f"{PAY}/subscription/me").status_code == 401


def test_a_failing_gateway_is_a_502_and_leaves_no_transaction():
    class Down(MockWebpay):
        def create_transaction(self, *args, **kwargs):
            raise WebpayError("Webpay could not be reached (ConnectTimeout)")

    pagos = Pagos(Down(SETTINGS.public_url))
    athlete_id, headers = pagos.athlete()
    response = pagos.initiate(headers, 1)
    assert response.status_code == 502
    assert response.json() == {"detail": "No se pudo iniciar el pago con Webpay. Inténtalo de nuevo."}
    assert pagos.repository._transacciones == {}  # noqa: SLF001


# -- approved payments: the benefits ----------------------------------------------------------------


def test_approved_payment_activates_the_subscription_for_the_plan_duration():
    pagos = Pagos()
    athlete_id, headers = pagos.athlete()
    started, response = pagos.pay(headers, 1)
    assert response.status_code == 200, response.text
    receipt = response.json()
    assert receipt["status"] == "APPROVED" and receipt["transaccion_id"] == started["transaccion_id"]
    assert receipt["buy_order"] == started["buy_order"] and receipt["amount"] == 3990
    assert receipt["plan"]["nombre"] == "Deportista Mensual"
    assert len(receipt["authorization_code"]) == 6 and receipt["card_last4"] == "6623"
    assert receipt["payment_type_code"] == "VN"

    subscription = receipt["suscripcion"]
    assert subscription["estado"] == "ACTIVE" and subscription["club_id"] is None
    start, end = datetime.fromisoformat(subscription["fecha_inicio"]), datetime.fromisoformat(subscription["fecha_fin"])
    assert end - start == timedelta(days=30)
    assert abs(start - datetime.now(timezone.utc)) < timedelta(minutes=1)
    assert pagos.subscription(headers).json() == subscription


def test_approved_club_payment_activates_the_clubs_subscription():
    pagos = Pagos()
    _, headers, club_id = pagos.club_admin()
    _, response = pagos.pay(headers, 3)
    assert response.status_code == 200
    assert response.json()["suscripcion"]["club_id"] == str(club_id)
    assert pagos.subscription(headers).json()["plan"]["id"] == 3
    assert pagos.repository.get_suscripcion(club_id=club_id).estado == "ACTIVE"


def test_renewing_an_active_subscription_adds_days_to_its_end():
    pagos = Pagos()
    _, headers = pagos.athlete()
    first = pagos.pay(headers, 1)[1].json()["suscripcion"]
    second = pagos.pay(headers, 1)[1].json()["suscripcion"]
    assert second["id"] == first["id"]  # one subscription per buyer
    assert second["fecha_inicio"] == first["fecha_inicio"]
    assert datetime.fromisoformat(second["fecha_fin"]) - datetime.fromisoformat(first["fecha_fin"]) == timedelta(days=30)


def test_paying_after_it_expired_starts_from_today():
    pagos = Pagos()
    athlete_id, headers = pagos.athlete()
    pagos.pay(headers, 1)
    pagos.repository.set_suscripcion_fin(usuario_id=athlete_id, fecha_fin=datetime.now(timezone.utc) - timedelta(days=10))
    assert pagos.subscription(headers).json()["estado"] == "EXPIRED"
    renewed = pagos.pay(headers, 1)[1].json()["suscripcion"]
    assert renewed["estado"] == "ACTIVE"
    assert abs(datetime.fromisoformat(renewed["fecha_inicio"]) - datetime.now(timezone.utc)) < timedelta(minutes=1)
    assert datetime.fromisoformat(renewed["fecha_fin"]) - datetime.fromisoformat(renewed["fecha_inicio"]) == timedelta(days=30)


def test_another_plan_while_one_is_active_is_refused_before_charging_anything():
    pagos = Pagos()
    athlete_id, headers = pagos.athlete()
    pagos.pay(headers, 1)
    response = pagos.initiate(headers, 2)
    assert response.status_code == 409
    assert response.json() == {
        "detail": "Ya tienes una suscripción activa de otro plan. Podrás cambiar de plan cuando termine."
    }
    pagos.repository.set_suscripcion_fin(usuario_id=athlete_id, fecha_fin=datetime.now(timezone.utc) - timedelta(days=1))
    switched = pagos.pay(headers, 2)[1].json()["suscripcion"]
    assert switched["plan"]["id"] == 2 and switched["estado"] == "ACTIVE"


def test_confirming_twice_returns_the_same_receipt_and_extends_nothing_again():
    pagos = Pagos()
    _, headers = pagos.athlete()
    started, first = pagos.pay(headers, 1)
    again = pagos.commit(headers, token_ws=started["token_ws"])  # Webpay's strict mock would refuse a 2nd commit
    assert again.status_code == 200 and again.json() == first.json()
    assert pagos.subscription(headers).json()["fecha_fin"] == first.json()["suscripcion"]["fecha_fin"]


def test_confirming_again_does_not_call_webpay_again():
    class Counting(MockWebpay):
        commits = 0

        def commit_transaction(self, token_ws):
            Counting.commits += 1
            return super().commit_transaction(token_ws)

    pagos = Pagos(Counting(SETTINGS.public_url))
    _, headers = pagos.athlete()
    started, first = pagos.pay(headers, 1)
    for _ in range(3):
        assert pagos.commit(headers, token_ws=started["token_ws"]).json() == first.json()
    assert Counting.commits == 1


def test_overlapping_confirmations_extend_the_subscription_only_once():
    """Two requests pass the PENDING check together and Webpay (unlike the strict
    mock) answers approved to both: the second settle must change nothing."""

    class Lenient(MockWebpay):
        repository: MockClubRepository

        def commit_transaction(self, token_ws):
            real = super().commit_transaction(token_ws)
            # the other request settles the payment first, with the same answer
            transaccion = self.repository.get_transaccion(token_ws=token_ws)
            self.repository.settle_transaccion(transaccion.id, "APPROVED", {"response_code": 0})
            return real

    webpay = Lenient(SETTINGS.public_url)
    pagos = Pagos(webpay)
    webpay.repository = pagos.repository
    athlete_id, headers = pagos.athlete()
    _, response = pagos.pay(headers, 1)
    assert response.status_code == 200
    subscription = response.json()["suscripcion"]
    assert datetime.fromisoformat(subscription["fecha_fin"]) - datetime.fromisoformat(subscription["fecha_inicio"]) == timedelta(days=30)


def test_settling_the_same_payment_twice_changes_it_only_once():
    pagos = Pagos()
    athlete_id, headers = pagos.athlete()
    started = pagos.started(headers, 1)
    transaccion = pagos.repository.get_transaccion(token_ws=started["token_ws"])
    first, changed_first, subscription = pagos.repository.settle_transaccion(transaccion.id, "APPROVED", {"response_code": 0})
    second, changed_second, _ = pagos.repository.settle_transaccion(transaccion.id, "APPROVED", {"response_code": 0})
    assert (changed_first, changed_second) == (True, False)
    assert pagos.repository.get_suscripcion(usuario_id=athlete_id).fecha_fin == subscription.fecha_fin
    # a late "rejected" answer cannot undo an approved payment
    third, changed_third, _ = pagos.repository.settle_transaccion(transaccion.id, "REJECTED", None)
    assert changed_third is False and third.status == "APPROVED"


# -- rejected, cancelled and failing payments ----------------------------------------------------------


def test_rejected_payment_is_a_400_with_receipt_and_leaves_the_subscription_alone():
    pagos = Pagos()
    athlete_id, headers = pagos.athlete()
    pagos.pay(headers, 1)
    before = pagos.subscription(headers).json()
    started, response = pagos.pay(headers, 1, "rechazar")
    assert response.status_code == 400
    body = response.json()
    assert body["detail"] == "El pago fue rechazado. No se realizó ningún cobro y tu suscripción no cambió."
    assert body["transaccion"]["status"] == "REJECTED" and body["transaccion"]["suscripcion"] is None
    assert body["transaccion"]["authorization_code"] is None
    assert pagos.subscription(headers).json() == before
    stored = pagos.repository.get_transaccion(token_ws=started["token_ws"])
    assert stored.status == "REJECTED" and stored.response_code == -1
    # confirming again gives the same answer
    assert pagos.commit(headers, token_ws=started["token_ws"]).json() == body


def test_a_rejected_first_payment_creates_no_subscription():
    pagos = Pagos()
    _, headers = pagos.athlete()
    assert pagos.pay(headers, 1, "rechazar")[1].status_code == 400
    assert pagos.subscription(headers).status_code == 404


def test_user_cancelling_on_the_webpay_form_is_a_cancelled_payment():
    pagos = Pagos()
    _, headers = pagos.athlete()
    started = pagos.started(headers, 1)
    pagos.webpay.decide(started["token_ws"], "cancelar")
    response = pagos.commit(
        headers, tbk_token=started["token_ws"], tbk_orden_compra=started["buy_order"], tbk_id_sesion=started["session_id"]
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "El pago fue cancelado. No se realizó ningún cobro y tu suscripción no cambió."
    assert response.json()["transaccion"]["status"] == "CANCELLED"
    assert pagos.subscription(headers).status_code == 404


def test_a_timed_out_payment_form_is_cancelled_by_its_buy_order():
    pagos = Pagos()
    _, headers = pagos.athlete()
    started = pagos.started(headers, 1)
    response = pagos.commit(headers, tbk_orden_compra=started["buy_order"], tbk_id_sesion=started["session_id"])
    assert response.status_code == 400 and response.json()["transaccion"]["status"] == "CANCELLED"


def test_tbk_parameters_win_over_token_ws():
    pagos = Pagos()
    _, headers = pagos.athlete()
    started = pagos.started(headers, 1)
    pagos.webpay.decide(started["token_ws"], "aprobar")  # even if it could be committed...
    response = pagos.commit(headers, token_ws=started["token_ws"], tbk_token=started["token_ws"], tbk_orden_compra=started["buy_order"])
    assert response.status_code == 400 and response.json()["transaccion"]["status"] == "CANCELLED"
    assert pagos.subscription(headers).status_code == 404


def test_tbk_parameters_of_another_order_do_not_cancel_this_one():
    pagos = Pagos()
    _, headers = pagos.athlete()
    started = pagos.started(headers, 1)
    other = pagos.started(headers, 1)
    response = pagos.commit(headers, tbk_token=started["token_ws"], tbk_orden_compra=other["buy_order"])
    assert response.status_code == 404
    assert pagos.repository.get_transaccion(token_ws=started["token_ws"]).status == "PENDING"


def test_confirming_before_the_person_paid_is_a_502_and_the_payment_stays_pending():
    pagos = Pagos()
    _, headers = pagos.athlete()
    started = pagos.started(headers, 1)
    response = pagos.commit(headers, token_ws=started["token_ws"])
    assert response.status_code == 502
    assert response.json() == {"detail": "No se pudo confirmar el pago con Webpay. Inténtalo de nuevo."}
    assert pagos.repository.get_transaccion(token_ws=started["token_ws"]).status == "PENDING"
    pagos.webpay.decide(started["token_ws"], "aprobar")
    assert pagos.commit(headers, token_ws=started["token_ws"]).status_code == 200  # retry works


def test_an_answer_that_does_not_match_the_order_never_activates_benefits():
    class Wrong(MockWebpay):
        def commit_transaction(self, token_ws):
            real = super().commit_transaction(token_ws)
            return WebpayCommit(**{**real.__dict__, "amount": 1})  # Webpay says CLP 1

    pagos = Pagos(Wrong(SETTINGS.public_url))
    _, headers = pagos.athlete()
    started, response = pagos.pay(headers, 1)
    assert response.status_code == 502
    assert response.json() == {"detail": "La confirmación de Webpay no coincide con la orden de compra."}
    assert pagos.repository.get_transaccion(token_ws=started["token_ws"]).status == "PENDING"
    assert pagos.subscription(headers).status_code == 404


def test_a_concurrent_confirmation_that_already_settled_it_wins():
    class Racy(MockWebpay):
        """Another request settles the payment while this one is at Webpay."""

        repository: MockClubRepository

        def commit_transaction(self, token_ws):
            transaccion = self.repository.get_transaccion(token_ws=token_ws)
            self.repository.settle_transaccion(transaccion.id, "APPROVED", {"response_code": 0})
            raise WebpayError("Transaction already locked by another process")

    webpay = Racy(SETTINGS.public_url)
    pagos = Pagos(webpay)
    webpay.repository = pagos.repository
    _, headers = pagos.athlete()
    started, response = pagos.pay(headers, 1)
    assert response.status_code == 200 and response.json()["status"] == "APPROVED"


# -- who can confirm ---------------------------------------------------------------------------------


def test_only_the_account_that_started_a_payment_can_confirm_it():
    pagos = Pagos()
    _, owner_headers = pagos.athlete()
    _, other_headers = pagos.athlete()
    started = pagos.started(owner_headers, 1)
    pagos.webpay.decide(started["token_ws"], "aprobar")
    for headers in (other_headers, token(uuid4(), "admin")):
        response = pagos.commit(headers, token_ws=started["token_ws"])
        assert response.status_code == 404 and response.json() == {"detail": "Transacción no encontrada."}
    assert pagos.repository.get_transaccion(token_ws=started["token_ws"]).status == "PENDING"
    assert pagos.commit(owner_headers, token_ws=started["token_ws"]).status_code == 200


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"tbk_id_sesion": "user:1"},
        {"token_ws": "con espacios"},
        {"token_ws": "a" * 65},
        {"token_ws": "abc'; DROP TABLE x;--"},
        {"tbk_orden_compra": "x" * 27},
        {"estado": "APPROVED", "token_ws": "a" * 64},
    ],
)
def test_malformed_confirmations_are_400(body):
    pagos = Pagos()
    _, headers = pagos.athlete()
    assert pagos.commit(headers, **body).status_code == 400


def test_unknown_token_is_404():
    pagos = Pagos()
    _, headers = pagos.athlete()
    assert pagos.commit(headers, token_ws="f" * 64).status_code == 404
    assert pagos.commit(headers, tbk_orden_compra="SMdesconocida").status_code == 404


# -- subscription ----------------------------------------------------------------------------------------


def test_subscription_me_by_role():
    pagos = Pagos()
    _, athlete = pagos.athlete()
    _, club_headers, _ = pagos.club_admin()
    _, no_club, _ = pagos.club_admin(with_club=False)
    assert pagos.subscription(athlete).json() == {"detail": "No tienes una suscripción."}
    assert pagos.subscription(no_club).json() == {"detail": "Aún no tienes un club."}
    assert pagos.subscription(token(uuid4(), "admin")).status_code == 403
    pagos.pay(athlete, 1)
    pagos.pay(club_headers, 3)
    assert pagos.subscription(athlete).json()["plan"]["id"] == 1
    assert pagos.subscription(club_headers).json()["plan"]["id"] == 3


# -- the browser's side: return bridge and simulated page -------------------------------------------------------


def test_return_bridge_sends_the_user_to_the_app_with_the_token():
    pagos = Pagos()
    token_ws = "a" * 64
    for response in (
        pagos.client.get(f"{PAY}/webpay/return", params={"token_ws": token_ws}, follow_redirects=False),
        pagos.client.post(f"{PAY}/webpay/return", data={"token_ws": token_ws}, follow_redirects=False),
    ):
        assert response.status_code == 303
        assert response.headers["location"] == f"http://app.test/pago/resultado?token_ws={token_ws}"


def test_return_bridge_maps_the_cancel_parameters():
    pagos = Pagos()
    response = pagos.client.get(
        f"{PAY}/webpay/return",
        params={"TBK_TOKEN": "b" * 64, "TBK_ORDEN_COMPRA": "SMabc", "TBK_ID_SESION": "user:1"},
        follow_redirects=False,
    )
    location = response.headers["location"]
    assert location.startswith("http://app.test/pago/resultado?")
    for part in (f"tbk_token={'b' * 64}", "tbk_orden_compra=SMabc", "tbk_id_sesion=user%3A1"):
        assert part in location


def test_return_bridge_drops_malformed_values_and_keeps_an_existing_query():
    settings = WebpaySettings(
        mock_mode=True, environment="INTEGRATION", commerce_code=None,
        public_url="http://svc.test", frontend_return_url="sportmatch://pago?src=webpay",
    )
    app = create_app(repository=MockClubRepository(), webpay=MockWebpay(settings.public_url), webpay_settings=settings)
    client = TestClient(app)
    bridge = f"{PAY}/webpay/return"
    nothing = client.get(bridge, params={"token_ws": "<script>alert(1)</script>", "otro": "x"}, follow_redirects=False)
    assert nothing.headers["location"] == "sportmatch://pago?src=webpay&error=parametros"
    some = client.get(bridge, params={"token_ws": "c" * 64, "TBK_ORDEN_COMPRA": "no válido!"}, follow_redirects=False)
    assert some.headers["location"] == f"sportmatch://pago?src=webpay&token_ws={'c' * 64}"
    assert client.get(bridge, follow_redirects=False).headers["location"].endswith("&error=parametros")


def test_the_whole_browser_flow_with_the_simulated_page():
    pagos = Pagos()
    _, headers = pagos.athlete()
    started = pagos.started(headers, 1)
    page = pagos.client.post(f"{PAY}/webpay/mock", data={"token_ws": started["token_ws"]})
    assert page.status_code == 200 and "Simulación de Webpay Plus" in page.text
    assert started["buy_order"] in page.text and "$3.990" in page.text

    pay = pagos.client.get(
        f"{PAY}/webpay/mock/decision", params={"token_ws": started["token_ws"], "accion": "aprobar"}, follow_redirects=False
    )
    assert pay.status_code == 303
    assert pay.headers["location"] == f"http://svc.test{PAY}/webpay/return?token_ws={started['token_ws']}"
    bridge = pagos.client.get(pay.headers["location"].replace("http://svc.test", ""), follow_redirects=False)
    assert bridge.headers["location"] == f"http://app.test/pago/resultado?token_ws={started['token_ws']}"
    assert pagos.commit(headers, token_ws=started["token_ws"]).status_code == 200


def test_the_simulated_page_rejects_unknown_tokens_and_actions():
    pagos = Pagos()
    _, headers = pagos.athlete()
    started = pagos.started(headers, 1)
    assert pagos.client.get(f"{PAY}/webpay/mock", params={"token_ws": "f" * 64}).status_code == 404
    assert pagos.client.get(f"{PAY}/webpay/mock", params={"token_ws": "<b>x</b>"}).status_code == 404
    assert pagos.client.get(f"{PAY}/webpay/mock").status_code == 404
    decision = f"{PAY}/webpay/mock/decision"
    assert pagos.client.get(decision, params={"token_ws": started["token_ws"], "accion": "regalar"}, follow_redirects=False).status_code == 404
    assert pagos.client.get(decision, params={"token_ws": "f" * 64, "accion": "aprobar"}, follow_redirects=False).status_code == 404


def test_the_simulated_pages_do_not_exist_when_the_real_gateway_is_used():
    class Real:  # stands in for TransbankWebpay
        def close(self): ...

    app = create_app(repository=MockClubRepository(planes=[PLAN_USUARIO]), webpay=Real(), webpay_settings=SETTINGS)
    client = TestClient(app)
    assert client.get(f"{PAY}/webpay/mock", params={"token_ws": "a" * 64}).status_code == 404
    assert client.get(f"{PAY}/webpay/mock/decision", params={"token_ws": "a" * 64, "accion": "aprobar"}, follow_redirects=False).status_code == 404
    assert client.get(f"{PAY}/plans").status_code == 200  # the rest keeps working


def test_without_webpay_configured_payments_are_unavailable_but_the_rest_works():
    client = TestClient(create_app(repository=MockClubRepository(planes=[PLAN_USUARIO])))
    response = client.get(f"{PAY}/plans")
    assert response.status_code == 503 and response.json() == {"detail": "Los pagos no están disponibles."}
    assert client.get(CLUBS_PATH).status_code == 200
