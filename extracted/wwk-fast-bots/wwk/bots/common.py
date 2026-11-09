from __future__ import annotations

import html
import logging
from contextlib import asynccontextmanager

from wwk.core.employees import AccessDenied
from wwk.crm.errors import CrmError

log = logging.getLogger("wwk.bots")
MOCK_BANNER = "⚠️ <b>Тестовый режим:</b> данные демонстрационные, это не данные CRM.\n\n"
MAX_TEXT = 3900


def esc(v) -> str:
    return html.escape("" if v is None else str(v))


def clip(text: str) -> str:
    return text if len(text) <= MAX_TEXT else text[:MAX_TEXT] + "\n…(обрезано)"


@asynccontextmanager
async def guard(message):
    """Единая обработка ожидаемых ошибок: пользователь видит понятный текст, детали - в лог."""
    try:
        yield
    except AccessDenied as e:
        await message.answer(f"⛔ {esc(e)}")
    except ValueError as e:
        await message.answer(f"⚠️ {esc(e)}")
    except CrmError as e:
        log.warning("Ошибка CRM: %s", type(e).__name__)
        await message.answer("⚠️ CRM 4logist сейчас недоступна или ответила неожиданно. Попробуйте позже.")
