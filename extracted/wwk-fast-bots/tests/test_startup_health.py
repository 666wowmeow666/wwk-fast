import asyncio
import json
import unittest

from wwk.app import build_core, memory_repos
from wwk.config import Settings
from wwk.core.models import Role
from wwk.crm.mock import MockFourlogistAdapter
from wwk.health import HealthServer


class EmptyStorageStartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_with_empty_storage_mock_mode(self):
        s = Settings.from_env({"TEAM_BOT_TOKEN": "x", "DATABASE_URL": "postgres://a/b", "ADMIN_TELEGRAM_IDS": "5,6"})
        self.assertEqual(s.validate(), [])
        repos = memory_repos()
        self.assertEqual(await repos.employees.list_all(), [])
        core = await build_core(s, repos)
        self.assertIsInstance(core.crm, MockFourlogistAdapter)
        self.assertTrue(core.crm.is_mock)
        admin = await core.employees.authenticate(5)
        self.assertIn(Role.ADMIN, admin.roles)
        self.assertEqual(len(await repos.employees.list_all()), 2)
        await build_core(s, repos)                       # повторный старт не дублирует
        self.assertEqual(len(await repos.employees.list_all()), 2)

    async def test_policy_from_settings(self):
        s = Settings.from_env({"CLIENT_VISIBLE_DOC_GROUPS": "cmr", "CLIENT_REQUIRE_MANAGER_APPROVAL": "1",
                               "CLIENT_MAX_FAILS_PER_TELEGRAM": "2"})
        core = await build_core(s, memory_repos())
        p = core.client_auth.policy
        self.assertEqual((p.max_fails_per_telegram, p.require_manager_approval, p.visible_doc_groups), (2, True, frozenset({"cmr"})))


class HealthTests(unittest.IsolatedAsyncioTestCase):
    async def _get(self, port, path):
        r, w = await asyncio.open_connection("127.0.0.1", port)
        w.write(f"GET {path} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
        await w.drain()
        data = await r.read()
        w.close()
        head, _, body = data.partition(b"\r\n\r\n")
        return head.split(b"\r\n")[0].decode(), body

    async def test_health_endpoint(self):
        h = HealthServer(0, lambda: {"status": "ok", "bots": {"team": "running"}}, host="127.0.0.1")
        await h.start()
        try:
            line, body = await self._get(h.port, "/healthz")
            self.assertIn("200", line)
            self.assertEqual(json.loads(body)["bots"]["team"], "running")
            line, _ = await self._get(h.port, "/secret")
            self.assertIn("404", line)
        finally:
            await h.stop()


if __name__ == "__main__":
    unittest.main()
