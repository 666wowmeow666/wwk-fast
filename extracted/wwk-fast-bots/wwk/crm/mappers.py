"""Маппинг ответов 4logist во внутренние модели.

Максимально терпим к разным названиям полей: сначала пробуем документированные/
ожидаемые ключи, затем типовые варианты из реальных ответов.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from .errors import CrmBadResponse
from .models import ClientInfo, CrmUser, DocumentInfo, OrderSummary

MAPPING_CONFIRMED = False

LIST_KEYS = (
    "items",
    "data",
    "results",
    "result",
    "orders",
    "clients",
    "documents",
    "users",
    "list",
    "rows",
    "records",
)


def extract_items(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, Mapping):
        items = None

        for k in LIST_KEYS:
            v = payload.get(k)
            if isinstance(v, list):
                items = v
                break

        if items is None:
            # Запасной вариант: ищем любой список объектов на первом уровне.
            for v in payload.values():
                if isinstance(v, list) and v and all(isinstance(i, Mapping) for i in v[:10]):
                    items = v
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
        v = _pick(
            v,
            "id",
            "clientId",
            "client_id",
            "orderId",
            "order_id",
            "userId",
            "user_id",
        )

    if v is None or isinstance(v, (Mapping, list)):
        return None

    s = str(v).strip()
    return s or None


def _nested_text(v: Any) -> str | None:
    if isinstance(v, Mapping):
        v = _pick(
            v,
            "name",
            "title",
            "label",
            "companyName",
            "company_name",
            "fullName",
            "full_name",
        )

    if v is None or isinstance(v, (Mapping, list)):
        return None

    s = str(v).strip()
    return s or None


def only_digits(v: Any) -> str:
    if v is None or isinstance(v, (Mapping, list, tuple, set, frozenset)):
        return ""

    return re.sub(r"\D", "", str(v))


def map_order(item: Mapping[str, Any]) -> OrderSummary:
    oid = _nested_id(_pick(item, "id", "orderId", "order_id"))
    if not oid:
        raise CrmBadResponse(f"в заказе не найден id; keys={sorted(item.keys())[:20]}")

    number_raw = _pick(
        item,
        "orderCode",
        "order_code",
        "code",
        "orderNumber",
        "order_number",
        "number",
    )

    number = _nested_text(number_raw) if isinstance(number_raw, Mapping) else None

    if number is None and number_raw is not None and not isinstance(number_raw, (list, tuple, set, frozenset)):
        number = str(number_raw).strip()

    # Если номер вдруг не вернулся отдельно, используем id как запасной номер.
    if not number:
        number = oid

    client_id = _nested_id(_pick(item, "clientId", "client_id", "client"))
    if not client_id:
        raise CrmBadResponse(f"в заказе не найден клиент; keys={sorted(item.keys())[:20]}")

    status = _nested_text(
        _pick(
            item,
            "status",
            "orderStatus",
            "order_status",
            "state",
        )
    )

    return OrderSummary(oid, number, client_id, status)


def map_client(item: Mapping[str, Any]) -> ClientInfo:
    cid = _nested_id(
        _pick(
            item,
            "id",
            "clientId",
            "client_id",
            "companyId",
            "company_id",
        )
    )

    if not cid:
        raise CrmBadResponse(f"в клиенте не найден id; keys={sorted(item.keys())[:20]}")

    name = _nested_text(
        _pick(
            item,
            "companyName",
            "company_name",
            "name",
            "title",
            "company_name_original",
            "fullName",
            "full_name",
        )
    )

    tax = only_digits(
        _pick(
            item,
            "innCode",
            "inn_code",
            "inn",
            "companyCode",
            "company_code",
            "tin",
            "taxId",
            "tax_id",
            "company_vat_code",
            "vat",
            "vatCode",
            "vat_code",
        )
    )

    # Аккуратный запасной поиск: только по именам полей, похожим на ИНН/код компании.
    if len(tax) < 4:
        for k, v in item.items():
            key = str(k).lower()
            if any(marker in key for marker in ("inn", "company_code", "companycode", "tax", "vat")):
                cand = only_digits(v)
                if len(cand) >= 4:
                    tax = cand
                    break

    return ClientInfo(cid, name, tax if len(tax) >= 4 else None)


def map_document(item: Mapping[str, Any]) -> DocumentInfo:
    did = _nested_id(_pick(item, "id", "documentId", "document_id"))
    if not did:
        raise CrmBadResponse("в документе не найден id")

    title = _nested_text(
        _pick(
            item,
            "name",
            "title",
            "fileName",
            "filename",
            "file_name",
        )
    ) or f"Документ {did}"

    group = _nested_text(
        _pick(
            item,
            "group",
            "documentGroup",
            "document_group",
            "groupId",
            "group_id",
        )
    )

    created = _nested_text(
        _pick(
            item,
            "created",
            "dateCreated",
            "date_created",
            "createdAt",
            "created_at",
            "date",
        )
    )

    return DocumentInfo(did, title, group, created)


def map_user(item: Mapping[str, Any]) -> CrmUser:
    uid = _nested_id(_pick(item, "id", "userId", "user_id"))
    if not uid:
        raise CrmBadResponse("в пользователе не найден id")

    name = _nested_text(
        _pick(
            item,
            "fullName",
            "full_name",
            "name",
            "username",
        )
    )

    email = _nested_text(
        _pick(
            item,
            "email",
            "googleEmail",
            "google_email",
        )
    )

    return CrmUser(uid, name, email)


def describe_shape(payload: Any, depth: int = 4) -> Any:
    """Структура JSON БЕЗ значений (безопасно показывать в чате и присылать разработчику)."""

    if depth <= 0:
        return "…"

    if isinstance(payload, Mapping):
        return {str(k): describe_shape(v, depth - 1) for k, v in list(payload.items())[:40]}

    if isinstance(payload, list):
        return [describe_shape(payload[0], depth - 1)] if payload else []

    return type(payload).__name__
