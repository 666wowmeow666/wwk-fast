"""Авторизация клиентов и контроль доступа к заказам/документам.

Схема: номер заказа + последние 4 цифры ИНН организации-заказчика.
Это слабая защита (10 000 вариантов), поэтому:
  * лимит неудач и по Telegram ID, и ПО НОМЕРУ ЗАКАЗА (нельзя обойти сменой аккаунта);
  * неверный номер и неверный ИНН неразличимы (нет перебора номеров заказов);
  * опциональное подтверждение привязки менеджером (CLIENT_REQUIRE_MANAGER_APPROVAL);
  * каждый доступ к заказу/документу перепроверяется по CRM (принадлежность клиенту),
    данные из callback-кнопок не считаются доверенными.
"""
from __future__ import annotations

import dataclasses
import enum
import hmac
import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Callable

from wwk.crm.base import CrmAdapter
from wwk.crm.errors import CrmError, CrmNotFound
from wwk.crm.models import DocumentContent, DocumentInfo, OrderSummary

from .employees import AccessDenied, EmployeeService
from .models import AuditEntry, BindingStatus, ClientBinding, Employee, Permission, utcnow
from .repos import AttemptRepo, AuditRepo, BindingRepo

_ORDER_RE = re.compile(r"^[\w\-./ ]{1,40}$", re.UNICODE)
_INN4_RE = re.compile(r"^\d{4}$")


@dataclass(frozen=True)
class ClientAuthPolicy:
    max_fails_per_telegram: int = 5
    max_fails_per_order: int = 10
    lockout: timedelta = timedelta(minutes=30)
    binding_ttl: timedelta = timedelta(days=180)
    require_manager_approval: bool = False
    visible_doc_groups: frozenset[str] = frozenset()   # пусто = клиенту документы не показываются
    max_document_bytes: int = 20 * 1024 * 1024


class VerifyStatus(str, enum.Enum):
    OK = "ok"
    PENDING_APPROVAL = "pending_approval"
    INVALID = "invalid"              # неверный номер ИЛИ ИНН (не различаем)
    LOCKED = "locked"
    REVOKED = "revoked"
    BAD_INPUT = "bad_input"
    CRM_UNAVAILABLE = "crm_unavailable"


@dataclass(frozen=True)
class VerifyResult:
    status: VerifyStatus
    binding: ClientBinding | None = None
    retry_after_minutes: int | None = None


class DocumentTooLarge(Exception):
    pass


def normalize_order_number(raw: str) -> str | None:
    s = " ".join((raw or "").strip().split())
    return s if _ORDER_RE.match(s) else None


class ClientAuthService:
    def __init__(self, crm: CrmAdapter, bindings: BindingRepo, attempts: AttemptRepo, audit: AuditRepo,
                 policy: ClientAuthPolicy | None = None, clock: Callable = utcnow) -> None:
        self._crm, self._bindings, self._attempts, self._audit = crm, bindings, attempts, audit
        self.policy = policy or ClientAuthPolicy()
        self._clock = clock

    async def _log(self, actor: str, action: str, target: str | None = None, **details) -> None:
        await self._audit.add(AuditEntry(actor, action, target, details, self._clock()))

    # ---------- проверка ----------
    async def verify(self, telegram_id: int, raw_order_number: str, raw_inn4: str) -> VerifyResult:
        now = self._clock()
        number = normalize_order_number(raw_order_number)
        inn4 = (raw_inn4 or "").strip()
        if number is None or not _INN4_RE.match(inn4):
            return VerifyResult(VerifyStatus.BAD_INPUT)

        keys = (f"tg:{telegram_id}", f"order:{number.casefold()}")
        since = now - self.policy.lockout
        limits = (self.policy.max_fails_per_telegram, self.policy.max_fails_per_order)
        for key, limit in zip(keys, limits):
            if await self._attempts.count_since(key, since) >= limit:
                await self._log(f"client:{telegram_id}", "client.verify_locked", key)
                return VerifyResult(VerifyStatus.LOCKED, retry_after_minutes=int(self.policy.lockout.total_seconds() // 60))

        try:
            order = await self._crm.find_order_by_number(number)
            client = await self._crm.get_client(order.client_id) if order else None
        except CrmError:
            return VerifyResult(VerifyStatus.CRM_UNAVAILABLE)   # сбой CRM не считается попыткой клиента

        tax = client.tax_id if client else None
        ok = bool(order and client and tax and len(tax) >= 4 and hmac.compare_digest(tax[-4:], inn4))
        if not ok:
            for key in keys:
                await self._attempts.add_failure(key, now)
            await self._log(f"client:{telegram_id}", "client.verify_failed", keys[1])
            return VerifyResult(VerifyStatus.INVALID)

        for key in keys:
            await self._attempts.clear(key)
        org_key = order.client_id
        existing = await self._bindings.find(telegram_id, org_key)
        if existing is not None and existing.status is BindingStatus.REVOKED:
            return VerifyResult(VerifyStatus.REVOKED)       # отзыв нельзя обойти повторной проверкой
        if existing is not None and existing.is_valid(now):
            return VerifyResult(VerifyStatus.OK, existing)
        if existing is not None and existing.status is BindingStatus.PENDING:
            return VerifyResult(VerifyStatus.PENDING_APPROVAL, existing)

        status = BindingStatus.PENDING if self.policy.require_manager_approval else BindingStatus.ACTIVE
        new = ClientBinding(telegram_id, org_key, client.name, status, now, now + self.policy.binding_ttl,
                            id=existing.id if existing else None)
        if existing is None:
            new = await self._bindings.add(new)
        else:
            await self._bindings.save(new)
        await self._log(f"client:{telegram_id}", "client.bind", org_key, status=status.value, order=number)
        return VerifyResult(VerifyStatus.PENDING_APPROVAL if status is BindingStatus.PENDING else VerifyStatus.OK, new)

    # ---------- привязки ----------
    async def list_valid_bindings(self, telegram_id: int) -> list[ClientBinding]:
        now = self._clock()
        return [b for b in await self._bindings.list_for_telegram(telegram_id) if b.is_valid(now)]

    async def get_valid_binding(self, telegram_id: int, org_key: str) -> ClientBinding | None:
        b = await self._bindings.find(telegram_id, org_key)
        return b if b is not None and b.is_valid(self._clock()) else None

    async def approve_binding(self, actor: Employee, binding_id: int, employees: EmployeeService) -> ClientBinding:
        employees.require(actor, Permission.APPROVE_CLIENT_BINDING)
        b = await self._bindings.get(binding_id)
        if b is None or b.status is not BindingStatus.PENDING:
            raise ValueError("Заявка на привязку не найдена или уже обработана")
        now = self._clock()
        b = dataclasses.replace(b, status=BindingStatus.ACTIVE, expires_at=now + self.policy.binding_ttl)
        await self._bindings.save(b)
        await self._log(f"employee:{actor.telegram_id}", "client.binding_approved", str(binding_id))
        return b

    async def revoke_binding(self, actor: Employee, binding_id: int, employees: EmployeeService) -> None:
        employees.require(actor, Permission.APPROVE_CLIENT_BINDING)
        b = await self._bindings.get(binding_id)
        if b is None:
            raise ValueError("Привязка не найдена")
        b = dataclasses.replace(b, status=BindingStatus.REVOKED, revoked_at=self._clock(),
                                revoked_by=f"employee:{actor.telegram_id}")
        await self._bindings.save(b)
        await self._log(f"employee:{actor.telegram_id}", "client.binding_revoked", str(binding_id))

    async def list_pending(self, actor: Employee, employees: EmployeeService) -> list[ClientBinding]:
        employees.require(actor, Permission.APPROVE_CLIENT_BINDING)
        return await self._bindings.list_pending()

    # ---------- доступ к данным (каждый раз по CRM) ----------
    async def _deny(self, telegram_id: int, what: str, target: str) -> None:
        await self._log(f"client:{telegram_id}", "client.access_denied", target, what=what)
        raise AccessDenied("Нет доступа.")

    async def assert_order_access(self, telegram_id: int, org_key: str, order_id: str) -> OrderSummary:
        if await self.get_valid_binding(telegram_id, org_key) is None:
            await self._deny(telegram_id, "no_binding", str(order_id))
        try:
            details = await self._crm.get_order(str(order_id))
        except CrmNotFound:
            await self._deny(telegram_id, "order_not_found", str(order_id))
        if details.summary.client_id != org_key:
            await self._deny(telegram_id, "foreign_order", str(order_id))
        return details.summary

    async def find_order_for_client(self, telegram_id: int, org_key: str, raw_number: str) -> OrderSummary | None:
        """None - и если заказа нет, и если он чужой (ответ одинаков)."""
        if await self.get_valid_binding(telegram_id, org_key) is None:
            await self._deny(telegram_id, "no_binding", "search")
        number = normalize_order_number(raw_number)
        if number is None:
            return None
        order = await self._crm.find_order_by_number(number)
        if order is None or order.client_id != org_key:
            if order is not None:
                await self._log(f"client:{telegram_id}", "client.access_denied", order.order_id, what="foreign_order")
            return None
        return order

    async def list_client_documents(self, telegram_id: int, org_key: str, order_id: str) -> list[DocumentInfo]:
        await self.assert_order_access(telegram_id, org_key, order_id)
        if not self.policy.visible_doc_groups:
            return []
        docs = await self._crm.list_order_documents(str(order_id))
        return [d for d in docs if d.group is not None and d.group in self.policy.visible_doc_groups]

    async def get_client_document(self, telegram_id: int, org_key: str, order_id: str,
                                  document_id: str) -> DocumentContent:
        visible = await self.list_client_documents(telegram_id, org_key, order_id)
        info = next((d for d in visible if d.document_id == str(document_id)), None)
        if info is None:
            await self._deny(telegram_id, "document_not_visible", str(document_id))
        content = await self._crm.download_document(info)
        if len(content.content) > self.policy.max_document_bytes:
            raise DocumentTooLarge()
        await self._log(f"client:{telegram_id}", "client.document_download", str(document_id), order=str(order_id))
        return content
