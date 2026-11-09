"""Реестр методов 4logist, реально присутствующих в docs/4logist_openapi.json.

Безопасность:
  READ_SAFE - операция чтения по описанию в документации; допускает автоповтор.
              (В 4logist списки читаются через POST - метод HTTP не равен "запись".)
  WRITE     - меняет данные; никогда не повторяется автоматически.
  BLOCKED   - GET-методы, которые по описанию МЕНЯЮТ состояние (отправка письма,
              смена статуса рейса). Клиент их не вызывает вообще.
Тест tests/test_endpoints.py сверяет реестр с OpenAPI-файлом.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Safety(str, Enum):
    READ_SAFE = "read_safe"
    WRITE = "write"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class Endpoint:
    key: str
    method: str
    path: str
    safety: Safety
    note: str = ""


def _e(key, method, path, safety, note=""):
    return key, Endpoint(key, method, path, safety, note)


ENDPOINTS: dict[str, Endpoint] = dict([
    # --- чтение ---
    _e("orders_list", "POST", "/api/orders/list", Safety.READ_SAFE, "фильтр form_order_api_filter[...] (multipart)"),
    _e("order_all_info", "GET", "/api/orders/{orderId}/all-info", Safety.READ_SAFE, "структура ответа в документации не описана"),
    _e("clients_list", "POST", "/api/clients/list", Safety.READ_SAFE, "фильтр form[clientId], form[companyCode], form[phone]"),
    _e("requests_list", "POST", "/api/requests/list", Safety.READ_SAFE, "фильтров в документации нет"),
    _e("requests_archive_list", "POST", "/api/requests/archive-list", Safety.READ_SAFE),
    _e("users_list", "POST", "/api/users/list", Safety.READ_SAFE, "фильтр по phone/email"),
    _e("users_info", "GET", "/api/users/info", Safety.READ_SAFE),
    _e("comments_list", "POST", "/api/comments/list", Safety.READ_SAFE, "фильтр clientId/tripId/consignmentId"),
    _e("tasks_list", "POST", "/api/tasks/list", Safety.READ_SAFE),
    _e("order_documents_list", "POST", "/api/order-documents/list", Safety.READ_SAFE, "фильтр form_document_api_filter[order|trip|group|...]"),
    _e("order_document_get", "GET", "/api/order-documents/get-document/{document}", Safety.READ_SAFE, "бинарное содержимое; проверки принадлежности на стороне API не описаны"),
    _e("order_consignments_list", "POST", "/api/order-consignments/list", Safety.READ_SAFE),
    _e("trips_list", "POST", "/api/trips/list", Safety.READ_SAFE),
    _e("trip_current_status", "GET", "/api/trips/{trip}/current-status", Safety.READ_SAFE, "trip указан и в пути, и в query"),
    _e("trip_statuses", "GET", "/api/trips/{trip}/statuses", Safety.READ_SAFE),
    _e("order_statuses_list", "POST", "/api/order-statuses/list", Safety.READ_SAFE),
    _e("request_statuses_list", "POST", "/api/request-statuses/list", Safety.READ_SAFE),
    # --- запись (этап B+; по умолчанию запрещены CRM_ALLOW_WRITES=0) ---
    _e("comments_new", "POST", "/api/comments/new", Safety.WRITE, "НЕ ПОДТВЕРЖДЕНО на реальном API"),
    _e("requests_new", "POST", "/api/requests/new", Safety.WRITE, "НЕ ПОДТВЕРЖДЕНО на реальном API"),
    _e("orders_edit", "POST", "/api/orders/edit/{id}", Safety.WRITE, "НЕ ПОДТВЕРЖДЕНО на реальном API"),
    _e("orders_update_additional_manager", "POST", "/api/orders/{order}/updateAdditionalManager", Safety.WRITE, "НЕ ПОДТВЕРЖДЕНО"),
    # --- GET, которые меняют состояние: вызывать нельзя ---
    _e("invoices_send", "GET", "/api/invoices/send/{invoice}", Safety.BLOCKED, "отправляет счёт клиенту по email"),
    _e("act_send", "GET", "/api/order-documents/{invoiceId}/act/send/{documentTemplateId}", Safety.BLOCKED, "отправляет акт клиенту"),
    _e("warehouse_handle_arrival", "GET", "/api/warehouses/handle-arrival/{trip}", Safety.BLOCKED, "меняет статус рейса"),
    _e("warehouse_trip_handle_departure", "GET", "/api/warehouses/trips/{trip}/handle-departure", Safety.BLOCKED, "завершает загрузку"),
    _e("warehouse_trip_handle_arrival", "GET", "/api/warehouses/trips/{trip}/handle-arrival", Safety.BLOCKED, "завершает разгрузку"),
])

# Эндпоинты-списки, которые разрешено дёргать диагностикой /crmprobe
PROBE_KEYS = ("users_list", "orders_list", "clients_list", "requests_list")
