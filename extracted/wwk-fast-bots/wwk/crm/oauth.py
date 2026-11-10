"""OAuth2 password/refresh flow for 4logist.

Secrets and tokens live only in process memory and are never logged. On process
restart a fresh password grant is used; while running, refresh_token is used.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from .errors import CrmAuthError, CrmUnavailable

log = logging.getLogger("wwk.crm.oauth")


@dataclass(frozen=True)
class OAuthHttpResponse:
    status: int
    body: bytes


class OAuthTransport(Protocol):
    async def post_json(self, url: str, payload: Mapping[str, str], timeout: float) -> OAuthHttpResponse: ...
    async def aclose(self) -> None: ...


class HttpxOAuthTransport:
    def __init__(self) -> None:
        import httpx
        self._httpx = httpx
        self._client = httpx.AsyncClient(follow_redirects=False)

    async def post_json(self, url: str, payload: Mapping[str, str], timeout: float) -> OAuthHttpResponse:
        try:
            response = await self._client.post(url, json=dict(payload), timeout=timeout,
                                               headers={"Content-Type": "application/json", "Accept": "application/json"})
        except self._httpx.TimeoutException:
            raise CrmUnavailable("4logist OAuth: превышено время ожидания") from None
        except self._httpx.HTTPError as exc:
            raise CrmUnavailable(f"4logist OAuth: ошибка соединения ({type(exc).__name__})") from None
        return OAuthHttpResponse(response.status_code, response.content)

    async def aclose(self) -> None:
        await self._client.aclose()


class OAuthTokenProvider:
    """Получает access_token по password grant и обновляет его по refresh_token."""

    def __init__(self, base_url: str, client_id: str, client_secret: str, username: str, password: str,
                 *, transport: OAuthTransport | None = None, timeout: float = 15.0,
                 safety_margin_seconds: int = 30) -> None:
        required = (base_url, client_id, client_secret, username, password)
        if not all(str(v).strip() for v in required):
            raise ValueError("Для OAuth 4logist нужны base_url, client_id, client_secret, username и password")
        self._url = base_url.rstrip("/") + "/oauth/v2/token"
        self._client_id = client_id
        self._client_secret = client_secret
        self._username = username
        self._password = password
        self._transport = transport or HttpxOAuthTransport()
        self._timeout = timeout
        self._margin = max(0, safety_margin_seconds)
        self._lock = asyncio.Lock()
        self._access_token = ""
        self._refresh_token = ""
        self._token_type = "Bearer"
        self._expires_at = 0.0

    def __repr__(self) -> str:
        return "OAuthTokenProvider(token=***)"

    def _valid(self) -> bool:
        return bool(self._access_token and time.monotonic() < self._expires_at - self._margin)

    async def access_token(self) -> str:
        if self._valid():
            return self._access_token
        async with self._lock:
            if self._valid():
                return self._access_token
            await self._obtain(use_refresh=bool(self._refresh_token))
            return self._access_token

    async def refresh_after_unauthorized(self, used_token: str) -> str:
        """Refresh once after 401; concurrent requests reuse the newly issued token."""
        async with self._lock:
            if self._access_token and self._access_token != used_token and self._valid():
                return self._access_token
            await self._obtain(use_refresh=bool(self._refresh_token))
            return self._access_token

    async def _obtain(self, *, use_refresh: bool) -> None:
        if use_refresh:
            payload = {"grant_type": "refresh_token", "client_id": self._client_id,
                       "client_secret": self._client_secret, "refresh_token": self._refresh_token}
            response = await self._transport.post_json(self._url, payload, self._timeout)
            # If the refresh token was revoked/expired, fall back to the supplied
            # resource-owner credentials. Any other failure is surfaced.
            if response.status in (400, 401):
                log.warning("4logist OAuth refresh rejected; trying password grant")
                await self._obtain(use_refresh=False)
                return
        else:
            payload = {"grant_type": "password", "client_id": self._client_id,
                       "client_secret": self._client_secret, "username": self._username,
                       "password": self._password}
            response = await self._transport.post_json(self._url, payload, self._timeout)

        if not 200 <= response.status < 300:
            # Записываем только статус и тип grant. Тело ответа может содержать
            # чувствительные сведения, поэтому его никогда не логируем.
            grant_type = "refresh_token" if use_refresh else "password"
            log.warning("4logist OAuth token endpoint returned HTTP %s (grant_type=%s)",
                        response.status, grant_type)
            if response.status in (400, 401, 403):
                raise CrmAuthError(f"4logist OAuth: HTTP {response.status}; проверьте OAuth-реквизиты и доступ")
            raise CrmUnavailable(f"4logist OAuth: HTTP {response.status}")
        try:
            body: Any = json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise CrmAuthError("4logist OAuth: ответ не является корректным JSON") from None
        if not isinstance(body, dict):
            raise CrmAuthError("4logist OAuth: неожиданный формат ответа")
        access = body.get("access_token")
        refresh = body.get("refresh_token")
        token_type = body.get("token_type") or "Bearer"
        try:
            expires_in = int(body.get("expires_in"))
        except (TypeError, ValueError):
            expires_in = 0
        if not isinstance(access, str) or not access or not isinstance(refresh, str) or not refresh or expires_in <= 0:
            raise CrmAuthError("4logist OAuth: в ответе отсутствуют access_token / refresh_token / expires_in")
        if not isinstance(token_type, str) or not token_type.strip():
            token_type = "Bearer"
        self._access_token = access
        self._refresh_token = refresh
        self._token_type = token_type.strip()
        self._expires_at = time.monotonic() + expires_in
        log.info("4logist OAuth: токен получен/обновлён, срок действия %s сек.", expires_in)

    def authorization_header(self, access_token: str | None = None) -> str:
        token = access_token or self._access_token
        return f"{self._token_type} {token}"

    async def aclose(self) -> None:
        close = getattr(self._transport, "aclose", None)
        if close:
            await close()
