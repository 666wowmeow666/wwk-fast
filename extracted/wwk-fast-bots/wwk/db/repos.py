"""Реализации хранилищ на PostgreSQL (SQLAlchemy 2, async)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from wwk.core.models import AuditEntry, BindingStatus, ClientBinding, Employee, Role

from .models import AuditLogRow, AuthAttemptRow, ClientBindingRow, EmployeeRoleRow, EmployeeRow


class SqlEmployeeRepo:
    def __init__(self, sf: async_sessionmaker) -> None:
        self._sf = sf

    @staticmethod
    def _to(row: EmployeeRow, roles: list[str]) -> Employee:
        return Employee(row.telegram_id, row.full_name, frozenset(Role(r) for r in roles),
                        row.crm_user_id, row.is_blocked, row.created_at, row.id)

    async def _roles(self, s, employee_id: int) -> list[str]:
        return list((await s.scalars(select(EmployeeRoleRow.role).where(EmployeeRoleRow.employee_id == employee_id))).all())

    async def get_by_telegram_id(self, telegram_id: int) -> Employee | None:
        async with self._sf() as s:
            row = await s.scalar(select(EmployeeRow).where(EmployeeRow.telegram_id == telegram_id))
            return self._to(row, await self._roles(s, row.id)) if row else None

    async def add(self, emp: Employee) -> Employee:
        async with self._sf() as s, s.begin():
            row = EmployeeRow(telegram_id=emp.telegram_id, full_name=emp.full_name, crm_user_id=emp.crm_user_id,
                              is_blocked=emp.is_blocked, created_at=emp.created_at)
            s.add(row)
            await s.flush()
            s.add_all(EmployeeRoleRow(employee_id=row.id, role=r.value) for r in emp.roles)
            return self._to(row, [r.value for r in emp.roles])

    async def save(self, emp: Employee) -> None:
        async with self._sf() as s, s.begin():
            row = await s.scalar(select(EmployeeRow).where(EmployeeRow.telegram_id == emp.telegram_id))
            if row is None:
                raise ValueError("employee not found")
            row.full_name, row.crm_user_id, row.is_blocked = emp.full_name, emp.crm_user_id, emp.is_blocked
            await s.execute(delete(EmployeeRoleRow).where(EmployeeRoleRow.employee_id == row.id))
            s.add_all(EmployeeRoleRow(employee_id=row.id, role=r.value) for r in emp.roles)

    async def list_all(self) -> list[Employee]:
        async with self._sf() as s:
            rows = (await s.scalars(select(EmployeeRow).order_by(EmployeeRow.id))).all()
            return [self._to(r, await self._roles(s, r.id)) for r in rows]

    async def count_active_admins(self) -> int:
        async with self._sf() as s:
            q = (select(func.count(func.distinct(EmployeeRow.id)))
                 .join(EmployeeRoleRow, EmployeeRoleRow.employee_id == EmployeeRow.id)
                 .where(EmployeeRoleRow.role == Role.ADMIN.value, EmployeeRow.is_blocked.is_(False)))
            return int(await s.scalar(q) or 0)


class SqlBindingRepo:
    def __init__(self, sf: async_sessionmaker) -> None:
        self._sf = sf

    @staticmethod
    def _to(r: ClientBindingRow) -> ClientBinding:
        return ClientBinding(r.telegram_id, r.org_key, r.org_name, BindingStatus(r.status), r.created_at,
                             r.expires_at, r.revoked_at, r.revoked_by, r.id)

    async def get(self, binding_id: int):
        async with self._sf() as s:
            r = await s.get(ClientBindingRow, binding_id)
            return self._to(r) if r else None

    async def find(self, telegram_id: int, org_key: str):
        async with self._sf() as s:
            r = await s.scalar(select(ClientBindingRow).where(ClientBindingRow.telegram_id == telegram_id,
                                                              ClientBindingRow.org_key == org_key))
            return self._to(r) if r else None

    async def list_for_telegram(self, telegram_id: int):
        async with self._sf() as s:
            rows = (await s.scalars(select(ClientBindingRow).where(ClientBindingRow.telegram_id == telegram_id)
                                    .order_by(ClientBindingRow.id))).all()
            return [self._to(r) for r in rows]

    async def list_pending(self):
        async with self._sf() as s:
            rows = (await s.scalars(select(ClientBindingRow).where(ClientBindingRow.status == BindingStatus.PENDING.value)
                                    .order_by(ClientBindingRow.id))).all()
            return [self._to(r) for r in rows]

    async def add(self, b: ClientBinding) -> ClientBinding:
        async with self._sf() as s, s.begin():
            row = ClientBindingRow(telegram_id=b.telegram_id, org_key=b.org_key, org_name=b.org_name,
                                   status=b.status.value, created_at=b.created_at, expires_at=b.expires_at,
                                   revoked_at=b.revoked_at, revoked_by=b.revoked_by)
            s.add(row)
            await s.flush()
            return self._to(row)

    async def save(self, b: ClientBinding) -> None:
        async with self._sf() as s, s.begin():
            row = await s.get(ClientBindingRow, b.id)
            if row is None:
                raise ValueError("binding not found")
            row.org_name, row.status = b.org_name, b.status.value
            row.created_at, row.expires_at = b.created_at, b.expires_at
            row.revoked_at, row.revoked_by = b.revoked_at, b.revoked_by


class SqlAttemptRepo:
    def __init__(self, sf: async_sessionmaker) -> None:
        self._sf = sf

    async def add_failure(self, key: str, at: datetime) -> None:
        async with self._sf() as s, s.begin():
            s.add(AuthAttemptRow(key=key, at=at))

    async def count_since(self, key: str, since: datetime) -> int:
        async with self._sf() as s:
            return int(await s.scalar(select(func.count()).select_from(AuthAttemptRow)
                                      .where(AuthAttemptRow.key == key, AuthAttemptRow.at >= since)) or 0)

    async def clear(self, key: str) -> None:
        async with self._sf() as s, s.begin():
            await s.execute(delete(AuthAttemptRow).where(AuthAttemptRow.key == key))


class SqlAuditRepo:
    def __init__(self, sf: async_sessionmaker) -> None:
        self._sf = sf

    async def add(self, e: AuditEntry) -> None:
        async with self._sf() as s, s.begin():
            s.add(AuditLogRow(at=e.at, actor=e.actor, action=e.action, target=e.target, details=e.details or {}))

    async def list_recent(self, limit: int = 20) -> list[AuditEntry]:
        async with self._sf() as s:
            rows = (await s.scalars(select(AuditLogRow).order_by(AuditLogRow.id.desc()).limit(limit))).all()
            return [AuditEntry(r.actor, r.action, r.target, dict(r.details or {}), r.at, r.id) for r in rows]
