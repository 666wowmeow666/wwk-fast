"""Интеграционные тесты на реальном PostgreSQL. Запускаются только если задан TEST_DATABASE_URL
(в CI на GitHub он задан; локально без БД тесты пропускаются)."""
import os
import unittest

URL = os.environ.get("TEST_DATABASE_URL")


@unittest.skipUnless(URL, "нет TEST_DATABASE_URL")
class SqlIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from sqlalchemy import text
        from wwk.app import sql_repos
        from wwk.config import normalize_database_url
        from wwk.db.migrate import run_migrations
        from wwk.db.session import make_engine, make_session_factory
        os.environ["DATABASE_URL"] = URL
        await run_migrations()                       # alembic upgrade head на (пустой) базе
        await run_migrations()                       # повторный запуск безопасен
        self.engine = make_engine(normalize_database_url(URL))
        async with self.engine.begin() as c:
            await c.execute(text("TRUNCATE employee_roles, employees, client_bindings, auth_attempts, audit_log RESTART IDENTITY CASCADE"))
        self.repos = sql_repos(make_session_factory(self.engine))

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_employees_roles_and_last_admin(self):
        from wwk.core.employees import AccessDenied, EmployeeService
        from wwk.core.models import Role
        svc = EmployeeService(self.repos.employees, self.repos.audit)
        await svc.bootstrap_admins([100])
        admin = await svc.authenticate(100)
        m = await svc.register(admin, 200, "Иван", [Role.MANAGER, Role.LOGISTICIAN])
        self.assertEqual(m.roles, frozenset({Role.MANAGER, Role.LOGISTICIAN}))
        await svc.revoke_role(admin, 200, Role.LOGISTICIAN)
        self.assertEqual((await svc.authenticate(200)).roles, frozenset({Role.MANAGER}))
        with self.assertRaises(ValueError):
            await svc.block(admin, 100)
        await svc.block(admin, 200)
        with self.assertRaises(AccessDenied):
            await svc.authenticate(200)
        self.assertIn("staff.block", [e.action for e in await svc.recent_audit(admin, 20)])
        self.assertEqual(await self.repos.employees.get_by_telegram_id(5_000_000_000), None)   # BIGINT

    async def test_client_flow(self):
        from wwk.core.client_auth import ClientAuthPolicy, ClientAuthService, VerifyStatus
        from wwk.crm.mock import MockFourlogistAdapter
        r = self.repos
        svc = ClientAuthService(MockFourlogistAdapter(), r.bindings, r.attempts, r.audit,
                                ClientAuthPolicy(visible_doc_groups=frozenset({"cmr"})))
        tg = 7_000_000_001
        self.assertEqual((await svc.verify(tg, "WWK-0001", "9999")).status, VerifyStatus.INVALID)
        ok = await svc.verify(tg, "WWK-0001", "0001")
        self.assertEqual(ok.status, VerifyStatus.OK)
        await svc.verify(tg, "WWK-0002", "0002")
        self.assertEqual({b.org_key for b in await svc.list_valid_bindings(tg)}, {"1001", "1002"})
        self.assertEqual((await svc.assert_order_access(tg, "1001", "5001")).number, "WWK-0001")
        from wwk.core.employees import AccessDenied
        with self.assertRaises(AccessDenied):
            await svc.assert_order_access(tg, "1001", "5002")
        docs = await svc.list_client_documents(tg, "1001", "5001")
        self.assertEqual([d.document_id for d in docs], ["9001"])

    async def test_lockout_persisted(self):
        from wwk.core.client_auth import ClientAuthPolicy, ClientAuthService, VerifyStatus
        from wwk.crm.mock import MockFourlogistAdapter
        r = self.repos
        svc = ClientAuthService(MockFourlogistAdapter(), r.bindings, r.attempts, r.audit,
                                ClientAuthPolicy(max_fails_per_telegram=3))
        for _ in range(3):
            await svc.verify(42, "WWK-0001", "9999")
        self.assertEqual((await svc.verify(42, "WWK-0001", "0001")).status, VerifyStatus.LOCKED)


if __name__ == "__main__":
    unittest.main()
