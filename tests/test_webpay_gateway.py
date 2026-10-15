from __future__ import annotations

import json
from datetime import timezone

import httpx
import pytest

from app.core.config import get_webpay_settings
from app.services.webpay import MockWebpay, TransbankWebpay, WebpayError, build_webpay

COMMERCE_CODE = "597055555532"
API_KEY = "test-secret-key-0123456789"
TOKEN = "e" * 64

# Shape of Transbank's commit response (v1.2), from its API reference.
APPROVED = {
    "vci": "TSY", "amount": 10000, "status": "AUTHORIZED", "buy_order": "SMabc",
    "session_id": "user:1", "card_detail": {"card_number": "6623"}, "accounting_date": "0522",
    "transaction_date": "2026-10-14T16:41:21.063Z", "authorization_code": "1213",
    "payment_type_code": "VN", "response_code": 0, "installments_amount": None,
    "installments_number": 0, "balance": None,
}


def adapter(handler) -> TransbankWebpay:
    client = httpx.Client(
        base_url="https://webpay3gint.transbank.cl", transport=httpx.MockTransport(handler)
    )
    return TransbankWebpay(client, COMMERCE_CODE, API_KEY)


# -- Transbank adapter -----------------------------------------------------------------


def test_create_transaction_sends_the_documented_request():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(
            method=request.method,
            path=request.url.path,
            id=request.headers["Tbk-Api-Key-Id"],
            secret=request.headers["Tbk-Api-Key-Secret"],
            content_type=request.headers["Content-Type"],
            body=json.loads(request.content),
        )
        return httpx.Response(
            200, json={"token": TOKEN, "url": "https://webpay3gint.transbank.cl/webpayserver/initTransaction"}
        )

    created = adapter(handler).create_transaction("SMabc", "user:1", 3990, "https://svc/api/v1/payments/webpay/return")
    assert seen == {
        "method": "POST",
        "path": "/rswebpaytransaction/api/webpay/v1.2/transactions",
        "id": COMMERCE_CODE,
        "secret": API_KEY,
        "content_type": "application/json",
        "body": {
            "buy_order": "SMabc", "session_id": "user:1", "amount": 3990,
            "return_url": "https://svc/api/v1/payments/webpay/return",
        },
    }
    assert created.token == TOKEN and created.url.startswith("https://webpay3gint.transbank.cl/")


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"token": TOKEN},
        {"token": "", "url": "https://webpay3gint.transbank.cl/x"},
        {"token": "e" * 65, "url": "https://webpay3gint.transbank.cl/x"},
        {"token": TOKEN, "url": "http://evil.example/pay"},
        {"token": TOKEN, "url": "javascript:alert(1)"},
        {"token": 123, "url": "https://webpay3gint.transbank.cl/x"},
    ],
)
def test_create_transaction_rejects_unexpected_responses(body):
    with pytest.raises(WebpayError):
        adapter(lambda request: httpx.Response(200, json=body)).create_transaction("SMabc", "s", 1000, "https://r")


def test_commit_transaction_uses_put_with_the_token_in_the_path_and_reads_the_result():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(method=request.method, path=request.url.path)
        return httpx.Response(200, json=APPROVED)

    commit = adapter(handler).commit_transaction(TOKEN)
    assert seen == {"method": "PUT", "path": f"/rswebpaytransaction/api/webpay/v1.2/transactions/{TOKEN}"}
    assert commit.approved is True
    assert (commit.buy_order, commit.session_id, commit.amount) == ("SMabc", "user:1", 10000)
    assert (commit.authorization_code, commit.payment_type_code) == ("1213", "VN")
    assert commit.card_last4 == "6623" and commit.installments_number == 0
    assert commit.transaction_date.tzinfo is not None
    assert commit.transaction_date.astimezone(timezone.utc).hour == 16


@pytest.mark.parametrize(
    ("override", "approved"),
    [
        ({}, True),
        ({"response_code": -1, "status": "FAILED"}, False),
        ({"response_code": -1}, False),
        ({"response_code": 0, "status": "FAILED"}, False),  # Transbank: both must hold
        ({"response_code": 0, "status": "REVERSED"}, False),
        ({"response_code": None}, False),
    ],
)
def test_a_payment_is_approved_only_with_response_code_0_and_status_authorized(override, approved):
    body = {**APPROVED, **override}
    commit = adapter(lambda request: httpx.Response(200, json=body)).commit_transaction(TOKEN)
    assert commit.approved is approved


def test_a_declined_payment_is_a_result_not_an_error():
    declined = {**APPROVED, "status": "FAILED", "response_code": -1, "authorization_code": None,
                "payment_type_code": None, "card_detail": None, "transaction_date": None}
    commit = adapter(lambda request: httpx.Response(200, json=declined)).commit_transaction(TOKEN)
    assert commit.approved is False and commit.authorization_code is None and commit.card_last4 is None


def test_card_digits_are_cut_to_the_last_four():
    body = {**APPROVED, "card_detail": {"card_number": "XXXXXXXXXXXX6623"}}
    assert adapter(lambda request: httpx.Response(200, json=body)).commit_transaction(TOKEN).card_last4 == "6623"


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(422, json={"error_message": "Transaction not found"}),
        httpx.Response(401, json={"error_message": "Not Authorized"}),
        httpx.Response(500, text="boom"),
        httpx.Response(200, text="<html>not json</html>"),
        httpx.Response(200, json=["unexpected"]),
        httpx.Response(200, json={"status": "AUTHORIZED"}),  # fields missing
        httpx.Response(200, json={**APPROVED, "amount": "no es número"}),
        httpx.Response(200, json={**APPROVED, "transaction_date": "ayer"}),
    ],
)
def test_unexpected_answers_are_webpay_errors(response):
    with pytest.raises(WebpayError):
        adapter(lambda request: response).commit_transaction(TOKEN)


def test_network_failures_are_webpay_errors_without_leaking_secrets():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout(f"timeout talking with {API_KEY}", request=request)

    with pytest.raises(WebpayError) as error:
        adapter(handler).commit_transaction(TOKEN)
    assert API_KEY not in str(error.value) and TOKEN not in str(error.value)


def test_errors_never_include_the_token_or_credentials():
    with pytest.raises(WebpayError) as error:
        adapter(lambda request: httpx.Response(422, json={"error_message": "x"})).commit_transaction(TOKEN)
    assert TOKEN not in str(error.value) and API_KEY not in str(error.value)


# -- simulated Webpay --------------------------------------------------------------------


def make_mock() -> MockWebpay:
    return MockWebpay("http://svc.test/")


def test_mock_creates_transactions_like_webpay_does():
    mock = make_mock()
    created = mock.create_transaction("SMabc", "user:1", 3990, "http://svc.test/api/v1/payments/webpay/return")
    assert len(created.token) == 64
    assert created.url == "http://svc.test/api/v1/payments/webpay/mock"
    assert mock.create_transaction("SMabd", "user:1", 3990, "r").token != created.token


@pytest.mark.parametrize(
    ("accion", "approved", "code"), [("aprobar", True, 0), ("rechazar", False, -1)]
)
def test_mock_commit_follows_what_the_person_chose(accion, approved, code):
    mock = make_mock()
    token = mock.create_transaction("SMabc", "user:1", 3990, "http://svc.test/return").token
    back = mock.decide(token, accion)
    assert back == f"http://svc.test/return?token_ws={token}"
    commit = mock.commit_transaction(token)
    assert commit.approved is approved and commit.response_code == code
    assert (commit.buy_order, commit.session_id, commit.amount) == ("SMabc", "user:1", 3990)
    assert (commit.authorization_code is not None) is approved


def test_mock_cancel_returns_the_tbk_parameters_and_cannot_be_committed():
    mock = make_mock()
    token = mock.create_transaction("SMabc", "user:1", 3990, "http://svc.test/return").token
    back = mock.decide(token, "cancelar")
    assert back == f"http://svc.test/return?TBK_TOKEN={token}&TBK_ORDEN_COMPRA=SMabc&TBK_ID_SESION=user%3A1"
    with pytest.raises(WebpayError):
        mock.commit_transaction(token)


def test_mock_requires_the_form_to_be_completed_and_commits_only_once():
    mock = make_mock()
    token = mock.create_transaction("SMabc", "user:1", 3990, "r").token
    with pytest.raises(WebpayError):
        mock.commit_transaction(token)  # the person has not paid yet
    mock.decide(token, "aprobar")
    mock.commit_transaction(token)
    with pytest.raises(WebpayError):
        mock.commit_transaction(token)  # already committed
    with pytest.raises(WebpayError):
        mock.commit_transaction("f" * 64)


def test_mock_ignores_unknown_tokens_and_actions():
    mock = make_mock()
    token = mock.create_transaction("SMabc", "user:1", 3990, "r").token
    assert mock.decide("f" * 64, "aprobar") is None
    assert mock.decide(token, "regalar") is None
    assert mock.describe("f" * 64) is None and mock.describe(token)["amount"] == 3990


# -- settings ------------------------------------------------------------------------------

BASE_ENV = {
    "WEBPAY_PUBLIC_URL": "https://api.sportmatch.example/",
    "WEBPAY_FRONTEND_RETURN_URL": "sportmatch://pago",
}


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in (
        "WEBPAY_MOCK_MODE", "WEBPAY_ENVIRONMENT", "WEBPAY_COMMERCE_CODE", "WEBPAY_API_KEY",
        "WEBPAY_PUBLIC_URL", "WEBPAY_FRONTEND_RETURN_URL", "WEBPAY_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)
    for name, value in BASE_ENV.items():
        monkeypatch.setenv(name, value)
    return monkeypatch


def test_default_is_the_real_gateway_in_integration_with_public_test_credentials(env):
    settings = get_webpay_settings()
    assert settings.mock_mode is False and settings.environment == "INTEGRATION"
    assert settings.commerce_code == COMMERCE_CODE and settings.api_key
    assert settings.return_url == "https://api.sportmatch.example/api/v1/payments/webpay/return"
    assert settings.api_key not in repr(settings)


@pytest.mark.parametrize("value", ["true", "TRUE", "1", "yes", "on"])
def test_mock_mode_can_be_switched_on(env, value):
    env.setenv("WEBPAY_MOCK_MODE", value)
    settings = get_webpay_settings()
    assert settings.mock_mode is True and settings.commerce_code is None


@pytest.mark.parametrize("value", ["", "false", "0", "no", "banana"])
def test_anything_else_keeps_the_real_gateway(env, value):
    env.setenv("WEBPAY_MOCK_MODE", value)
    assert get_webpay_settings().mock_mode is False


def test_mock_mode_is_refused_in_production(env):
    env.setenv("WEBPAY_MOCK_MODE", "true")
    env.setenv("WEBPAY_ENVIRONMENT", "production")
    with pytest.raises(RuntimeError, match="PRODUCTION"):
        get_webpay_settings()


def test_production_needs_its_own_credentials_and_https(env):
    env.setenv("WEBPAY_ENVIRONMENT", "PRODUCTION")
    with pytest.raises(RuntimeError, match="WEBPAY_COMMERCE_CODE"):
        get_webpay_settings()
    env.setenv("WEBPAY_COMMERCE_CODE", "597012345678")
    env.setenv("WEBPAY_API_KEY", "production-secret")
    assert get_webpay_settings().environment == "PRODUCTION"
    env.setenv("WEBPAY_PUBLIC_URL", "http://api.sportmatch.example")
    with pytest.raises(RuntimeError, match="https"):
        get_webpay_settings()


@pytest.mark.parametrize("environment", ["SANDBOX", "prod ", "test"])
def test_unknown_environment_is_refused(env, environment):
    env.setenv("WEBPAY_ENVIRONMENT", environment)
    with pytest.raises(RuntimeError, match="WEBPAY_ENVIRONMENT"):
        get_webpay_settings()


@pytest.mark.parametrize("url", ["", "api.sportmatch.example", "ftp://x", "javascript:alert(1)"])
def test_public_url_must_be_an_http_url(env, url):
    env.setenv("WEBPAY_PUBLIC_URL", url)
    with pytest.raises(RuntimeError, match="WEBPAY_PUBLIC_URL"):
        get_webpay_settings()


@pytest.mark.parametrize("url", ["", "pago", "javascript://x", "data://x", "/pago"])
def test_frontend_return_url_must_be_a_page_or_deep_link(env, url):
    env.setenv("WEBPAY_FRONTEND_RETURN_URL", url)
    with pytest.raises(RuntimeError, match="WEBPAY_FRONTEND_RETURN_URL"):
        get_webpay_settings()


def test_deep_links_and_web_pages_are_accepted(env):
    for url in ("sportmatch://pago", "https://app.sportmatch.example/pago/resultado?x=1"):
        env.setenv("WEBPAY_FRONTEND_RETURN_URL", url)
        assert get_webpay_settings().frontend_return_url == url


@pytest.mark.parametrize("timeout", ["0", "-3", "diez"])
def test_timeout_must_be_a_positive_number(env, timeout):
    env.setenv("WEBPAY_TIMEOUT_SECONDS", timeout)
    with pytest.raises(RuntimeError, match="WEBPAY_TIMEOUT_SECONDS"):
        get_webpay_settings()


def test_build_webpay_picks_the_adapter_and_the_transbank_host(env):
    env.setenv("WEBPAY_MOCK_MODE", "true")
    assert isinstance(build_webpay(get_webpay_settings()), MockWebpay)
    env.setenv("WEBPAY_MOCK_MODE", "false")
    integration = build_webpay(get_webpay_settings())
    assert isinstance(integration, TransbankWebpay)
    assert integration._client.base_url.host == "webpay3gint.transbank.cl"  # noqa: SLF001
    integration.close()
    env.setenv("WEBPAY_ENVIRONMENT", "PRODUCTION")
    env.setenv("WEBPAY_COMMERCE_CODE", "597012345678")
    env.setenv("WEBPAY_API_KEY", "production-secret")
    production = build_webpay(get_webpay_settings())
    assert production._client.base_url.host == "webpay3g.transbank.cl"  # noqa: SLF001
    production.close()
