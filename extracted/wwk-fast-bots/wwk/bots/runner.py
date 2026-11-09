"""Изолированный запуск ботов: сбой одного не останавливает другой; перезапуск с задержкой."""
from __future__ import annotations

import asyncio
import logging
from typing import Callable

log = logging.getLogger("wwk.runner")


async def run_bot(name: str, token: str, router_factory: Callable, stop: asyncio.Event, status: dict) -> None:
    from aiogram import Bot, Dispatcher
    from aiogram.client.default import DefaultBotProperties
    from aiogram.enums import ParseMode
    from aiogram.exceptions import TelegramUnauthorizedError
    from aiogram.fsm.storage.memory import MemoryStorage

    backoff = 5
    while not stop.is_set():
        bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        dp = Dispatcher(storage=MemoryStorage())
        dp.include_router(router_factory())

        @dp.errors()
        async def on_error(event):  # noqa: ANN001
            log.error("Необработанная ошибка в боте %s: %s", name, type(event.exception).__name__,
                      exc_info=event.exception)
            return True

        poll = asyncio.create_task(dp.start_polling(bot, handle_signals=False, close_bot_session=False))
        waiter = asyncio.create_task(stop.wait())
        try:
            status[name] = "running"
            log.info("Бот %s запущен", name)
            await asyncio.wait({poll, waiter}, return_when=asyncio.FIRST_COMPLETED)
            if stop.is_set():
                try:
                    await dp.stop_polling()
                except Exception:  # noqa: BLE001
                    pass
                await asyncio.wait({poll}, timeout=10)
                status[name] = "stopped"
                return
            poll.result()                       # если polling упал - исключение уйдёт ниже
            log.warning("Бот %s завершил polling без ошибки, перезапуск", name)
        except TelegramUnauthorizedError:
            log.critical("Бот %s: Telegram отверг токен. Проверьте токен в переменных окружения. Бот остановлен.", name)
            status[name] = "bad_token"
            return
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.error("Бот %s упал (%s), перезапуск через %s c", name, type(e).__name__, backoff, exc_info=True)
            status[name] = "restarting"
        finally:
            waiter.cancel()
            if not poll.done():
                poll.cancel()
            await bot.session.close()
        try:
            await asyncio.wait_for(stop.wait(), timeout=backoff)
        except asyncio.TimeoutError:
            pass
        backoff = min(backoff * 2, 300)
