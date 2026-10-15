from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from uuid import UUID

from fastapi import HTTPException, status

from app.core.config import WebpaySettings
from app.core.security import Actor
from app.repositories.base import (
    ClubRepository,
    StoredPlan,
    StoredSuscripcion,
    StoredTransaccion,
)
from app.schemas.pago import (
    PagoConfirmado,
    PagoConfirmar,
    PagoIniciado,
    PagoIniciar,
    PlanRead,
    SuscripcionRead,
)
from app.services.webpay import WebpayCommit, WebpayError, WebpayGateway

logger = logging.getLogger(__name__)

_MENSAJES = {
    "REJECTED": "El pago fue rechazado. No se realizó ningún cobro y tu suscripción no cambió.",
    "CANCELLED": "El pago fue cancelado. No se realizó ningún cobro y tu suscripción no cambió.",
}


@dataclass(frozen=True)
class ResultadoPago:
    """Outcome of confirming a payment. `detail` explains a payment that did not
    go through; it is None when the payment was approved."""

    confirmado: PagoConfirmado
    detail: str | None


class PagoService:
    """Plan payments with Webpay Plus (HU-40)."""

    def __init__(
        self, repository: ClubRepository, gateway: WebpayGateway, settings: WebpaySettings
    ) -> None:
        self._repository = repository
        self._gateway = gateway
        self._settings = settings

    def list_planes(self, tipo: str | None) -> list[PlanRead]:
        return [to_plan_read(plan) for plan in self._repository.list_planes(tipo)]

    def initiate(self, actor: Actor, payload: PagoIniciar) -> PagoIniciado:
        plan = self._repository.get_plan(payload.plan_id)
        if plan is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan no encontrado.")
        usuario_id, club_id = self._comprador(actor, plan)

        actual = self._repository.get_suscripcion(usuario_id=usuario_id, club_id=club_id)
        if actual is not None and actual.estado == "ACTIVE" and actual.plan.id != plan.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Ya tienes una suscripción activa de otro plan. "
                "Podrás cambiar de plan cuando termine.",
            )

        buy_order = "SM" + secrets.token_hex(10)  # 22 of Webpay's 26 characters
        session_id = f"{'club' if club_id else 'user'}:{club_id or usuario_id}"
        try:
            creada = self._gateway.create_transaction(
                buy_order, session_id, plan.precio, self._settings.return_url
            )
        except WebpayError as error:
            logger.warning("Webpay could not create %s: %s", buy_order, error)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="No se pudo iniciar el pago con Webpay. Inténtalo de nuevo.",
            ) from error

        transaccion = self._repository.create_transaccion(
            {
                "usuario_id": usuario_id,
                "club_id": club_id,
                "iniciada_por": actor.id,
                "plan_id": plan.id,
                "buy_order": buy_order,
                "session_id": session_id,
                "token_ws": creada.token,
                "amount": plan.precio,
            }
        )
        return PagoIniciado(
            transaccion_id=transaccion.id,
            buy_order=transaccion.buy_order,
            session_id=transaccion.session_id,
            amount=transaccion.amount,
            token_ws=transaccion.token_ws,
            url=creada.url,
            status=transaccion.status,
            plan=to_plan_read(plan),
        )

    def confirm(self, actor: Actor, payload: PagoConfirmar) -> ResultadoPago:
        transaccion = self._find(actor, payload)

        # Idempotent: refreshing the return page, or Webpay returning twice,
        # shows the same receipt and never charges or extends anything again.
        if transaccion.status != "PENDING":
            return self._resultado(transaccion, self._suscripcion_de(transaccion))

        if payload.abandonado:
            return self._settle(transaccion, "CANCELLED", None)

        try:
            commit = self._gateway.commit_transaction(transaccion.token_ws)
        except WebpayError as error:
            # A concurrent confirmation may have settled it while this one failed.
            actual = self._repository.get_transaccion(token_ws=transaccion.token_ws)
            if actual is not None and actual.status != "PENDING":
                return self._resultado(actual, self._suscripcion_de(actual))
            logger.warning("Webpay could not commit %s: %s", transaccion.buy_order, error)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="No se pudo confirmar el pago con Webpay. Inténtalo de nuevo.",
            ) from error

        if commit.buy_order != transaccion.buy_order or commit.amount != transaccion.amount:
            # Never activate benefits from an answer that is not about this order.
            logger.error(
                "Webpay commit does not match order %s (amount %s vs %s)",
                transaccion.buy_order, commit.amount, transaccion.amount,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="La confirmación de Webpay no coincide con la orden de compra.",
            )
        return self._settle(
            transaccion, "APPROVED" if commit.approved else "REJECTED", _resultado_de(commit)
        )

    def my_suscripcion(self, actor: Actor) -> SuscripcionRead:
        if actor.is_club_admin:
            club = self._repository.get_club_by_owner(actor.id)
            if club is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Aún no tienes un club.")
            suscripcion = self._repository.get_suscripcion(club_id=club.id)
        elif actor.is_athlete:
            suscripcion = self._repository.get_suscripcion(usuario_id=actor.id)
        else:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Solo deportistas y representantes de club tienen suscripción.",
            )
        if suscripcion is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No tienes una suscripción.")
        return to_suscripcion_read(suscripcion)

    # -- internals ---------------------------------------------------------------------

    def _comprador(self, actor: Actor, plan: StoredPlan) -> tuple[UUID | None, UUID | None]:
        """(usuario_id, club_id) of whoever is paying, from the access token:
        a club plan is paid for the representative's own club, a user plan for
        the athlete themselves."""
        if plan.tipo == "CLUB":
            if not actor.is_club_admin:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Este plan es para representantes de club.",
                )
            club = self._repository.get_club_by_owner(actor.id)
            if club is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Primero crea el perfil de tu club.",
                )
            return None, club.id
        if not actor.is_athlete:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Este plan es para deportistas."
            )
        return actor.id, None

    def _find(self, actor: Actor, payload: PagoConfirmar) -> StoredTransaccion:
        if payload.abandonado and not payload.tbk_token:
            transaccion = self._repository.get_transaccion(buy_order=payload.tbk_orden_compra)
        else:
            transaccion = self._repository.get_transaccion(
                token_ws=payload.tbk_token or payload.token_ws
            )
        # Someone else's payment looks exactly like a missing one.
        if (
            transaccion is None
            or transaccion.iniciada_por != actor.id
            or (payload.tbk_orden_compra and payload.tbk_orden_compra != transaccion.buy_order)
        ):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Transacción no encontrada."
            )
        return transaccion

    def _settle(
        self, transaccion: StoredTransaccion, estado: str, resultado: dict | None
    ) -> ResultadoPago:
        actual, _, suscripcion = self._repository.settle_transaccion(
            transaccion.id, estado, resultado
        )
        return self._resultado(actual, suscripcion)

    def _suscripcion_de(self, transaccion: StoredTransaccion) -> StoredSuscripcion | None:
        if transaccion.status != "APPROVED":
            return None
        return self._repository.get_suscripcion(
            usuario_id=transaccion.usuario_id, club_id=transaccion.club_id
        )

    def _resultado(
        self, transaccion: StoredTransaccion, suscripcion: StoredSuscripcion | None
    ) -> ResultadoPago:
        confirmado = PagoConfirmado(
            transaccion_id=transaccion.id,
            status=transaccion.status,
            buy_order=transaccion.buy_order,
            amount=transaccion.amount,
            plan=to_plan_read(transaccion.plan),
            authorization_code=transaccion.authorization_code,
            payment_type_code=transaccion.payment_type_code,
            card_last4=transaccion.card_last4,
            installments_number=transaccion.installments_number,
            transaction_date=transaccion.transaction_date,
            fecha_creacion=transaccion.fecha_creacion,
            suscripcion=to_suscripcion_read(suscripcion) if suscripcion else None,
        )
        return ResultadoPago(confirmado, _MENSAJES.get(transaccion.status))


def _resultado_de(commit: WebpayCommit) -> dict:
    return {
        "response_code": commit.response_code,
        "authorization_code": commit.authorization_code,
        "payment_type_code": commit.payment_type_code,
        "card_last4": commit.card_last4,
        "installments_number": commit.installments_number,
        "transaction_date": commit.transaction_date,
    }


def to_plan_read(plan: StoredPlan) -> PlanRead:
    return PlanRead(
        id=plan.id,
        nombre=plan.nombre,
        tipo=plan.tipo,
        descripcion=plan.descripcion,
        precio=plan.precio,
        duracion_dias=plan.duracion_dias,
    )


def to_suscripcion_read(suscripcion: StoredSuscripcion) -> SuscripcionRead:
    return SuscripcionRead(
        id=suscripcion.id,
        estado=suscripcion.estado,
        plan=to_plan_read(suscripcion.plan),
        fecha_inicio=suscripcion.fecha_inicio,
        fecha_fin=suscripcion.fecha_fin,
        club_id=suscripcion.club_id,
    )
