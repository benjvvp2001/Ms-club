from __future__ import annotations

import html
import re
import secrets
import jwt
from typing import Annotated
from urllib.parse import parse_qs, urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from starlette.concurrency import run_in_threadpool
from pydantic import ValidationError
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
from app.services.webpay_checkout import read_checkout

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


@router.get("/webpay/checkout", include_in_schema=False)
def checkout(ticket: str = Query(max_length=4096)) -> Response:
    try:
        data = read_checkout(ticket)
    except jwt.PyJWTError:
        return HTMLResponse(_page("Enlace vencido", "<p>Vuelve a SportMatch para iniciar el pago.</p>"), 400)
    nonce = secrets.token_urlsafe(24)
    body = (
        f'<form id="webpay" method="POST" action="{html.escape(data["url"], quote=True)}">'
        f'<input type="hidden" name="token_ws" value="{html.escape(data["token_ws"], quote=True)}">'
        '<p>Continúa a Webpay para completar el pago.</p><button type="submit">Ir a Webpay</button></form>'
        f'<script nonce="{nonce}">document.getElementById("webpay").submit();</script>'
    )
    return HTMLResponse(_page("Pago con Webpay", body), headers={
        "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
        "Content-Security-Policy": f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'",
    })


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
    request: Request, settings: WebpaySettings = Depends(get_webpay_settings_dep),
    service: PagoService = Depends(get_pago_service),
) -> Response:
    """Confirm with Transbank before redirecting, even if the login expired.
    Clients can retry /commit safely to retrieve their authenticated receipt."""
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
    else:
        try:
            await run_in_threadpool(service.confirm_browser, PagoConfirmar(**forwarded))
        except (HTTPException, ValidationError):
            # Do not invent a successful result on errors. The authenticated
            # result screen will retrieve or retry the same transaction.
            pass
    destination = settings.frontend_return_url
    if request.query_params.get("client") == "web":
        if not settings.web_return_url:
            return HTMLResponse(_page("Pago", "<p>El retorno web no está configurado.</p>"), 503)
        destination = settings.web_return_url
    separator = "&" if "?" in destination else "?"
    return RedirectResponse(
        f"{destination}{separator}{urlencode(forwarded)}",
        status_code=status.HTTP_303_SEE_OTHER,
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
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

    def link(accion: str, texto: str) -> str:
        query = urlencode({"token_ws": token, "accion": accion})
        return f'<p><a href="/api/v1/payments/webpay/mock/decision?{query}">{texto}</a></p>'

    body = (
        "<p><strong>Simulación de Webpay Plus.</strong> No se realiza ningún cobro.</p>"
        f"<p>Orden de compra: {html.escape(created['buy_order'])}<br>"
        f"Monto: ${created['amount']:,}".replace(",", ".")
        + "</p>"
        + link("aprobar", "Pagar (simular pago aprobado)")
        + link("rechazar", "Simular pago rechazado")
        + link("cancelar", "Anular compra y volver")
    )
    return HTMLResponse(_page("Webpay Plus (simulado)", body))


@router.get("/webpay/mock/decision", include_in_schema=False)
def mock_webpay_decision(
    token_ws: str, accion: str, mock: MockWebpay = Depends(get_mock_webpay)
) -> Response:
    destino = mock.decide(token_ws, accion) if re.fullmatch(TOKEN_PATTERN, token_ws) else None
    if destino is None:
        return HTMLResponse(_page("Transacción no encontrada", "<p>El enlace no es válido.</p>"), 404)
    return RedirectResponse(destino, status_code=status.HTTP_303_SEE_OTHER)


def _page(title: str, body: str) -> str:
    return (
        '<!doctype html><html lang="es"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{html.escape(title)}</title></head>"
        '<body style="font-family:sans-serif;max-width:28rem;margin:2rem auto;padding:0 1rem">'
        f"<h1>{html.escape(title)}</h1>{body}</body></html>"
    )
