from dataclasses import replace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from test_pagos import Pagos, SETTINGS, PAY
from test_webpay_gateway import APPROVED, TOKEN, adapter
from app.main import create_app
from app.services.webpay import MockWebpay, WebpayError


def test_signed_checkout_posts_only_the_transaction_token_and_rejects_tampering():
    pagos = Pagos()
    _, headers = pagos.athlete()
    started = pagos.started(headers, 1)
    checkout = started['checkout_url'].replace(SETTINGS.public_url, '')
    page = pagos.client.get(checkout)
    assert page.status_code == 200
    assert 'method="POST"' in page.text
    assert f'name="token_ws" value="{started["token_ws"]}"' in page.text
    assert 'document.getElementById("webpay").submit()' in page.text
    assert page.headers['cache-control'] == 'no-store'
    assert page.headers['referrer-policy'] == 'no-referrer'
    ticket = parse_qs(urlsplit(checkout).query)['ticket'][0]
    assert pagos.client.get(f'{PAY}/webpay/checkout', params={'ticket': 'x' + ticket}).status_code == 400


def test_web_return_is_configured_by_server_and_keeps_mobile_return():
    pagos = Pagos()
    settings = replace(SETTINGS, web_return_url='http://web.test/pago/resultado')
    pagos.client = TestClient(create_app(repository=pagos.repository, webpay=pagos.webpay, webpay_settings=settings))
    _, headers = pagos.athlete()
    started = pagos.initiate(headers, 1, client='web').json()
    assert pagos.webpay.describe(started['token_ws'])['return_url'].endswith('/return?client=web')
    result = pagos.client.post(f'{PAY}/webpay/return?client=web', data={'token_ws': started['token_ws']}, follow_redirects=False)
    assert result.headers['location'] == f'http://web.test/pago/resultado?token_ws={started["token_ws"]}'
    mobile = pagos.started(headers, 1)
    assert pagos.webpay.describe(mobile['token_ws'])['return_url'] == SETTINGS.return_url
    assert pagos.initiate(headers, 1, client='https://evil.test').status_code == 400


def test_web_without_return_url_fails_before_creating_a_charge():
    pagos = Pagos()
    _, headers = pagos.athlete()
    assert pagos.initiate(headers, 1, client='web').status_code == 503
    assert not pagos.repository._transacciones


def test_a_lost_commit_response_recovers_status_and_extends_only_once():
    class LostResponse(MockWebpay):
        def commit_transaction(self, token_ws):
            super().commit_transaction(token_ws)
            raise WebpayError('Response lost after authorization')

    pagos = Pagos(LostResponse(SETTINGS.public_url))
    _, headers = pagos.athlete()
    started, response = pagos.pay(headers, 1)
    assert response.status_code == 200
    again = pagos.commit(headers, token_ws=started['token_ws'])
    assert again.json() == response.json()


def test_another_session_cannot_activate_or_cancel_a_payment():
    class WrongSession(MockWebpay):
        def commit_transaction(self, token_ws):
            return replace(super().commit_transaction(token_ws), session_id='user:someone-else')

    pagos = Pagos(WrongSession(SETTINGS.public_url))
    _, headers = pagos.athlete()
    started, response = pagos.pay(headers, 1)
    assert response.status_code == 502
    assert pagos.subscription(headers).status_code == 404
    assert pagos.commit(headers, tbk_token=started['token_ws'], tbk_id_sesion='other').status_code == 404


@pytest.mark.parametrize('amount', [3990.4, -1, 'NaN', 'Infinity', True])
def test_invalid_clp_amounts_are_not_rounded_into_valid_payments(amount):
    with pytest.raises(WebpayError):
        adapter(lambda request: httpx.Response(200, json={**APPROVED, 'amount': amount})).commit_transaction(TOKEN)


def test_status_queries_use_get_and_parse_the_same_receipt():
    def handler(request):
        assert request.method == 'GET'
        assert request.url.path.endswith('/' + TOKEN)
        return httpx.Response(200, json=APPROVED)
    assert adapter(handler).transaction_status(TOKEN).approved


def test_untrusted_https_checkout_host_is_rejected():
    with pytest.raises(WebpayError):
        adapter(lambda request: httpx.Response(200, json={'token': TOKEN, 'url': 'https://evil.test/pay'})).create_transaction('order', 'session', 1000, 'https://return')


def test_return_confirms_without_login_and_authenticated_receipt_is_idempotent():
    pagos = Pagos()
    _, headers = pagos.athlete()
    started = pagos.started(headers, 1)
    pagos.webpay.decide(started['token_ws'], 'aprobar')
    response = pagos.client.post(f'{PAY}/webpay/return', data={'token_ws': started['token_ws']}, follow_redirects=False)
    assert response.status_code == 303
    subscription = pagos.subscription(headers).json()
    assert subscription['estado'] == 'ACTIVE'
    assert pagos.commit(headers, token_ws=started['token_ws']).json()['suscripcion'] == subscription


def test_return_without_complete_references_cannot_cancel_an_order():
    pagos = Pagos()
    _, headers = pagos.athlete()
    started = pagos.started(headers, 1)
    pagos.client.get(f'{PAY}/webpay/return', params={'TBK_ORDEN_COMPRA': started['buy_order']}, follow_redirects=False)
    assert pagos.repository.get_transaccion(token_ws=started['token_ws']).status == 'PENDING'


@pytest.mark.parametrize('code', [0.5, True, 'NaN'])
def test_invalid_response_code_cannot_be_converted_to_approval(code):
    with pytest.raises(WebpayError):
        adapter(lambda request: httpx.Response(200, json={**APPROVED, 'response_code': code})).commit_transaction(TOKEN)
