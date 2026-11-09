from datetime import datetime, timedelta, timezone

from wwk.core.client_auth import ClientAuthPolicy, ClientAuthService
from wwk.core.employees import EmployeeService
from wwk.core.memory_repo import (MemoryAttemptRepo, MemoryAuditRepo, MemoryBindingRepo, MemoryEmployeeRepo)
from wwk.crm.mock import MockFourlogistAdapter


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, **kw):
        self.now += timedelta(**kw)


def make_services(policy=None, crm=None, clock=None):
    clock = clock or Clock()
    audit = MemoryAuditRepo()
    emp_repo = MemoryEmployeeRepo()
    bindings = MemoryBindingRepo()
    attempts = MemoryAttemptRepo()
    crm = crm or MockFourlogistAdapter()
    employees = EmployeeService(emp_repo, audit, clock)
    client_auth = ClientAuthService(crm, bindings, attempts, audit, policy or ClientAuthPolicy(), clock)
    return type("S", (), dict(clock=clock, audit=audit, emp_repo=emp_repo, bindings=bindings,
                              attempts=attempts, crm=crm, employees=employees, client_auth=client_auth))
