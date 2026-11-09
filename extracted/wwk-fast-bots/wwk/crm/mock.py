"""MockFourlogistAdapter - демонстрационные данные. НИКОГДА не выдавать за данные CRM:
боты показывают предупреждение, пока is_mock=True."""
from __future__ import annotations

from typing import Any

from .base import CrmAdapter
from .errors import CrmNotFound
from .models import ClientInfo, CrmUser, DocumentContent, DocumentInfo, OrderDetails, OrderSummary

MOCK_DATA: dict[str, Any] = {
    "clients": {
        "1001": {"name": "ООО «Тест-Клиент А» (MOCK)", "tax_id": "7700000001"},
        "1002": {"name": "ООО «Тест-Клиент Б» (MOCK)", "tax_id": "7800000002"},
    },
    "orders": {
        "5001": {"number": "WWK-0001", "client_id": "1001", "status": "В пути (MOCK)"},
        "5002": {"number": "WWK-0002", "client_id": "1002", "status": "Погрузка (MOCK)"},
    },
    "documents": {
        "5001": [
            {"id": "9001", "title": "CMR (MOCK)", "group": "cmr", "content": b"%PDF-mock-cmr"},
            {"id": "9002", "title": "Внутренний расчёт (MOCK)", "group": "internal", "content": b"mock-internal"},
        ],
        "5002": [{"id": "9003", "title": "CMR (MOCK)", "group": "cmr", "content": b"%PDF-mock-cmr2"}],
    },
    "users": {"77": {"name": "Тест Менеджер (MOCK)", "email": "manager@example.invalid"}},
}


class MockFourlogistAdapter(CrmAdapter):
    is_mock = True

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        self.data = data if data is not None else MOCK_DATA

    def _summary(self, oid: str) -> OrderSummary:
        o = self.data["orders"].get(oid)
        if o is None:
            raise CrmNotFound(oid)
        return OrderSummary(oid, o["number"], o["client_id"], o.get("status"))

    async def find_order_by_number(self, number: str) -> OrderSummary | None:
        hits = [oid for oid, o in self.data["orders"].items()
                if o["number"].casefold() == number.strip().casefold()]
        return self._summary(hits[0]) if len(hits) == 1 else None

    async def get_order(self, order_id: str) -> OrderDetails:
        s = self._summary(order_id)
        c = self.data["clients"].get(s.client_id, {})
        return OrderDetails(s, c.get("name"), {"mock": True})

    async def get_client(self, client_id: str) -> ClientInfo | None:
        c = self.data["clients"].get(str(client_id))
        return ClientInfo(str(client_id), c["name"], c.get("tax_id")) if c else None

    async def list_order_documents(self, order_id: str) -> list[DocumentInfo]:
        return [DocumentInfo(d["id"], d["title"], d.get("group")) for d in self.data["documents"].get(order_id, [])]

    async def download_document(self, info: DocumentInfo) -> DocumentContent:
        for docs in self.data["documents"].values():
            for d in docs:
                if d["id"] == info.document_id:
                    return DocumentContent(info, d["content"], "application/octet-stream")
        raise CrmNotFound(info.document_id)

    async def list_users(self, phone: str | None = None, email: str | None = None) -> list[CrmUser]:
        return [CrmUser(uid, u["name"], u.get("email")) for uid, u in self.data["users"].items()]

    async def probe(self, key: str) -> Any:
        return {"mock": True, "items": [{"id": 1, "note": "демонстрационная структура, не из CRM"}]}

    async def ping(self) -> bool:
        return True
