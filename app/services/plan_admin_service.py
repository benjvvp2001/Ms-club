"""HU-56: create, edit and deactivate plans and convenios (platform administrator)."""
from __future__ import annotations

from fastapi import HTTPException, status

from app.core.security import Actor
from app.repositories.base import (
    ClubRepository,
    PlanActivoError,
    PlanBaseError,
    PlanInactivoError,
    PlanNombreDuplicadoError,
    StoredPlan,
)
from app.schemas.plan_admin import (
    PlanAdminPage,
    PlanAdminRead,
    PlanCreate,
    PlanDesactivar,
    PlanReactivar,
    PlanSuscripcionPage,
    PlanSuscripcionRead,
    PlanUpdate,
)
from app.services.account_names import AccountDirectory
from app.services.plan_audit import PlanAuditor, PlanAuditUnavailable

_MOTIVOS = {
    "PLAN_CREATED": "Creación de plan o convenio",
    "PLAN_UPDATED": "Edición de plan o convenio",
    "PLAN_DEACTIVATED": "Desactivación de plan o convenio",
    "PLAN_REACTIVATED": "Reactivación de plan o convenio",
}


def to_admin_read(plan: StoredPlan) -> PlanAdminRead:
    return PlanAdminRead(
        id=plan.id, nombre=plan.nombre, tipo=plan.tipo, categoria=plan.categoria, descripcion=plan.descripcion,
        precio=plan.precio, duracion_dias=plan.duracion_dias, beneficios=list(plan.beneficios),
        institucion=plan.institucion, is_active=plan.is_active, suscripciones_vigentes=plan.suscripciones_vigentes,
        fecha_creacion=plan.fecha_creacion, es_base=plan.es_base, fecha_desactivacion=plan.fecha_desactivacion,
        motivo_desactivacion=plan.motivo_desactivacion,
    )


def _metadata(before: StoredPlan | None, after: StoredPlan) -> dict[str, str | int | bool | None]:
    """Only numbers and fixed words: no free text goes to the audit log."""
    changed = [] if before is None else [
        name for name in ("nombre", "descripcion", "precio", "duracion_dias", "beneficios", "institucion", "is_active")
        if getattr(before, name) != getattr(after, name)
    ]
    return {
        "tipo": after.tipo, "categoria": after.categoria,
        "precio_anterior": None if before is None else before.precio, "precio_nuevo": after.precio,
        "duracion_anterior": None if before is None else before.duracion_dias, "duracion_nueva": after.duracion_dias,
        "beneficios_cantidad": len(after.beneficios),
        "campos": "creacion" if before is None else ",".join(changed),
    }


class PlanAdminService:
    def __init__(
        self, repository: ClubRepository, auditor: PlanAuditor | None, names: AccountDirectory | None = None
    ) -> None:
        self._repository = repository
        self._auditor = auditor
        self._names = names

    def list(self, estado: str, tipo: str | None, categoria: str | None, pagina: int, limit: int) -> PlanAdminPage:
        planes, total = self._repository.list_planes_admin(
            estado=estado, tipo=tipo, categoria=categoria, limit=limit, offset=(pagina - 1) * limit)
        return PlanAdminPage(items=[to_admin_read(p) for p in planes], pagina=pagina, limit=limit, total=total)

    def create(self, actor: Actor, payload: PlanCreate) -> PlanAdminRead:
        values = {
            "nombre": _normalize(payload.nombre), "tipo": payload.tipo, "categoria": payload.categoria,
            "descripcion": payload.descripcion, "precio": payload.precio, "duracion_dias": payload.duracion_dias,
            "beneficios": tuple(_normalize(b) for b in payload.beneficios),
            "institucion": _normalize(payload.institucion) if payload.institucion else None,
        }
        try:
            plan = self._repository.create_plan(values, audit=self._audit(actor, payload.motivo))
        except PlanNombreDuplicadoError:
            raise _duplicate() from None
        return to_admin_read(plan)

    def update(self, actor: Actor, plan_id: int, payload: PlanUpdate) -> PlanAdminRead:
        current = self._repository.get_plan_any(plan_id)
        if current is None:
            raise _not_found()
        if not current.is_active:
            raise _inactive()
        changes: dict = {}
        for name in payload.model_fields_set - {"motivo"}:
            value = getattr(payload, name)
            if name == "nombre":
                value = _normalize(value)
            elif name == "beneficios":
                value = tuple(_normalize(b) for b in value)
            elif name == "institucion":
                value = _normalize(value) if value else None
                if value is not None and current.categoria != "CONVENIO":
                    raise HTTPException(status.HTTP_400_BAD_REQUEST, "Solo un convenio tiene institución.")
            if value != getattr(current, name):
                changes[name] = value
        if not changes:
            return to_admin_read(current)  # nothing changes: nothing is written or audited
        # "Sin vencimiento" (duracion null) solo existe en un plan gratis: se revisa con el precio y la duracion resultantes.
        if changes.get("duracion_dias", current.duracion_dias) is None and changes.get("precio", current.precio) > 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Un plan de pago necesita una duración (de 1 a 366 días).")
        try:
            result = self._repository.update_plan(plan_id, changes, audit=self._audit(actor, payload.motivo))
        except PlanNombreDuplicadoError:
            raise _duplicate() from None
        except PlanInactivoError:
            raise _inactive() from None
        if result is None:
            raise _not_found()
        return to_admin_read(result[1])

    def deactivate(self, actor: Actor, plan_id: int, payload: PlanDesactivar) -> PlanAdminRead:
        try:
            result = self._repository.deactivate_plan(
                plan_id, audit=self._audit(actor, payload.motivo), motivo=payload.motivo)
        except PlanInactivoError:
            raise HTTPException(status.HTTP_409_CONFLICT, "El plan ya está desactivado.") from None
        except PlanBaseError:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "Es el plan base: toda cuenta nueva parte ahí, no se puede desactivar.") from None
        if result is None:
            raise _not_found()
        return to_admin_read(result[1])

    def reactivate(self, actor: Actor, plan_id: int, payload: PlanReactivar) -> PlanAdminRead:
        """Vuelve al catalogo un plan desactivado. Su nombre debe seguir libre entre los planes activos."""
        try:
            result = self._repository.reactivate_plan(plan_id, audit=self._audit(actor, payload.motivo))
        except PlanActivoError:
            raise HTTPException(status.HTTP_409_CONFLICT, "El plan ya está activo.") from None
        except PlanNombreDuplicadoError:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "No se puede reactivar: ya existe un plan activo con ese nombre.") from None
        if result is None:
            raise _not_found()
        return to_admin_read(result[1])

    def list_subscriptions(
        self, plan_id: int, estado: str, pagina: int, limit: int
    ) -> PlanSuscripcionPage:
        """Quienes tienen este plan como su ultimo pago: nombre corto, estado, inicio y termino."""
        if self._repository.get_plan_any(plan_id) is None:
            raise _not_found()
        rows, total = self._repository.list_suscripciones_plan(
            plan_id, estado=estado, limit=limit, offset=(pagina - 1) * limit)
        athletes = [r.usuario_id for r in rows if r.usuario_id is not None]
        names = self._names.names(athletes) if self._names is not None and athletes else {}
        items = [
            PlanSuscripcionRead(
                id=r.id, suscriptor=r.club_nombre if r.club_id else names.get(r.usuario_id),
                tipo_suscriptor="CLUB" if r.club_id else "USUARIO", estado=r.estado,
                plan_nombre=r.plan_nombre, precio_pagado=r.precio_pagado, fecha_inicio=r.fecha_inicio,
                fecha_fin=r.fecha_fin)
            for r in rows
        ]
        return PlanSuscripcionPage(items=items, pagina=pagina, limit=limit, total=total)

    def _audit(self, actor: Actor, motivo: str | None):
        def audit(action: str, before: StoredPlan | None, after: StoredPlan) -> None:
            try:
                if self._auditor is None:
                    raise PlanAuditUnavailable
                self._auditor.record(actor_id=actor.id, action=action, plan_id=after.id,
                                     motivo=motivo or _MOTIVOS[action], metadata=_metadata(before, after))
            except PlanAuditUnavailable:
                # Raised inside the repository's transaction: the change is rolled back.
                raise HTTPException(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "No se pudo registrar la auditoría; el cambio no se guardó. Inténtalo de nuevo.",
                ) from None
        return audit


def _normalize(text: str) -> str:
    return " ".join(text.split())


def _not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "Plan no encontrado.")


def _duplicate() -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, "Ya existe un plan activo con ese nombre.")


def _inactive() -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, "El plan está desactivado y no se puede editar.")
