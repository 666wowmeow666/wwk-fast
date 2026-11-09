"""Проверка сборки роутеров aiogram (запускается, только если aiogram установлен - в CI)."""
import importlib.util
import unittest

HAS = importlib.util.find_spec("aiogram") is not None


@unittest.skipUnless(HAS, "aiogram не установлен")
class BotSmokeTests(unittest.IsolatedAsyncioTestCase):
    async def test_routers_build_and_attach(self):
        from aiogram import Dispatcher
        from wwk.app import build_core, memory_repos
        from wwk.bots.client_bot import build_client_router
        from wwk.bots.team_bot import build_team_router
        from wwk.config import Settings
        s = Settings.from_env({"ADMIN_TELEGRAM_IDS": "1"})
        core = await build_core(s, memory_repos())
        dp = Dispatcher()
        dp.include_router(build_client_router(core.client_auth, core.crm, s))
        dp.include_router(build_team_router(core.employees, core.client_auth, core.crm, s))
        self.assertEqual(len(dp.sub_routers), 2)


if __name__ == "__main__":
    unittest.main()
