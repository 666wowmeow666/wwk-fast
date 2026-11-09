"""Преобразование ответов 4logist в модели.

!!! ВАЖНО: в OpenAPI нет схем ответов. Имена полей ниже - ПРЕДПОЛОЖЕНИЯ (по именам полей форм).
Всё, что не удалось найти, приводит к CrmBadResponse (fail closed), а не к догадкам.
Сверка с реальным API: команда /crmprobe во внутреннем боте (показывает структуру без значений).
"""
from __future__ import annotations

import re
from typing import Any, Mapping

from .errors import CrmBadResponse
from .models import ClientInfo, CrmUser, DocumentInfo, OrderSummary

MAPPING_CONFIRMED = False

LIST_KEYS = ("items", "data", "results", "result", "orders", "clients", "documents", "users", "list")


def extract_items(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, Mapping):
        items = None
        for k in LIST_KEYS:
            if isinstance(payload.get(k), list):
                items = payload[k]
                break
        if items is None:
            raise CrmBadResponse("не найден список в ответе CRM")
    else:
        raise CrmBadResponse("ответ CRM не JSON-объект и не список")
    out = [i for i in items if isinstance(i, Mapping)]
    if len(out) != len(items):
        raise CrmBadResponse("в списке ответа CRM есть не-объекты")
    return out


def _pick(d: Mapping[str, Any], *names: str) -> Any:
    for n in names:
        if n in d and d[n] not in (None, ""):
            return d[n]
    return None


def _nested_id(v: Any) -> str | None:
    if isinstance(v, Mapping):
        v = _pick(v, "id", "clientId")
    if v is None or isinstance(v, (Mapping, list)):
        return None
    return str(v)


def _nested_text(v: Any) -> str | None:
    if isinstance(v, Mapping):
        v = _pick(v, "name", "title", "label")
    if v is None or isinstance(v, (Mapping, list)):
        return None
    return str(v)


def map_order(item: Mapping[str, Any]) -> OrderSummary:
    oid = _nested_id(_pick(item, "id", "orderId"))
    number = _pick(item, "orderCode", "code", "orderNumber", "number")
    client_id = _nested_id(_pick(item, "clientId", "client_id", "client"))
    if not (oid and number and client_id):
        raise CrmBadResponse("в заказе не найдены id / номер / клиент")
    return OrderSummary(oid, str(number), client_id, _nested_text(_pick(item, "status", "orderStatus")))


def only_digits(v: Any) -> str:
    return re.sub(r"\D", "", str(v)) if v is not None else ""


def map_client(item: Mapping[str, Any]) -> ClientInfo:
    cid = _nested_id(_pick(item, "id", "clientId"))
    if not cid:
        raise CrmBadResponse("в клиенте не найден id")
    name = _nested_text(_pick(item, "companyName", "company_name", "name"))
    tax = only_digits(_pick(item, "innCode", "inn_code", "inn", "companyCode", "company_code"))
    return ClientInfo(cid, name, tax if len(tax) >= 4 else None)


def map_document(item: Mapping[str, Any]) -> DocumentInfo:
    did = _nested_id(_pick(item, "id", "documentId"))
    if not did:
        raise CrmBadResponse("в документе не найден id")
    title = _nested_text(_pick(item, "name", "title", "fileName", "filename")) or f"Документ {did}"
    group = _nested_text(_pick(item, "group", "documentGroup", "groupId"))
    created = _nested_text(_pick(item, "created", "dateCreated", "createdAt", "date"))
    return DocumentInfo(did, title, group, created)


def map_user(item: Mapping[str, Any]) -> CrmUser:
    uid = _nested_id(_pick(item, "id", "userId"))
    if not uid:
        raise CrmBadResponse("в пользователе не найден id")
    name = _nested_text(_pick(item, "fullName", "name", "username"))
    return CrmUser(uid, name, _nested_text(_pick(item, "email")))


def describe_shape(payload: Any, depth: int = 4) -> Any:
    """Структура JSON БЕЗ значений (безопасно показывать в чате и присылать разработчику)."""
    if depth <= 0:
        return "…"
    if isinstance(payload, Mapping):
        return {str(k): describe_shape(v, depth - 1) for k, v in list(payload.items())[:40]}
    if isinstance(payload, list):
        return [describe_shape(payload[0], depth - 1)] if payload else []
    return type(payload).__name__
