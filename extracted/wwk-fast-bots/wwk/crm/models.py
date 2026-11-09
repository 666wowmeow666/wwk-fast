from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class OrderSummary:
    order_id: str
    number: str
    client_id: str
    status: str | None = None


@dataclass(frozen=True)
class OrderDetails:
    summary: OrderSummary
    client_name: str | None = None
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class ClientInfo:
    client_id: str
    name: str | None
    tax_id: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class DocumentInfo:
    document_id: str
    title: str
    group: str | None = None
    created_at: str | None = None


@dataclass(frozen=True)
class DocumentContent:
    info: DocumentInfo
    content: bytes = field(repr=False)
    content_type: str | None = None


@dataclass(frozen=True)
class CrmUser:
    user_id: str
    name: str | None
    email: str | None = None
