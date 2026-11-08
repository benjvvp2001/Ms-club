"""HU-68: what changed in an edited convocatoria, and the single notice the applicants get.

Pure functions shared by both repositories, so the PostgreSQL adapter and the mock decide exactly the same.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.repositories.base import OutboxMessage, StoredConvocatoria

CHILE = ZoneInfo("America/Santiago")

# Fields the applicants must hear about: when (fecha_prueba), where (direccion, comuna), what to bring (requisitos)
# and the sport. Everything else (title, description, age range, cupos, chat switch, category) is a minor detail.
IMPORTANT_FIELDS = ("fecha_prueba", "direccion", "comuna", "requisitos", "deporte_id")


def current_value(convocatoria: StoredConvocatoria, field: str) -> Any:
    return convocatoria.deporte.id if field == "deporte_id" else getattr(convocatoria, field)


def _same(old: Any, new: Any) -> bool:
    if isinstance(old, datetime) and isinstance(new, datetime):
        return old == new  # aware datetimes compare by instant, whatever their offset
    if isinstance(old, list) or isinstance(new, list):
        return list(old or []) == list(new or [])
    return old == new


def diff(before: StoredConvocatoria, changes: dict[str, Any]) -> dict[str, tuple[Any, Any]]:
    """field -> (old, new) for the fields whose value really changes. Sending a value that is already there is
    not a change, so repeating the same request changes nothing and warns nobody."""
    result = {}
    for field, new in changes.items():
        old = current_value(before, field)
        if not _same(old, new):
            result[field] = (old, new)
    return result


def _json(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


def history_entry(changes: dict[str, tuple[Any, Any]]) -> dict[str, dict[str, Any]]:
    return {field: {"antes": _json(old), "despues": _json(new)} for field, (old, new) in changes.items()}


def _fecha_texto(value: datetime) -> str:
    local = value.astimezone(CHILE)
    return f"{local:%d-%m-%Y} a las {local:%H:%M}"


def build_aviso(before: StoredConvocatoria, changes: dict[str, tuple[Any, Any]]) -> OutboxMessage | None:
    """One notice per edit, however many important fields changed; None when only minor ones did."""
    important = [field for field in IMPORTANT_FIELDS if field in changes]
    if not important:
        return None
    parts: list[str] = []
    if "fecha_prueba" in changes:
        parts.append(f"la fecha (ahora {_fecha_texto(changes['fecha_prueba'][1])})")
    if "direccion" in changes or "comuna" in changes:
        direccion = changes["direccion"][1] if "direccion" in changes else before.direccion
        comuna = changes["comuna"][1] if "comuna" in changes else before.comuna
        parts.append(f"el lugar (ahora {direccion}{', ' + comuna if comuna else ''})")
    if "requisitos" in changes:
        parts.append("los requisitos")
    if "deporte_id" in changes:
        parts.append("el deporte")
    cambios = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " y " + parts[-1]
    prueba = f"«{before.titulo}»" if before.titulo else f"la prueba de {before.deporte.nombre} {before.categoria_edad}"
    lugares = {"fecha_prueba": "fecha", "direccion": "lugar", "comuna": "lugar", "requisitos": "requisitos",
               "deporte_id": "deporte"}
    return OutboxMessage(
        kind="convocatoria",
        title="Cambió una convocatoria a la que postulaste",
        body=f"{before.club_nombre} modificó {prueba}: cambió {cambios}.",
        data={
            "screen": "convocatoria",
            "convocatoriaId": str(before.id),
            "cambios": sorted({lugares[field] for field in important}),
        },
    )
