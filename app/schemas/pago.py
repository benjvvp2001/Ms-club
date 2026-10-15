from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.schemas.common import APIModel

PlanTipo = Literal["USUARIO", "CLUB"]
EstadoTransaccion = Literal["PENDING", "APPROVED", "REJECTED", "CANCELLED"]
EstadoSuscripcion = Literal["ACTIVE", "EXPIRED"]

# Tokens and references Transbank sends back: letters, digits and a few separators.
TOKEN_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"
BUY_ORDER_PATTERN = r"^[A-Za-z0-9_-]{1,26}$"
SESSION_PATTERN = r"^[A-Za-z0-9:_-]{1,61}$"


class PagoIniciar(APIModel):
    """Start paying a plan (the InitiatePaymentDto).

    Only the plan travels in the request. Who pays comes from the access token
    (a club_admin pays for their club, an athlete for themselves) and the amount
    is the plan's price: neither can be chosen by the client.
    """

    plan_id: int = Field(ge=1)


class PagoConfirmar(APIModel):
    """What Webpay Plus returned to the browser (the CommitPaymentDto). Exactly
    one of these cases:

    - token_ws: the user went through the payment form (paid or declined).
    - tbk_token (+ tbk_orden_compra, tbk_id_sesion): the user cancelled it.
    - tbk_orden_compra (+ tbk_id_sesion) alone: the form timed out.
    """

    token_ws: str | None = Field(default=None, pattern=TOKEN_PATTERN)
    tbk_token: str | None = Field(default=None, pattern=TOKEN_PATTERN)
    tbk_orden_compra: str | None = Field(default=None, pattern=BUY_ORDER_PATTERN)
    tbk_id_sesion: str | None = Field(default=None, pattern=SESSION_PATTERN)

    @model_validator(mode="after")
    def algo_que_confirmar(self) -> PagoConfirmar:
        if not (self.token_ws or self.tbk_token or self.tbk_orden_compra):
            raise ValueError("Envía token_ws, tbk_token o tbk_orden_compra.")
        return self

    @property
    def abandonado(self) -> bool:
        """Webpay reports a cancellation or a timeout with TBK_* parameters;
        the transaction must not be committed even if token_ws also came."""
        return bool(self.tbk_token or self.tbk_orden_compra)


class PlanRead(APIModel):
    id: int
    nombre: str
    tipo: PlanTipo
    descripcion: str | None
    precio: int
    duracion_dias: int


class PagoIniciado(APIModel):
    """Result of starting a payment. The client must send the user's browser to
    `url` with a POST form whose only field is `token_ws`."""

    transaccion_id: UUID
    buy_order: str
    session_id: str
    amount: int
    token_ws: str
    url: str
    status: EstadoTransaccion
    plan: PlanRead


class Comprobante(APIModel):
    """Receipt of a payment attempt."""

    transaccion_id: UUID
    status: EstadoTransaccion
    buy_order: str
    amount: int
    plan: PlanRead
    authorization_code: str | None
    payment_type_code: str | None
    card_last4: str | None
    installments_number: int | None
    transaction_date: datetime | None
    fecha_creacion: datetime


class SuscripcionRead(APIModel):
    id: UUID
    estado: EstadoSuscripcion
    plan: PlanRead
    fecha_inicio: datetime
    fecha_fin: datetime
    club_id: UUID | None


class PagoConfirmado(Comprobante):
    """Result of confirming a payment. `suscripcion` is the buyer's subscription
    after the payment; null if the payment did not succeed."""

    suscripcion: SuscripcionRead | None
