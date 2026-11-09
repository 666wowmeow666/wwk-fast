import unittest

from tests.helpers import make_services
from wwk.core.employees import AccessDenied
from wwk.core.models import Permission as P, Role
from wwk.core.roles import permissions_for


class RolesTests(unittest.TestCase):
    def test_role_separation(self):
        m, l, a = (permissions_for({r}) for r in (Role.MANAGER, Role.LOGISTICIAN, Role.ADMIN))
        self.assertIn(P.CREATE_REQUEST, m)
        self.assertNotIn(P.CREATE_REQUEST, l)
        self.assertIn(P.MANAGE_RATES, l)
        self.assertNotIn(P.MANAGE_RATES, m)
        for p in (P.MANAGE_STAFF, P.VIEW_AUDIT, P.CRM_DIAGNOSTICS):
            self.assertNotIn(p, m)
            self.assertNotIn(p, l)
        self.assertEqual(a, frozenset(P))

    def test_roles_combine(self):
        both = permissions_for({Role.MANAGER, Role.LOGISTICIAN})
        self.assertTrue({P.CREATE_REQUEST, P.MANAGE_RATES} <= both)
        self.assertNotIn(P.MANAGE_STAFF, both)


class EmployeeServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.s = make_services()
        await self.s.employees.bootstrap_admins([100])
        self.admin = await self.s.employees.authenticate(100)

    async def test_bootstrap_idempotent(self):
        await self.s.employees.bootstrap_admins([100, 101])
        await self.s.employees.bootstrap_admins([100, 101])
        self.assertEqual(len(await self.s.emp_repo.list_all()), 2)

    async def test_unknown_blocked_and_roleless_are_denied(self):
        with self.assertRaises(AccessDenied):
            await self.s.employees.authenticate(999)
        await self.s.employees.register(self.admin, 200, "Иван", [Role.MANAGER])
        await self.s.employees.block(self.admin, 200)
        with self.assertRaises(AccessDenied):
            await self.s.employees.authenticate(200)
        await self.s.employees.register(self.admin, 201, "Без роли", [Role.MANAGER])
        await self.s.employees.revoke_role(self.admin, 201, Role.MANAGER)
        with self.assertRaises(AccessDenied):
            await self.s.employees.authenticate(201)

    async def test_non_admin_cannot_manage_staff(self):
        mgr = await self.s.employees.register(self.admin, 200, "Иван", [Role.MANAGER])
        for call in (lambda: self.s.employees.register(mgr, 300, "X", [Role.ADMIN]),
                     lambda: self.s.employees.grant_role(mgr, 200, Role.ADMIN),
                     lambda: self.s.employees.block(mgr, 100),
                     lambda: self.s.employees.set_crm_user(mgr, 200, "5"),
                     lambda: self.s.employees.list_staff(mgr),
                     lambda: self.s.employees.recent_audit(mgr)):
            with self.assertRaises(AccessDenied):
                await call()
        self.assertEqual(len(await self.s.emp_repo.list_all()), 2)

    async def test_blocked_admin_loses_permissions_immediately(self):
        a2 = await self.s.employees.register(self.admin, 101, "Админ 2", [Role.ADMIN])
        await self.s.employees.block(self.admin, 101)
        stale = await self.s.emp_repo.get_by_telegram_id(101)
        self.assertFalse(self.s.employees.has(stale, P.MANAGE_STAFF))
        self.assertTrue(a2.id)

    async def test_last_admin_protected(self):
        with self.assertRaises(ValueError):
            await self.s.employees.block(self.admin, 100)
        with self.assertRaises(ValueError):
            await self.s.employees.revoke_role(self.admin, 100, Role.ADMIN)
        await self.s.employees.register(self.admin, 101, "Админ 2", [Role.ADMIN])
        await self.s.employees.revoke_role(self.admin, 100, Role.ADMIN)   # теперь можно

    async def test_crm_binding_and_audit(self):
        await self.s.employees.register(self.admin, 200, "Иван", [Role.MANAGER])
        await self.s.employees.set_crm_user(self.admin, 200, " 77 ")
        self.assertEqual((await self.s.emp_repo.get_by_telegram_id(200)).crm_user_id, "77")
        actions = [e.action for e in await self.s.employees.recent_audit(self.admin, 10)]
        self.assertIn("staff.bind_crm_user", actions)
        self.assertIn("staff.register", actions)

    async def test_duplicate_and_empty_roles_rejected(self):
        with self.assertRaises(ValueError):
            await self.s.employees.register(self.admin, 100, "Дубль", [Role.MANAGER])
        with self.assertRaises(ValueError):
            await self.s.employees.register(self.admin, 300, "Без ролей", [])


if __name__ == "__main__":
    unittest.main()
