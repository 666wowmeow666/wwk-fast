from __future__ import annotations

from typing import Iterable

from .models import Permission as P, Role

ROLE_PERMISSIONS: dict[Role, frozenset[P]] = {
    Role.MANAGER: frozenset({P.VIEW_OWN_REQUESTS, P.SEARCH, P.CREATE_REQUEST, P.COMMENT,
                             P.APPROVE_CLIENT_BINDING, P.AI_TEXT}),
    Role.LOGISTICIAN: frozenset({P.VIEW_OWN_REQUESTS, P.SEARCH, P.COMMENT, P.MANAGE_RATES, P.AI_LOGIST}),
    Role.ADMIN: frozenset(P),   # администратор: всё
}


def permissions_for(roles: Iterable[Role]) -> frozenset[P]:
    """Роли совмещаются: права - объединение."""
    out: set[P] = set()
    for r in roles:
        out |= ROLE_PERMISSIONS[r]
    return frozenset(out)
