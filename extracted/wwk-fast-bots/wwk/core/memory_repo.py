"""Хранилище в памяти: для тестов и проверки запуска с пустой базой. НЕ для продакшена."""
from __future__ import annotations

import dataclasses
from datetime import datetime

from .models import AuditEntry, BindingStatus, ClientBinding, Employee, Role


class MemoryEmployeeRepo:
    def __init__(self) -> None:
        self._by_tg: dict[int, Employee] = {}
        self._seq = 0

    async def get_by_telegram_id(self, telegram_id):
        return self._by_tg.get(telegram_id)

    async def add(self, emp):
        if emp.telegram_id in self._by_tg:
            raise ValueError("duplicate telegram_id")
        self._seq += 1
        emp = dataclasses.replace(emp, id=self._seq)
        self._by_tg[emp.telegram_id] = emp
        return emp

    async def save(self, emp):
        self._by_tg[emp.telegram_id] = emp

    async def list_all(self):
        return sorted(self._by_tg.values(), key=lambda e: e.id or 0)

    async def count_active_admins(self):
        return sum(1 for e in self._by_tg.values() if Role.ADMIN in e.roles and not e.is_blocked)


class MemoryBindingRepo:
    def __init__(self) -> None:
        self._items: dict[int, ClientBinding] = {}
        self._seq = 0

    async def get(self, binding_id):
        return self._items.get(binding_id)

    async def find(self, telegram_id, org_key):
        return next((b for b in self._items.values()
                     if b.telegram_id == telegram_id and b.org_key == org_key), None)

    async def list_for_telegram(self, telegram_id):
        return [b for b in self._items.values() if b.telegram_id == telegram_id]

    async def list_pending(self):
        return [b for b in self._items.values() if b.status is BindingStatus.PENDING]

    async def add(self, b):
        self._seq += 1
        b = dataclasses.replace(b, id=self._seq)
        self._items[b.id] = b
        return b

    async def save(self, b):
        self._items[b.id] = b


class MemoryAttemptRepo:
    def __init__(self) -> None:
        self._items: dict[str, list[datetime]] = {}

    async def add_failure(self, key, at):
        self._items.setdefault(key, []).append(at)

    async def count_since(self, key, since):
        return sum(1 for t in self._items.get(key, []) if t >= since)

    async def clear(self, key):
        self._items.pop(key, None)


class MemoryAuditRepo:
    def __init__(self) -> None:
        self.entries: list[AuditEntry] = []

    async def add(self, entry):
        self.entries.append(dataclasses.replace(entry, id=len(self.entries) + 1))

    async def list_recent(self, limit=20):
        return list(reversed(self.entries))[:limit]
