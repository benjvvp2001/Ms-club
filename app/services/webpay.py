from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from urllib.parse import urlencode

import httpx

from app.core.config import WebpaySettings

_BASE_URLS = {
    "INTEGRATION": "https://webpay3gint.transbank.cl",
    "PRODUCTION": "https://webpay3g.transbank.cl",
}
_API = "/rswebpaytransaction/api/webpay/v1.2/transactions"


class WebpayError(Exception):
    """The gateway could not be reached or answered something unexpected. A
    payment the user declined is NOT an error: it is a WebpayCommit that is not
    approved. The message never contains tokens or credentials."""


@dataclass(frozen=True)
class WebpayTransaction:
    token: str  # token_ws
    url: str  # payment form; the browser POSTs token_ws to it


@dataclass(frozen=True)
class WebpayCommit:
    buy_order: str
    session_id: str
    amount: int
    status: str  # AUTHORIZED, FAILED, REVERSED...
    response_code: int | None
    authorization_code: str | None = None
    payment_type_code: str | None = None
    card_last4: str | None = None
    installments_number: int | None = None
    transaction_date: datetime | None = None

    @property
    def approved(self) -> bool:
        """Transbank's rule: response_code 0 and status AUTHORIZED."""
        return self.response_code == 0 and self.status == "AUTHORIZED"


class WebpayGateway(Protocol):
    """Webpay Plus as the payment service needs it. TransbankWebpay talks to
    Transbank; MockWebpay simulates it (WEBPAY_MOCK_MODE=true)."""

    def create_transaction(
        self, buy_order: str, session_id: str, amount: int, return_url: str
    ) -> WebpayTransaction: ...

    def commit_transaction(self, token_ws: str) -> WebpayCommit: ...

    def close(self) -> None: ...


class TransbankWebpay:
    """Webpay Plus over Transbank's REST API (v1.2).

    Written from Transbank's published API reference. To use the official SDK
    instead, implement WebpayGateway with it and pass it to create_app(): nothing
    else in the service changes.
    """

    def __init__(
        self, client: httpx.Client, commerce_code: str, api_key: str
    ) -> None:
        self._client = client
        self._headers = {
            "Tbk-Api-Key-Id": commerce_code,
            "Tbk-Api-Key-Secret": api_key,
            "Content-Type": "application/json",
        }

    @classmethod
    def from_settings(cls, settings: WebpaySettings) -> TransbankWebpay:
        assert settings.commerce_code and settings.api_key
        client = httpx.Client(
            base_url=_BASE_URLS[settings.environment],
            timeout=settings.timeout_seconds,
            trust_env=False,
        )
        return cls(client, settings.commerce_code, settings.api_key)

    def close(self) -> None:
        self._client.close()

    def create_transaction(
        self, buy_order: str, session_id: str, amount: int, return_url: str
    ) -> WebpayTransaction:
        body = self._request(
            "POST",
            _API,
            json={
                "buy_order": buy_order,
                "session_id": session_id,
                "amount": amount,
                "return_url": return_url,
            },
        )
        token, url = body.get("token"), body.get("url")
        if (
            not isinstance(token, str)
            or not 1 <= len(token) <= 64
            or not isinstance(url, str)
            or not url.startswith("https://")
        ):
            raise WebpayError("Webpay answered an unexpected create-transaction response")
        return WebpayTransaction(token=token, url=url)

    def commit_transaction(self, token_ws: str) -> WebpayCommit:
        body = self._request("PUT", f"{_API}/{token_ws}")
        try:
            return WebpayCommit(
                buy_order=str(body["buy_order"]),
                session_id=str(body["session_id"]),
                amount=int(round(float(body["amount"]))),
                status=str(body["status"]),
                response_code=_optional_int(body.get("response_code")),
                authorization_code=_optional_text(body.get("authorization_code")),
                payment_type_code=_optional_text(body.get("payment_type_code")),
                card_last4=_card_last4(body.get("card_detail")),
                installments_number=_optional_int(body.get("installments_number")),
                transaction_date=_optional_datetime(body.get("transaction_date")),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise WebpayError("Webpay answered an unexpected commit response") from error

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = self._client.request(method, path, headers=self._headers, **kwargs)
        except httpx.HTTPError as error:
            raise WebpayError(f"Webpay could not be reached ({type(error).__name__})") from error
        if response.status_code != 200:
            raise WebpayError(f"Webpay answered HTTP {response.status_code}")
        try:
            body = response.json()
        except ValueError as error:
            raise WebpayError("Webpay answered a non-JSON body") from error
        if not isinstance(body, dict):
            raise WebpayError("Webpay answered an unexpected body")
        return body


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _optional_text(value: Any) -> str | None:
    return None if value in (None, "") else str(value)


def _optional_datetime(value: Any) -> datetime | None:
    return None if not value else datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _card_last4(card_detail: Any) -> str | None:
    """Transbank sends only the last digits of the card; keep at most 4."""
    if not isinstance(card_detail, dict):
        return None
    number = str(card_detail.get("card_number") or "")
    digits = "".join(ch for ch in number if ch.isdigit())
    return digits[-4:] or None


class MockWebpay:
    """Simulated Webpay Plus for development and tests (WEBPAY_MOCK_MODE=true).

    It never charges anything and approves whatever the person clicks on the
    simulated payment page served by this service, so it must never run in
    production (the settings refuse that combination). Like the real service, a
    token can be committed only once and only after the user finished the form.
    """

    PAGE_PATH = "/api/v1/payments/webpay/mock"

    def __init__(self, public_url: str) -> None:
        self._public_url = public_url.rstrip("/")
        self._lock = threading.Lock()
        self._created: dict[str, dict[str, Any]] = {}
        self._decisions: dict[str, str] = {}
        self._committed: set[str] = set()

    def close(self) -> None:
        pass

    def create_transaction(
        self, buy_order: str, session_id: str, amount: int, return_url: str
    ) -> WebpayTransaction:
        token = secrets.token_hex(32)  # 64 characters, like Transbank's
        with self._lock:
            self._created[token] = {
                "buy_order": buy_order,
                "session_id": session_id,
                "amount": amount,
                "return_url": return_url,
            }
        return WebpayTransaction(token=token, url=f"{self._public_url}{self.PAGE_PATH}")

    def describe(self, token: str) -> dict[str, Any] | None:
        """What the simulated payment page shows; None for an unknown token."""
        with self._lock:
            created = self._created.get(token)
            return dict(created) if created else None

    def decide(self, token: str, accion: str) -> str | None:
        """Record what the person chose and return where the browser goes back
        to, like Webpay does; None for an unknown token or action."""
        with self._lock:
            created = self._created.get(token)
            if created is None or accion not in {"aprobar", "rechazar", "cancelar"}:
                return None
            self._decisions[token] = accion
            if accion == "cancelar":
                params = {
                    "TBK_TOKEN": token,
                    "TBK_ORDEN_COMPRA": created["buy_order"],
                    "TBK_ID_SESION": created["session_id"],
                }
            else:
                params = {"token_ws": token}
            return f"{created['return_url']}?{urlencode(params)}"

    def commit_transaction(self, token_ws: str) -> WebpayCommit:
        with self._lock:
            created = self._created.get(token_ws)
            decision = self._decisions.get(token_ws)
            if created is None:
                raise WebpayError("Unknown transaction")
            if decision not in {"aprobar", "rechazar"}:
                raise WebpayError("The user has not completed the payment form")
            if token_ws in self._committed:
                raise WebpayError("The transaction was already committed")
            self._committed.add(token_ws)
        approved = decision == "aprobar"
        return WebpayCommit(
            buy_order=created["buy_order"],
            session_id=created["session_id"],
            amount=created["amount"],
            status="AUTHORIZED" if approved else "FAILED",
            response_code=0 if approved else -1,
            authorization_code=f"{secrets.randbelow(10**6):06d}" if approved else None,
            payment_type_code="VN" if approved else None,
            card_last4="6623" if approved else None,
            installments_number=0 if approved else None,
            transaction_date=datetime.now().astimezone(),
        )


def build_webpay(settings: WebpaySettings) -> WebpayGateway:
    if settings.mock_mode:
        return MockWebpay(settings.public_url)
    return TransbankWebpay.from_settings(settings)
