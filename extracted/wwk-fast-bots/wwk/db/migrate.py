from __future__ import annotations

import asyncio
import logging
import pathlib

log = logging.getLogger("wwk.db")
ROOT = pathlib.Path(__file__).resolve().parents[2]


def _upgrade_sync() -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    command.upgrade(cfg, "head")


async def run_migrations() -> None:
    """Alembic использует собственный event loop - запускаем в потоке."""
    log.info("Применяю миграции БД…")
    await asyncio.to_thread(_upgrade_sync)
    log.info("Миграции применены")
