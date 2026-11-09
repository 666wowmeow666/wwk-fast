"""Сотрудники: идентификация по Telegram ID (не по username), роли, блокировки."""
from __future__ import annotations

import dataclasses
from typing import Callable, Iterable

from .models import AuditEntry, Employee, Permission, Role, utcnow
from .repos import AuditRepo, EmployeeRepo
from .roles import permissions_for


class AccessDenied(Exception):
    """Нет доступа. Текст безопасен для показа пользователю."""


class EmployeeService:
    def __init__(self, repo: EmployeeRepo, audit: AuditRepo, clock: Callable = utcnow) -> None:
        self._repo, self._audit, self._clock = repo, audit, clock

    async def _log(self, actor: Employee | None, action: str, target: str | None = None, **details) -> None:
        who = f"employee:{actor.telegram_id}" if actor else "system"
        await self._audit.add(AuditEntry(who, action, target, details, self._clock()))

    async def bootstrap_admins(self, telegram_ids: Iterable[int]) -> None:
        """Первичные администраторы из ADMIN_TELEGRAM_IDS (создаются, только если их нет)."""
        for tg in telegram_ids:
            if await self._repo.get_by_telegram_id(tg) is None:
                await self._repo.add(Employee(tg, f"Администратор {tg}", frozenset({Role.ADMIN}),
                                              created_at=self._clock()))
                await self._log(None, "staff.bootstrap_admin", str(tg))

    async def authenticate(self, telegram_id: int) -> Employee:
        emp = await self._repo.get_by_telegram_id(telegram_id)
        if emp is None:
            raise AccessDenied("Вы не зарегистрированы как сотрудник.")
        if emp.is_blocked:
            raise AccessDenied("Доступ заблокирован.")
        if not emp.roles:
            raise AccessDenied("Вам не назначена роль.")
        return emp

    @staticmethod
    def has(emp: Employee, perm: Permission) -> bool:
        return (not emp.is_blocked) and perm in permissions_for(emp.roles)

    def require(self, emp: Employee, perm: Permission) -> None:
        if not self.has(emp, perm):
            raise AccessDenied("Недостаточно прав.")

    async def _target(self, tg: int) -> Employee:
        t = await self._repo.get_by_telegram_id(tg)
        if t is None:
            raise ValueError("Сотрудник не найден")
        return t

    async def _guard_last_admin(self, target: Employee, will_lose_admin: bool) -> None:
        if will_lose_admin and Role.ADMIN in target.roles and not target.is_blocked \
                and await self._repo.count_active_admins() <= 1:
            raise ValueError("Нельзя лишить доступа последнего администратора")

    async def register(self, actor: Employee, telegram_id: int, full_name: str, roles: Iterable[Role]) -> Employee:
        self.require(actor, Permission.MANAGE_STAFF)
        roles = frozenset(roles)
        if not roles:
            raise ValueError("Укажите хотя бы одну роль")
        if await self._repo.get_by_telegram_id(telegram_id):
            raise ValueError("Сотрудник с таким Telegram ID уже есть")
        emp = await self._repo.add(Employee(telegram_id, full_name.strip()[:200], roles, created_at=self._clock()))
        await self._log(actor, "staff.register", str(telegram_id), roles=sorted(r.value for r in roles))
        return emp

    async def set_crm_user(self, actor: Employee, telegram_id: int, crm_user_id: str) -> None:
        self.require(actor, Permission.MANAGE_STAFF)
        t = await self._target(telegram_id)
        await self._repo.save(dataclasses.replace(t, crm_user_id=crm_user_id.strip()))
        await self._log(actor, "staff.bind_crm_user", str(telegram_id), crm_user_id=crm_user_id.strip())

    async def grant_role(self, actor: Employee, telegram_id: int, role: Role) -> None:
        self.require(actor, Permission.MANAGE_STAFF)
        t = await self._target(telegram_id)
        await self._repo.save(dataclasses.replace(t, roles=t.roles | {role}))
        await self._log(actor, "staff.grant_role", str(telegram_id), role=role.value)

    async def revoke_role(self, actor: Employee, telegram_id: int, role: Role) -> None:
        self.require(actor, Permission.MANAGE_STAFF)
        t = await self._target(telegram_id)
        await self._guard_last_admin(t, role is Role.ADMIN)
        await self._repo.save(dataclasses.replace(t, roles=t.roles - {role}))
        await self._log(actor, "staff.revoke_role", str(telegram_id), role=role.value)

    async def block(self, actor: Employee, telegram_id: int) -> None:
        self.require(actor, Permission.MANAGE_STAFF)
        t = await self._target(telegram_id)
        await self._guard_last_admin(t, True)
        await self._repo.save(dataclasses.replace(t, is_blocked=True))
        await self._log(actor, "staff.block", str(telegram_id))

    async def unblock(self, actor: Employee, telegram_id: int) -> None:
        self.require(actor, Permission.MANAGE_STAFF)
        t = await self._target(telegram_id)
        await self._repo.save(dataclasses.replace(t, is_blocked=False))
        await self._log(actor, "staff.unblock", str(telegram_id))

    async def list_staff(self, actor: Employee) -> list[Employee]:
        self.require(actor, Permission.MANAGE_STAFF)
        return await self._repo.list_all()

    async def recent_audit(self, actor: Employee, limit: int = 20) -> list[AuditEntry]:
        self.require(actor, Permission.VIEW_AUDIT)
        return await self._audit.list_recent(limit)
