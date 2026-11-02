"""HU-56: audit of plan changes through the internal endpoint of Ms_Users (HU-48).

The plans live in this service's database, so the change and the audit cannot share one SQL transaction. The
repository calls the auditor INSIDE the transaction that changes the plan, before it commits: if the audit cannot
be written, the exception rolls the change back and the request answers 503. The reverse failure (the audit is
written and the commit then fails) leaves one extra audit row and no change; it is the safe side."""
from __future__ import annotations

import logging
from typing import Protocol
from uuid import UUID

import httpx

logger = logging.getLogger(__name__)

AUDIT_PATH = "/internal/v1/audit/events"
MIN_TOKEN_BYTES = 32


class PlanAuditUnavailable(Exception):
    """The audit entry could not be written (Ms_Users down, secret missing or refused)."""


class PlanAuditor(Protocol):
    def record(self, *, actor_id: UUID, action: str, plan_id: int, motivo: str,
               metadata: dict[str, str | int | bool | None]) -> None: ...


class InternalAuditClient:
    """POST /internal/v1/audit/events with the service-to-service secret (X-Internal-Service-Token)."""

    def __init__(self, client: httpx.Client, token: str | None) -> None:
        self._client = client
        self._token = token if token and len(token.encode("utf-8")) >= MIN_TOKEN_BYTES else None

    def record(self, *, actor_id: UUID, action: str, plan_id: int, motivo: str,
               metadata: dict[str, str | int | bool | None]) -> None:
        if self._token is None:
            logger.error("INTERNAL_SERVICE_TOKEN is not configured: plan changes cannot be audited")
            raise PlanAuditUnavailable
        body = {"actor_user_id": str(actor_id), "action": action, "resource_type": "plan",
                "resource_id": str(plan_id), "motivo": motivo, "result": "success", "metadata": metadata}
        try:
            response = self._client.post(AUDIT_PATH, json=body, headers={"X-Internal-Service-Token": self._token})
        except httpx.HTTPError as error:
            logger.error("Ms_Users did not answer the audit request: %s", type(error).__name__)
            raise PlanAuditUnavailable from error
        if response.status_code != 204:
            logger.error("Ms_Users refused the audit entry: HTTP %s", response.status_code)
            raise PlanAuditUnavailable
