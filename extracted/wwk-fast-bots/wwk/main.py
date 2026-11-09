"""Точка входа: python -m wwk.main"""
from __future__ import annotations

import asyncio
import logging
import signal
import sys

from .app import build_core, sql_repos
from .config import Settings
from .health import HealthServer
from .logging_setup import setup_logging

log = logging.getLogger("wwk.main")


async def amain() -> int:
    settings = Settings.from_env()
    setup_logging(settings.log_level, settings.secrets())
    errors = settings.validate()
    if errors:
        for e in errors:
            log.critical("Конфигурация: %s", e)
        return 2

    if settings.run_migrations:
        from .db.migrate import run_migrations
        try:
            await run_migrations()
        except Exception:  # noqa: BLE001
            log.critical("Миграции не применены - запуск остановлен", exc_info=True)
            return 3

    from .db.session import make_engine, make_session_factory
    engine = make_engine(settings.database_url)
    core = await build_core(settings, sql_repos(make_session_factory(engine)))
    if core.crm.is_mock:
        log.warning("CRM_MODE=mock: используются ДЕМОНСТРАЦИОННЫЕ данные, не данные 4logist")

    stop = asyncio.Event()
    status: dict[str, str] = {}
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    health = HealthServer(settings.port, lambda: {"status": "ok", "crm_mode": settings.crm_mode, "bots": dict(status)})
    await health.start()

    from .bots.client_bot import build_client_router
    from .bots.runner import run_bot
    from .bots.team_bot import build_team_router

    tasks = []
    if settings.client_bot_token:
        tasks.append(asyncio.create_task(run_bot(
            "client", settings.client_bot_token,
            lambda: build_client_router(core.client_auth, core.crm, settings), stop, status)))
    else:
        log.warning("CLIENT_BOT_TOKEN не задан: клиентский бот не запущен")
    if settings.team_bot_token:
        tasks.append(asyncio.create_task(run_bot(
            "team", settings.team_bot_token,
            lambda: build_team_router(core.employees, core.client_auth, core.crm, settings), stop, status)))
    else:
        log.warning("TEAM_BOT_TOKEN не задан: внутренний бот не запущен")

    await stop.wait()
    log.info("Останавливаюсь…")
    await asyncio.gather(*tasks, return_exceptions=True)
    await health.stop()
    closer = getattr(core.crm, "aclose", None)
    if closer:
        await closer()
    await engine.dispose()
    return 0


def main() -> None:
    sys.exit(asyncio.run(amain()))


if __name__ == "__main__":
    main()
