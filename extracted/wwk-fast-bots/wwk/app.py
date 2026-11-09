"""Сборка общего ядра (используется обоими ботами)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from .config import Settings
from .core.client_auth import ClientAuthPolicy, ClientAuthService
from .core.employees import EmployeeService
from .core.memory_repo import MemoryAttemptRepo, MemoryAuditRepo, MemoryBindingRepo, MemoryEmployeeRepo
from .crm.base import CrmAdapter
from .crm.client import FourlogistClient
from .crm.mock import MockFourlogistAdapter
from .crm.oauth import OAuthTokenProvider


@dataclass
class Repos:
    employees: object
    bindings: object
    attempts: object
    audit: object


def memory_repos() -> Repos:
    return Repos(MemoryEmployeeRepo(), MemoryBindingRepo(), MemoryAttemptRepo(), MemoryAuditRepo())


def sql_repos(session_factory) -> Repos:
    from .db.repos import SqlAttemptRepo, SqlAuditRepo, SqlBindingRepo, SqlEmployeeRepo
    return Repos(SqlEmployeeRepo(session_factory), SqlBindingRepo(session_factory),
                 SqlAttemptRepo(session_factory), SqlAuditRepo(session_factory))


@dataclass
class Core:
    crm: CrmAdapter
    employees: EmployeeService
    client_auth: ClientAuthService


def build_crm(s: Settings) -> CrmAdapter:
    if s.crm_mode == "real":
        oauth_values = (s.crm_client_id, s.crm_client_secret, s.crm_username, s.crm_password)
        provider = OAuthTokenProvider(s.crm_base_url, *oauth_values, timeout=s.crm_timeout_seconds) if all(oauth_values) else None
        return FourlogistClient(s.crm_base_url, s.crm_token or None, token_provider=provider,
                                auth_header=s.crm_auth_header, auth_scheme=s.crm_auth_scheme,
                                timeout=s.crm_timeout_seconds, max_read_retries=s.crm_max_read_retries,
                                allow_writes=s.crm_allow_writes)
    return MockFourlogistAdapter()


def build_policy(s: Settings) -> ClientAuthPolicy:
    return ClientAuthPolicy(
        max_fails_per_telegram=s.client_max_fails_per_telegram,
        max_fails_per_order=s.client_max_fails_per_order,
        lockout=timedelta(minutes=s.client_lockout_minutes),
        binding_ttl=timedelta(days=s.client_binding_ttl_days),
        require_manager_approval=s.client_require_manager_approval,
        visible_doc_groups=s.client_visible_doc_groups,
        max_document_bytes=s.client_max_document_mb * 1024 * 1024,
    )


async def build_core(s: Settings, repos: Repos) -> Core:
    crm = build_crm(s)
    employees = EmployeeService(repos.employees, repos.audit)
    await employees.bootstrap_admins(s.admin_telegram_ids)
    client_auth = ClientAuthService(crm, repos.bindings, repos.attempts, repos.audit, build_policy(s))
    return Core(crm, employees, client_auth)
