from __future__ import annotations

import html
import re
from typing import Annotated
from urllib.parse import parse_qs, urlencode

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from app.api.dependencies import (
    get_current_actor,
    get_mock_webpay,
    get_pago_service,
    get_webpay_settings_dep,
)
from app.core.config import WebpaySettings
from app.core.security import Actor
from app.schemas.pago import (
    BUY_ORDER_PATTERN,
    SESSION_PATTERN,
    TOKEN_PATTERN,
    PagoConfirmado,
    PagoConfirmar,
    PagoIniciado,
    PagoIniciar,
    PlanRead,
    PlanTipo,
    SuscripcionRead,
)
from app.services.pago_service import PagoService
from app.services.webpay import MockWebpay

# Plan payments with Webpay Plus (HU-40). Listing plans is public; paying and
# reading one's own subscription need an access token. The return and mock
# endpoints are the browser's, so they carry no token.
router = APIRouter(prefix="/api/v1/payments", tags=["pagos"])

# Parameter Webpay sends back -> name the app receives, with the format it must have.
_RETURN_PARAMS = {
    "token_ws": ("token_ws", re.compile(TOKEN_PATTERN)),
    "TBK_TOKEN": ("tbk_token", re.compile(TOKEN_PATTERN)),
    "TBK_ORDEN_COMPRA": ("tbk_orden_compra", re.compile(BUY_ORDER_PATTERN)),
    "TBK_ID_SESION": ("tbk_id_sesion", re.compile(SESSION_PATTERN)),
}
_MAX_RETURN_BODY = 4096


@router.get(
    "/plans",
    response_model=list[PlanRead],
    summary="Planes disponibles",
    description="Planes activos, el más barato primero. Filtro opcional: tipo (USUARIO, CLUB). "
    "El precio es en pesos chilenos.",
)
def list_plans(
    tipo: Annotated[PlanTipo | None, Query()] = None,
    service: PagoService = Depends(get_pago_service),
) -> list[PlanRead]:
    return service.list_planes(tipo)


@router.post(
    "/webpay/initiate",
    response_model=PagoIniciado,
    status_code=status.HTTP_201_CREATED,
    summary="Iniciar el pago de un plan",
    description="Crea la transacción en Webpay Plus y la registra PENDING. El comprador sale del "
    "token (un representante paga por su club, un deportista por sí mismo) y el monto es el precio "
    "del plan. Redirige el navegador a `url` con un formulario POST cuyo único campo es `token_ws`.",
)
def initiate_payment(
    payload: PagoIniciar,
    actor: Actor = Depends(get_current_actor),
    service: PagoService = Depends(get_pago_service),
) -> PagoIniciado:
    return service.initiate(actor, payload)


@router.post(
    "/webpay/commit",
    response_model=PagoConfirmado,
    summary="Confirmar un pago al volver de Webpay",
    description="Recibe lo que Webpay devolvió al navegador: `token_ws` (el usuario pasó por el "
    "formulario), o `tbk_token`/`tbk_orden_compra`/`tbk_id_sesion` (canceló o venció el tiempo). "
    "Aprobado: 200 con el comprobante y la suscripción activa, extendida según el plan. "
    "Rechazado o cancelado: 400 con el comprobante, sin cambiar la suscripción. "
    "Repetir la confirmación devuelve el mismo resultado sin cobrar ni extender de nuevo.",
)
def commit_payment(
    payload: PagoConfirmar,
    actor: Actor = Depends(get_current_actor),
    service: PagoService = Depends(get_pago_service),
):
    resultado = service.confirm(actor, payload)
    if resultado.detail is None:
        return resultado.confirmado
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": resultado.detail, "transaccion": jsonable_encoder(resultado.confirmado)},
    )


@router.get(
    "/subscription/me",
    response_model=SuscripcionRead,
    summary="Mi suscripción",
    description="La suscripción del deportista, o del club del representante: plan, vigencia y "
    "estado (ACTIVE o EXPIRED). 404 si nunca ha contratado un plan.",
)
def my_subscription(
    actor: Actor = Depends(get_current_actor),
    service: PagoService = Depends(get_pago_service),
) -> SuscripcionRead:
    return service.my_suscripcion(actor)


@router.api_route(
    "/webpay/return",
    methods=["GET", "POST"],
    include_in_schema=False,
    summary="Retorno de Webpay Plus",
)
async def webpay_return(
    request: Request, settings: WebpaySettings = Depends(get_webpay_settings_dep)
) -> Response:
    """Where Webpay sends the user's browser (GET, or POST in Transbank's
    integration environment). It does not trust or act on anything: it only
    passes the well-formed parameters on to the app, which then calls /commit
    with the user's session."""
    received: dict[str, str] = dict(request.query_params)
    if request.method == "POST":
        body = (await request.body())[:_MAX_RETURN_BODY].decode("utf-8", errors="ignore")
        received.update({name: values[0] for name, values in parse_qs(body).items()})
    forwarded = {
        forward: received[name]
        for name, (forward, pattern) in _RETURN_PARAMS.items()
        if name in received and pattern.fullmatch(received[name])
    }
    if not forwarded:
        forwarded = {"error": "parametros"}
    separator = "&" if "?" in settings.frontend_return_url else "?"
    return RedirectResponse(
        f"{settings.frontend_return_url}{separator}{urlencode(forwarded)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


# -- Simulated Webpay (WEBPAY_MOCK_MODE=true); 404 when it is off --------------------------


@router.api_route(
    "/webpay/mock", methods=["GET", "POST"], include_in_schema=False, response_class=HTMLResponse
)
async def mock_webpay_page(
    request: Request, mock: MockWebpay = Depends(get_mock_webpay)
) -> Response:
    token = request.query_params.get("token_ws", "")
    if request.method == "POST":
        body = (await request.body())[:_MAX_RETURN_BODY].decode("utf-8", errors="ignore")
        token = parse_qs(body).get("token_ws", [token])[0]
    created = mock.describe(token) if re.fullmatch(TOKEN_PATTERN, token) else None
    if created is None:
        return HTMLResponse(_page("Transacción no encontrada", "<p>El enlace no es válido.</p>"), 404)

    monto = f"${created['amount']:,}".replace(",", ".")
    body = (
        '<form method="post" action="/api/v1/payments/webpay/mock/pagar" class="tarjeta">'
        f'<input type="hidden" name="token_ws" value="{html.escape(token)}">'
        '<p class="resumen"><span>Comercio</span><strong>SportMatch</strong></p>'
        f'<p class="resumen"><span>Orden</span><strong>{html.escape(created["buy_order"])}</strong></p>'
        f'<p class="total"><span>Total a pagar</span><strong>{monto} CLP</strong></p>'
        '<label>Número de tarjeta<input name="numero" inputmode="numeric" autocomplete="cc-number" '
        'placeholder="4051 8856 0044 6623" required></label>'
        '<div class="fila"><label>Vencimiento<input placeholder="MM/AA" autocomplete="cc-exp" required></label>'
        '<label>CVV<input placeholder="123" inputmode="numeric" autocomplete="cc-csc" required></label></div>'
        '<label>Nombre del titular<input placeholder="Como aparece en la tarjeta" autocomplete="cc-name" required></label>'
        '<button type="submit">Pagar ' + monto + '</button>'
        '</form>'
        '<p class="ayuda">Entorno de prueba: no se realiza ningún cobro real. '
        'Tarjeta 4051 8856 0044 6623 aprueba el pago; 5186 0595 5959 5609 lo rechaza.</p>'
        + f'<p><a class="volver" href="/api/v1/payments/webpay/mock/decision?'
        + urlencode({"token_ws": token, "accion": "cancelar"})
        + '">Anular compra y volver a la app</a></p>'
    )
    return HTMLResponse(_page("Pago seguro", body, _ESTILO_PAGO))


@router.post("/webpay/mock/pagar", include_in_schema=False)
async def mock_webpay_pagar(request: Request, mock: MockWebpay = Depends(get_mock_webpay)) -> Response:
    """Simulated card payment: the card number only decides approve or reject in test mode.
    It is never stored or sent anywhere."""
    datos = parse_qs((await request.body())[:_MAX_RETURN_BODY].decode("utf-8", errors="ignore"))
    token = datos.get("token_ws", [""])[0]
    numero = re.sub(r"\D", "", datos.get("numero", [""])[0])
    if not re.fullmatch(TOKEN_PATTERN, token) or mock.describe(token) is None:
        return HTMLResponse(_page("Transacción no encontrada", "<p>El enlace no es válido.</p>"), 404)
    accion = "aprobar" if numero == "4051885600446623" else "rechazar"
    destino = mock.decide(token, accion)
    if destino is None:
        return HTMLResponse(_page("Transacción no encontrada", "<p>El enlace no es válido.</p>"), 404)
    return RedirectResponse(destino, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/webpay/mock/decision", include_in_schema=False)
def mock_webpay_decision(
    token_ws: str, accion: str, mock: MockWebpay = Depends(get_mock_webpay)
) -> Response:
    destino = mock.decide(token_ws, accion) if re.fullmatch(TOKEN_PATTERN, token_ws) else None
    if destino is None:
        return HTMLResponse(_page("Transacción no encontrada", "<p>El enlace no es válido.</p>"), 404)
    return RedirectResponse(destino, status_code=status.HTTP_303_SEE_OTHER)


_ESTILO_PAGO = """
body{margin:0;background:#eef1f5;font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;color:#1c2430}
.cabecera{background:#1c2430;color:#fff;padding:14px 18px;font-weight:700}
.tarjeta{background:#fff;max-width:420px;margin:18px auto;padding:20px;border-radius:12px;box-shadow:0 2px 12px rgba(0,0,0,.08)}
.resumen,.total{display:flex;justify-content:space-between;margin:8px 0;font-size:14px;color:#5b6676}
.total{font-size:16px;color:#1c2430;border-top:1px solid #e3e7ee;padding-top:12px;margin-top:14px}
label{display:block;font-size:13px;color:#5b6676;margin-top:12px}
input{display:block;width:100%;box-sizing:border-box;margin-top:4px;padding:11px;border:1px solid #cdd4de;border-radius:8px;font-size:16px}
.fila{display:flex;gap:10px}.fila label{flex:1}
button{width:100%;margin-top:18px;padding:14px;border:0;border-radius:10px;background:#0b7a5a;color:#fff;font-size:16px;font-weight:700}
.ayuda{max-width:420px;margin:0 auto;padding:0 20px;font-size:12px;color:#5b6676}
.volver{display:block;text-align:center;margin:18px;color:#5b6676;font-size:14px}
"""


def _page(title: str, body: str, estilo: str = "") -> str:
    return (
        '<!doctype html><html lang="es"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{html.escape(title)}</title><style>{estilo}</style></head>"
        '<body>'
        f'<div class="cabecera">{html.escape(title)}</div>{body}</body></html>'
    )
