from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Role(str, enum.Enum):
    ADMIN = "ADMIN"
    MANAGER = "MANAGER"
    LOGISTICIAN = "LOGISTICIAN"


class Permission(str, enum.Enum):
    VIEW_OWN_REQUESTS = "view_own_requests"
    VIEW_ALL_REQUESTS = "view_all_requests"
    SEARCH = "search"
    CREATE_REQUEST = "create_request"
    COMMENT = "comment"
    MANAGE_RATES = "manage_rates"
    ASSIGN_RESPONSIBLE = "assign_responsible"
    APPROVE_CLIENT_BINDING = "approve_client_binding"
    AI_TEXT = "ai_text"
    AI_LOGIST = "ai_logist"
    MANAGE_STAFF = "manage_staff"
    VIEW_REPORTS = "view_reports"
    VIEW_AUDIT = "view_audit"
    CRM_DIAGNOSTICS = "crm_diagnostics"


@dataclass(frozen=True)
class Employee:
    telegram_id: int
    full_name: str
    roles: frozenset[Role] = frozenset()
    crm_user_id: str | None = None
    is_blocked: bool = False
    created_at: datetime = field(default_factory=utcnow)
    id: int | None = None


class BindingStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    PENDING = "PENDING"      # ждёт подтверждения менеджером
    REVOKED = "REVOKED"


@dataclass(frozen=True)
class ClientBinding:
    telegram_id: int
    org_key: str             # id клиента (организации) в CRM
    org_name: str | None
    status: BindingStatus
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None = None
    revoked_by: str | None = None
    id: int | None = None

    def is_valid(self, now: datetime) -> bool:
        return self.status is BindingStatus.ACTIVE and self.revoked_at is None and now < self.expires_at


@dataclass(frozen=True)
class AuditEntry:
    actor: str               # "employee:<tg_id>" | "client:<tg_id>" | "system"
    action: str
    target: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    at: datetime = field(default_factory=utcnow)
    id: int | None = None
