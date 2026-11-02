from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.dependencies import get_admin, get_plan_admin_service
from app.core.security import Actor
from app.schemas.pago import PlanCategoria, PlanTipo
from app.schemas.plan_admin import (
    LIMIT_DEFAULT,
    LIMIT_MAX,
    SUSCRIPCIONES_LIMIT_MAX,
    EstadoPlanFiltro,
    EstadoSuscripcionFiltro,
    PlanAdminPage,
    PlanAdminRead,
    PlanCreate,
    PlanDesactivar,
    PlanReactivar,
    PlanSuscripcionPage,
    PlanUpdate,
)
from app.services.plan_admin_service import PlanAdminService

# HU-56: planes y convenios para el administrador de la plataforma. Cada ruta exige el rol admin aqui (el
# gateway solo valida el JWT): un no-admin recibe 403 aunque el plan no exista. Un cambio queda en la
# auditoria (HU-48) en la misma operacion; si la auditoria falla, no se guarda y se responde 503.
router = APIRouter(prefix="/api/v1/payments/admin/plans", tags=["planes-admin"])


@router.get(
    "",
    response_model=PlanAdminPage,
    summary="Planes y convenios (administrador)",
    description="Activos e inactivos, el más reciente primero, con el número de suscripciones vigentes de cada "
    f"uno. Filtros: estado (activo, inactivo, todos), tipo, categoria; pagina (desde 1) y limit (máximo {LIMIT_MAX}).",
)
def list_plans(
    _: Annotated[Actor, Depends(get_admin)],
    service: Annotated[PlanAdminService, Depends(get_plan_admin_service)],
    estado: Annotated[EstadoPlanFiltro, Query()] = "todos",
    tipo: Annotated[PlanTipo | None, Query()] = None,
    categoria: Annotated[PlanCategoria | None, Query()] = None,
    pagina: Annotated[int, Query(ge=1, le=10_000)] = 1,
    limit: Annotated[int, Query(ge=1, le=LIMIT_MAX)] = LIMIT_DEFAULT,
) -> PlanAdminPage:
    return service.list(estado, tipo, categoria, pagina, limit)


@router.post(
    "",
    response_model=PlanAdminRead,
    status_code=status.HTTP_201_CREATED,
    summary="Crear un plan o convenio",
    description="categoria PLAN o CONVENIO; tipo USUARIO o CLUB indica quién lo compra. Precio entero en pesos "
    "(0 a 10.000.000; 0 = gratuito, no se paga con Webpay), duración de 1 a 366 días, hasta 10 beneficios. "
    "El nombre es único entre los planes activos.",
)
def create_plan(
    payload: PlanCreate,
    admin: Annotated[Actor, Depends(get_admin)],
    service: Annotated[PlanAdminService, Depends(get_plan_admin_service)],
) -> PlanAdminRead:
    return service.create(admin, payload)


@router.patch(
    "/{plan_id}",
    response_model=PlanAdminRead,
    summary="Editar un plan o convenio activo",
    description="Solo cambia lo que se envía. Un cambio de precio o de duración no afecta a las suscripciones ya "
    "pagadas: cada una conserva lo que pagó. El tipo y la categoría no se editan.",
)
def update_plan(
    plan_id: int,
    payload: PlanUpdate,
    admin: Annotated[Actor, Depends(get_admin)],
    service: Annotated[PlanAdminService, Depends(get_plan_admin_service)],
) -> PlanAdminRead:
    return service.update(admin, plan_id, payload)


@router.post(
    "/{plan_id}/deactivate",
    response_model=PlanAdminRead,
    summary="Desactivar un plan o convenio",
    description="Borrado lógico (is_active = false): deja de mostrarse en el catálogo y no se puede contratar ni "
    "renovar, pero las suscripciones vigentes siguen hasta su término. Nunca se borra la fila.",
)
def deactivate_plan(
    plan_id: int,
    admin: Annotated[Actor, Depends(get_admin)],
    service: Annotated[PlanAdminService, Depends(get_plan_admin_service)],
    payload: PlanDesactivar | None = None,
) -> PlanAdminRead:
    return service.deactivate(admin, plan_id, payload or PlanDesactivar())


@router.post(
    "/{plan_id}/reactivate",
    response_model=PlanAdminRead,
    summary="Reactivar un plan o convenio desactivado",
    description="Solo si está desactivado. Vuelve al catálogo y borra la fecha y el motivo de la desactivación. "
    "Su nombre debe seguir libre entre los planes activos: si otro plan activo lo tomó, responde 409. "
    "Queda en la auditoría (PLAN_REACTIVATED) en la misma operación; si la auditoría falla, no se guarda (503).",
)
def reactivate_plan(
    plan_id: int,
    admin: Annotated[Actor, Depends(get_admin)],
    service: Annotated[PlanAdminService, Depends(get_plan_admin_service)],
    payload: PlanReactivar | None = None,
) -> PlanAdminRead:
    return service.reactivate(admin, plan_id, payload or PlanReactivar())


@router.get(
    "/{plan_id}/subscriptions",
    response_model=PlanSuscripcionPage,
    summary="Suscripciones de un plan o convenio (administrador)",
    description="Quienes tienen este plan como su último pago: nombre corto (o el del club), estado, fecha de inicio "
    f"y de término. Filtro estado (vigente, vencida, todas); pagina (desde 1) y limit (máximo {SUSCRIPCIONES_LIMIT_MAX}).",
)
def list_plan_subscriptions(
    plan_id: int,
    _: Annotated[Actor, Depends(get_admin)],
    service: Annotated[PlanAdminService, Depends(get_plan_admin_service)],
    estado: Annotated[EstadoSuscripcionFiltro, Query()] = "todas",
    pagina: Annotated[int, Query(ge=1, le=10_000)] = 1,
    limit: Annotated[int, Query(ge=1, le=SUSCRIPCIONES_LIMIT_MAX)] = 20,
) -> PlanSuscripcionPage:
    return service.list_subscriptions(plan_id, estado, pagina, limit)
