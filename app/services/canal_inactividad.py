"""Hourly check of inactive club channels (G4).

A channel 30 days without messages notifies the club's administrators and
gets aviso_inactividad_en; without an answer in 7 days it is deleted. If the
club keeps it (mantener_hasta), the next notice waits until then. Any new
message cancels a pending notice (the repository clears it when inserting).

decidir() is the rule, a pure function of the channel and the time given.
revisar() applies it once under pg_try_advisory_lock, so with several
instances only one does the work. InactividadWorker runs revisar() every hour
in a daemon thread; a failure only goes to the log.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

from app.repositories.base import CanalInactividad, ClubRepository, OutboxMessage

logger = logging.getLogger(__name__)

INACTIVIDAD = timedelta(days=30)
PLAZO_RESPUESTA = timedelta(days=7)
INTERVALO_SEGUNDOS = 3600.0
ENV_ENABLED = "CANALES_INACTIVIDAD_ENABLED"

Decision = Literal["nada", "avisar", "eliminar"]


def decidir(canal: CanalInactividad, ahora: datetime) -> Decision:
    """What the check does with the channel at ahora."""
    if canal.aviso_inactividad_en is not None:
        return "eliminar" if ahora >= canal.aviso_inactividad_en + PLAZO_RESPUESTA else "nada"
    if canal.mantener_hasta is not None and ahora < canal.mantener_hasta:
        return "nada"
    return "avisar" if ahora >= canal.ultima_actividad + INACTIVIDAD else "nada"


@dataclass(frozen=True)
class Resultado:
    avisados: int = 0
    eliminados: int = 0
    bloqueado: bool = False  # another instance held the lock


def revisar(repository: ClubRepository, ahora: datetime) -> Resultado:
    """One pass over the channels to check, under the advisory lock."""
    with repository.inactividad_lock() as obtenido:
        if not obtenido:
            return Resultado(bloqueado=True)
        avisados = eliminados = 0
        for canal in repository.canales_por_revisar(ahora - INACTIVIDAD):
            decision = decidir(canal, ahora)
            if decision == "avisar" and repository.avisar_inactividad(canal, ahora, _aviso(canal)):
                avisados += 1
            elif decision == "eliminar" and repository.eliminar_canal_inactivo(canal):
                eliminados += 1
        return Resultado(avisados=avisados, eliminados=eliminados)


def _aviso(canal: CanalInactividad) -> OutboxMessage:
    return OutboxMessage(
        kind="canal",
        title="Canal sin actividad",
        body=f"«{canal.nombre}» lleva 30 días sin mensajes. ¿Lo eliminas o lo mantienes?",
        data={"screen": "canal_inactivo", "clubId": str(canal.club_id), "canalId": str(canal.id)},
    )


def enabled() -> bool:
    """On unless CANALES_INACTIVIDAD_ENABLED is false/0/no/off."""
    return os.getenv(ENV_ENABLED, "true").strip().lower() not in {"false", "0", "no", "off"}


class InactividadWorker:
    """Daemon thread running revisar() at start and then every interval."""

    def __init__(
        self,
        repository: ClubRepository,
        *,
        interval: float = INTERVALO_SEGUNDOS,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._repository = repository
        self._interval = interval
        self._clock = clock
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="canales-inactividad", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout)

    def run_once(self) -> Resultado | None:
        try:
            resultado = revisar(self._repository, self._clock())
        except Exception:  # noqa: BLE001 - a failed pass is logged and retried next time
            logger.exception("Falló la revisión de canales inactivos")
            return None
        if resultado.avisados or resultado.eliminados:
            logger.info(
                "Canales inactivos: %d avisados, %d eliminados",
                resultado.avisados, resultado.eliminados,
            )
        return resultado

    def _run(self) -> None:
        while not self._stop.is_set():
            self.run_once()
            self._stop.wait(self._interval)
