from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .models import ClientInfo, CrmUser, DocumentContent, DocumentInfo, OrderDetails, OrderSummary


class CrmAdapter(ABC):
    """Интерфейс операций с CRM. Реализации: FourlogistClient (real), MockFourlogistAdapter (mock)."""

    is_mock: bool = False

    @abstractmethod
    async def find_order_by_number(self, number: str) -> OrderSummary | None:
        """Точное совпадение номера заказа. Неоднозначность = None (fail closed)."""

    @abstractmethod
    async def get_order(self, order_id: str) -> OrderDetails: ...

    @abstractmethod
    async def get_client(self, client_id: str) -> ClientInfo | None: ...

    @abstractmethod
    async def list_order_documents(self, order_id: str) -> list[DocumentInfo]: ...

    @abstractmethod
    async def download_document(self, info: DocumentInfo) -> DocumentContent: ...

    @abstractmethod
    async def list_users(self, phone: str | None = None, email: str | None = None) -> list[CrmUser]: ...

    @abstractmethod
    async def probe(self, key: str) -> Any:
        """Сырой ответ одного из PROBE_KEYS (limit=1) - для сверки структуры ответов."""

    @abstractmethod
    async def ping(self) -> bool: ...
