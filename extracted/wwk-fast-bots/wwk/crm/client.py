"""FourlogistClient - реальный клиент CRM 4logist.

Принципы:
  * вызов только по ключу из реестра ENDPOINTS (сырых запросов наружу нет);
  * автоповтор ТОЛЬКО для READ_SAFE; запись - одна попытка, при неизвестном исходе
    CrmWriteOutcomeUnknown; BLOCKED не вызывается; запись требует allow_writes;
  * таймауты, логи без токенов и без тел запросов/ответов;
  * способ аутентификации в OpenAPI не описан - заголовок/схема настраиваются (НЕ ПОДТВЕРЖДЕНО).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Mapping, Protocol, Sequence
from urllib.parse import quote

from . import mappers
from .base import CrmAdapter
from .endpoints import ENDPOINTS, PROBE_KEYS, Endpoint, Safety
from .errors import (
    CrmAuthError,
    CrmBadResponse,
    CrmError,
    CrmForbiddenOperation,
    CrmNotFound,
    CrmTimeout,
    CrmUnavailable,
    CrmWriteOutcomeUnknown,
)
from .models import (
    ClientInfo,
    CrmUser,
    DocumentContent,
    DocumentInfo,
    OrderDetails,
    OrderSummary,
)
from .oauth import OAuthTokenProvider

log = logging.getLogger("wwk.crm")

RETRYABLE_STATUS = {502, 503, 504}


@dataclass
class HttpResponse:
    status: int
    body: bytes = field(default=b"", repr=False)
    headers: Mapping[str, str] = field(default_factory=dict)


class TransportTimeout(Exception):
    pass


class TransportError(Exception):
    pass


class HttpTransport(Protocol):
    async def request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, str],
        form: Sequence[tuple[str, str]],
        headers: Mapping[str, str],
        timeout: float,
    ) -> HttpResponse:
        ...


class HttpxTransport:
    """Транспорт на httpx (импорт лениво - ядро и тесты работают без httpx)."""

    def __init__(self) -> None:
        import httpx  # noqa: PLC0415

        self._httpx = httpx
        self._client = httpx.AsyncClient(follow_redirects=False)

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, str],
        form: Sequence[tuple[str, str]],
        headers: Mapping[str, str],
        timeout: float,
    ) -> HttpResponse:
        h = self._httpx
        files = [(k, (None, v)) for k, v in form] if form else None  # multipart/form-data, как в OpenAPI

        try:
            r = await self._client.request(
                method,
                url,
                params=dict(params),
                files=files,
                headers=dict(headers),
                timeout=timeout,
            )
        except h.TimeoutException as e:
            raise TransportTimeout(type(e).__name__) from None
        except h.HTTPError as e:
            raise TransportError(type(e).__name__) from None

        return HttpResponse(r.status_code, r.content, dict(r.headers))

    async def aclose(self) -> None:
        await self._client.aclose()


def build_form(fields: Mapping[str, Any] | None) -> list[tuple[str, str]]:
    """dict -> пары multipart; None и пустые значения отбрасываются; списки -> key[]."""

    out: list[tuple[str, str]] = []

    for k, v in (fields or {}).items():
        if v is None or v == "":
            continue

        if isinstance(v, (list, tuple, set, frozenset)):
            out.extend((f"{k}[]", str(i)) for i in v)
        else:
            out.append((k, str(v)))

    return out


class FourlogistClient(CrmAdapter):
    is_mock = False

    def __init__(
        self,
        base_url: str,
        token: str | None = None,
        *,
        transport: HttpTransport | None = None,
        token_provider: OAuthTokenProvider | None = None,
        auth_header: str = "Authorization",
        auth_scheme: str = "Bearer",
        timeout: float = 15.0,
        max_read_retries: int = 2,
        allow_writes: bool = False,
        backoff: float = 0.5,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if not base_url or not (token or token_provider):
            raise ValueError("base_url и token/token_provider обязательны")

        self._base = base_url.rstrip("/")
        self._token = token or ""
        self._token_provider = token_provider
        self._transport = transport or HttpxTransport()
        self._auth_header = auth_header
        self._auth_scheme = auth_scheme
        self._timeout = timeout
        self._retries = max(0, max_read_retries)
        self._allow_writes = allow_writes
        self._backoff = backoff
        self._sleep = sleep

    def __repr__(self) -> str:
        return f"FourlogistClient(base_url={self._base!r}, token=***)"

    # ---------- ядро ----------

    def _headers(self, access_token: str | None = None) -> dict[str, str]:
        if self._token_provider is not None:
            value = self._token_provider.authorization_header(access_token)
        else:
            token = access_token or self._token
            value = f"{self._auth_scheme} {token}".strip() if self._auth_scheme else token

        return {self._auth_header: value, "Accept": "application/json"}

    @staticmethod
    def _url(ep: Endpoint, base: str, path_params: Mapping[str, Any] | None) -> str:
        path = ep.path

        for k, v in (path_params or {}).items():
            sv = str(v).strip()
            if not sv:
                raise ValueError(f"пустой параметр пути {k}")

            path = path.replace("{" + k + "}", quote(sv, safe=""))

        if "{" in path:
            raise ValueError(f"не заполнены параметры пути: {ep.path}")

        return base + path

    async def call(
        self,
        key: str,
        *,
        path_params: Mapping[str, Any] | None = None,
        query: Mapping[str, Any] | None = None,
        form: Mapping[str, Any] | None = None,
    ) -> HttpResponse:
        ep = ENDPOINTS.get(key)
        if ep is None:
            raise CrmForbiddenOperation(f"неизвестный метод CRM: {key}")

        if ep.safety is Safety.BLOCKED:
            raise CrmForbiddenOperation(f"метод {key} изменяет состояние и заблокирован")

        if ep.safety is Safety.WRITE and not self._allow_writes:
            raise CrmForbiddenOperation(f"запись в CRM отключена (CRM_ALLOW_WRITES=0): {key}")

        url = self._url(ep, self._base, path_params)
        params = {k: str(v) for k, v in (query or {}).items() if v is not None}
        pairs = build_form(form)

        attempts = 1 + (self._retries if ep.safety is Safety.READ_SAFE else 0)
        write = ep.safety is Safety.WRITE
        last: Exception | None = None
        attempt = 0
        auth_retry_used = False

        while attempt < attempts:
            t0 = time.monotonic()
            access_token = await self._token_provider.access_token() if self._token_provider else self._token

            try:
                resp = await self._transport.request(
                    ep.method,
                    url,
                    params=params,
                    form=pairs,
                    headers=self._headers(access_token),
                    timeout=self._timeout,
                )
            except TransportTimeout:
                last = (
                    CrmWriteOutcomeUnknown(f"{key}: таймаут, исход записи неизвестен")
                    if write
                    else CrmTimeout(f"{key}: таймаут")
                )
            except TransportError as e:
                last = (
                    CrmWriteOutcomeUnknown(f"{key}: сбой связи, исход записи неизвестен")
                    if write
                    else CrmUnavailable(f"{key}: сбой связи ({e})")
                )
            else:
                log.info(
                    "crm %s %s -> %s (%.0f мс)",
                    ep.method,
                    ep.path,
                    resp.status,
                    (time.monotonic() - t0) * 1000,
                )

                st = resp.status

                if 200 <= st < 300:
                    return resp

                if st == 401 and self._token_provider and ep.safety is Safety.READ_SAFE and not auth_retry_used:
                    await self._token_provider.refresh_after_unauthorized(access_token)
                    auth_retry_used = True
                    continue  # не расходуем лимит сетевых повторов

                if st in (401, 403):
                    raise CrmAuthError(f"{key}: HTTP {st}")

                if st == 404:
                    raise CrmNotFound(key)

                if st >= 500:
                    last = (
                        CrmWriteOutcomeUnknown(f"{key}: HTTP {st}, исход записи неизвестен")
                        if write
                        else CrmUnavailable(f"{key}: HTTP {st}")
                    )

                    if not write and st not in RETRYABLE_STATUS:
                        raise last
                else:
                    raise CrmError(f"{key}: HTTP {st}")

            attempt += 1

            if write or attempt >= attempts:
                break

            await self._sleep(self._backoff * (2 ** (attempt - 1)))

        assert last is not None
        raise last

    async def call_json(self, key: str, **kw: Any) -> Any:
        resp = await self.call(key, **kw)

        try:
            return json.loads(resp.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise CrmBadResponse(f"{key}: ответ не JSON") from None

    # ---------- операции адаптера ----------

    async def find_order_by_number(self, number: str) -> OrderSummary | None:
        raw = " ".join(str(number or "").strip().split())
        if not raw:
            return None

        variants: list[Mapping[str, Any]] = [
            {"form_order_api_filter[orderCode]": raw},
        ]

        if raw.isdigit():
            variants.append({"form_order_api_filter[orderId]": raw})

        target_norm = re.sub(r"\s+", "", raw).casefold()
        target_digits = re.sub(r"\D", "", raw)
        last_exc: Exception | None = None

        for form in variants:
            try:
                payload = await self.call_json(
                    "orders_list",
                    query={"limit": 100},
                    form=form,
                )
                items = [mappers.map_order(i) for i in mappers.extract_items(payload)]
            except CrmAuthError:
                raise
            except CrmError as exc:
                last_exc = exc
                continue
            else:
                last_exc = None

            exact: list[OrderSummary] = []

            for o in items:
                order_number_norm = re.sub(r"\s+", "", o.number or "").casefold()
                order_id_norm = re.sub(r"\s+", "", o.order_id or "").casefold()

                if order_number_norm == target_norm or order_id_norm == target_norm:
                    exact.append(o)

            if exact:
                return exact[0]

            if target_digits:
                digit_exact: list[OrderSummary] = []

                for o in items:
                    order_number_digits = re.sub(r"\D", "", o.number or "")
                    order_id_digits = re.sub(r"\D", "", o.order_id or "")

                    if order_number_digits == target_digits or order_id_digits == target_digits:
                        digit_exact.append(o)

                if len(digit_exact) == 1:
                    return digit_exact[0]

            # Если фильтр вернул один заказ, считаем, что это он.
            if len(items) == 1:
                return items[0]

        if last_exc is not None:
            raise last_exc

        return None

    async def get_order(self, order_id: str) -> OrderDetails:
        payload = await self.call_json("order_all_info", path_params={"orderId": order_id})

        if not isinstance(payload, Mapping):
            raise CrmBadResponse("all-info: ожидался объект")

        body = payload

        for key in ("order", "data", "result", "item"):
            if isinstance(payload.get(key), Mapping):
                body = payload[key]
                break

        summary = mappers.map_order(body)

        if str(summary.order_id).strip().casefold() != str(order_id).strip().casefold():
            log.warning(
                "all-info вернул другой id заказа: requested=%s returned=%s",
                order_id,
                summary.order_id,
            )

        client = body.get("client") if isinstance(body.get("client"), Mapping) else None

        name = None
        if isinstance(client, Mapping):
            name = mappers._nested_text(
                mappers._pick(
                    client,
                    "companyName",
                    "company_name",
                    "name",
                    "title",
                )
            )

        if not name:
            name = mappers._nested_text(
                mappers._pick(
                    body,
                    "clientName",
                    "client_name",
                    "companyName",
                    "company_name",
                )
            )

        return OrderDetails(summary, name, body)

    async def get_client(self, client_id: str) -> ClientInfo | None:
        cid = str(client_id or "").strip()
        if not cid:
            return None

        variants: list[Mapping[str, Any]] = [
            {"form[clientId]": cid},
            {"form[clientId][]": cid},
            {"form[clientId][0]": cid},
            {"form[clientId]": [cid]},
        ]

        last_exc: Exception | None = None

        for form in variants:
            try:
                payload = await self.call_json(
                    "clients_list",
                    query={"limit": 10},
                    form=form,
                )
                items = [mappers.map_client(i) for i in mappers.extract_items(payload)]
            except CrmAuthError:
                raise
            except CrmError as exc:
                last_exc = exc
                continue
            else:
                last_exc = None

            found = [c for c in items if c.client_id == cid]
            if found:
                return found[0]

        if isinstance(last_exc, CrmAuthError):
            raise last_exc

        # Запасной поиск без фильтра: первые 5 страниц по 100 клиентов.
        try:
            for page in range(1, 6):
                payload = await self.call_json(
                    "clients_list",
                    query={"limit": 100, "page": page},
                )
                items = [mappers.map_client(i) for i in mappers.extract_items(payload)]

                found = [c for c in items if c.client_id == cid]
                if found:
                    return found[0]

                if len(items) < 100:
                    break
        except CrmAuthError:
            raise
        except CrmError as exc:
            if last_exc is None:
                last_exc = exc

        if last_exc is not None:
            raise last_exc

        return None

    async def list_order_documents(self, order_id: str) -> list[DocumentInfo]:
        payload = await self.call_json(
            "order_documents_list",
            query={"limit": 100},
            form={"form_document_api_filter[order]": order_id},
        )

        return [mappers.map_document(i) for i in mappers.extract_items(payload)]

    async def download_document(self, info: DocumentInfo) -> DocumentContent:
        resp = await self.call(
            "order_document_get",
            path_params={"document": info.document_id},
        )

        return DocumentContent(
            info,
            resp.body,
            resp.headers.get("content-type") or resp.headers.get("Content-Type"),
        )

    async def list_users(self, phone: str | None = None, email: str | None = None) -> list[CrmUser]:
        payload = await self.call_json(
            "users_list",
            query={"limit": 100},
            form={
                "form_user_api_filter[phone]": phone,
                "form_user_api_filter[email]": email,
            },
        )

        return [mappers.map_user(i) for i in mappers.extract_items(payload)]

    async def probe(self, key: str) -> Any:
        if key not in PROBE_KEYS:
            raise CrmForbiddenOperation("probe разрешён только для списков чтения")

        return await self.call_json(key, query={"limit": 1})

    async def ping(self) -> bool:
        try:
            await self.call("users_info")
            return True
        except CrmError:
            return False

    async def aclose(self) -> None:
        close = getattr(self._transport, "aclose", None)
        if close:
            await close()

        if self._token_provider is not None:
            await self._token_provider.aclose()
