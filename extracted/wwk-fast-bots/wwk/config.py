"""Конфигурация из переменных окружения. Только стандартная библиотека."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

_TRUE = {"1", "true", "yes", "on", "y"}


def _bool(v: str | None, default: bool = False) -> bool:
    if v is None or v.strip() == "":
        return default
    return v.strip().lower() in _TRUE


def _int(v: str | None, default: int) -> int:
    if v is None or v.strip() == "":
        return default
    return int(v.strip())


def _id_set(v: str | None) -> frozenset[int]:
    if not v:
        return frozenset()
    return frozenset(int(p) for p in v.replace(";", ",").split(",") if p.strip())


def _str_set(v: str | None) -> frozenset[str]:
    if not v:
        return frozenset()
    return frozenset(p.strip() for p in v.split(",") if p.strip())


def normalize_database_url(url: str) -> str:
    """postgres:// и postgresql:// -> postgresql+asyncpg://, sslmode -> ssl (asyncpg)."""
    url = url.strip()
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+asyncpg://" + url[len("postgresql://"):]
    parts = urlsplit(url)
    if parts.query:
        q = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)]
        q = [("ssl", v) if k == "sslmode" else (k, v) for k, v in q]
        url = urlunsplit(parts._replace(query=urlencode(q)))
    return url


@dataclass(frozen=True)
class Settings:
    client_bot_token: str = field(default="", repr=False)
    team_bot_token: str = field(default="", repr=False)
    admin_telegram_ids: frozenset[int] = frozenset()
    database_url: str = field(default="", repr=False)
    run_migrations: bool = True
    crm_mode: str = "mock"
    crm_base_url: str = "https://wwkfast.4logist.com"
    crm_token: str = field(default="", repr=False)  # legacy: вручную выданный access token
    crm_client_id: str = field(default="", repr=False)
    crm_client_secret: str = field(default="", repr=False)
    crm_username: str = field(default="", repr=False)
    crm_password: str = field(default="", repr=False)
    crm_auth_header: str = "Authorization"
    crm_auth_scheme: str = "Bearer"
    crm_timeout_seconds: float = 15.0
    crm_max_read_retries: int = 2
    crm_allow_writes: bool = False
    client_max_fails_per_telegram: int = 5
    client_max_fails_per_order: int = 10
    client_lockout_minutes: int = 30
    client_binding_ttl_days: int = 180
    client_require_manager_approval: bool = False
    client_visible_doc_groups: frozenset[str] = frozenset()
    client_max_document_mb: int = 20
    company_name: str = "WWK FAST"
    company_phone: str = ""
    company_requisites: str = ""
    port: int = 8080
    log_level: str = "INFO"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        e = os.environ if env is None else env
        g = e.get
        db = g("DATABASE_URL", "")
        return cls(
            client_bot_token=g("CLIENT_BOT_TOKEN", "").strip(),
            team_bot_token=g("TEAM_BOT_TOKEN", "").strip(),
            admin_telegram_ids=_id_set(g("ADMIN_TELEGRAM_IDS")),
            database_url=normalize_database_url(db) if db.strip() else "",
            run_migrations=_bool(g("RUN_MIGRATIONS"), True),
            crm_mode=g("CRM_MODE", "mock").strip().lower() or "mock",
            crm_base_url=(g("CRM_BASE_URL") or "https://wwkfast.4logist.com").strip().rstrip("/"),
            crm_token=g("CRM_TOKEN", "").strip(),
            crm_client_id=g("CRM_CLIENT_ID", "").strip(),
            crm_client_secret=g("CRM_CLIENT_SECRET", "").strip(),
            crm_username=g("CRM_USERNAME", "").strip(),
            crm_password=g("CRM_PASSWORD", "").strip(),
            crm_auth_header=(g("CRM_AUTH_HEADER") or "Authorization").strip(),
            crm_auth_scheme=g("CRM_AUTH_SCHEME", "Bearer").strip() if g("CRM_AUTH_SCHEME") is not None else "Bearer",
            crm_timeout_seconds=float(g("CRM_TIMEOUT_SECONDS") or 15),
            crm_max_read_retries=_int(g("CRM_MAX_READ_RETRIES"), 2),
            crm_allow_writes=_bool(g("CRM_ALLOW_WRITES"), False),
            client_max_fails_per_telegram=_int(g("CLIENT_MAX_FAILS_PER_TELEGRAM"), 5),
            client_max_fails_per_order=_int(g("CLIENT_MAX_FAILS_PER_ORDER"), 10),
            client_lockout_minutes=_int(g("CLIENT_LOCKOUT_MINUTES"), 30),
            client_binding_ttl_days=_int(g("CLIENT_BINDING_TTL_DAYS"), 180),
            client_require_manager_approval=_bool(g("CLIENT_REQUIRE_MANAGER_APPROVAL"), False),
            client_visible_doc_groups=_str_set(g("CLIENT_VISIBLE_DOC_GROUPS")),
            client_max_document_mb=_int(g("CLIENT_MAX_DOCUMENT_MB"), 20),
            company_name=g("COMPANY_NAME", "WWK FAST").strip() or "WWK FAST",
            company_phone=g("COMPANY_PHONE", "").strip(),
            company_requisites=g("COMPANY_REQUISITES", "").strip(),
            port=_int(g("PORT"), 8080),
            log_level=(g("LOG_LEVEL") or "INFO").strip().upper(),
        )

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not (self.client_bot_token or self.team_bot_token):
            errors.append("Не задан ни CLIENT_BOT_TOKEN, ни TEAM_BOT_TOKEN")
        if not self.database_url:
            errors.append("Не задан DATABASE_URL")
        if self.crm_mode not in {"mock", "real"}:
            errors.append("CRM_MODE должен быть mock или real")
        if self.crm_mode == "real":
            oauth_values = (self.crm_client_id, self.crm_client_secret, self.crm_username, self.crm_password)
            if not (all(oauth_values) or self.crm_token):
                errors.append("Для CRM_MODE=real нужны CRM_CLIENT_ID, CRM_CLIENT_SECRET, CRM_USERNAME, CRM_PASSWORD (либо legacy CRM_TOKEN)")
            if not self.crm_base_url:
                errors.append("Для CRM_MODE=real нужен CRM_BASE_URL")
        if self.team_bot_token and not self.admin_telegram_ids:
            errors.append("Не задан ADMIN_TELEGRAM_IDS: некому будет добавлять сотрудников")
        return errors

    def secrets(self) -> list[str]:
        values = [self.client_bot_token, self.team_bot_token, self.crm_token,
                  self.crm_client_secret, self.crm_password]
        if self.database_url:
            try:
                encoded_password = urlsplit(self.database_url).password
                if encoded_password:
                    values.extend((encoded_password, unquote(encoded_password)))
            except ValueError:
                pass
        return [s for s in values if s]
