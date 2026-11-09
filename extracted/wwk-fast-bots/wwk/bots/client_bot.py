"""WWK FAST CLIENT - клиентский бот. Этап A: авторизация (номер заказа + 4 цифры ИНН), выбор организации,
карточка заказа (то, что подтверждено), документы по политике видимости, информация о компании.
Общение с менеджером и загрузка документов - этап C."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
                           KeyboardButton, Message, ReplyKeyboardMarkup)

from wwk.config import Settings
from wwk.core.client_auth import ClientAuthService, DocumentTooLarge, VerifyStatus
from wwk.core.employees import AccessDenied
from wwk.crm.base import CrmAdapter
from wwk.crm.errors import CrmError

from .common import MOCK_BANNER, clip, esc, log

BTN_FIND, BTN_ABOUT, BTN_SWITCH = "📦 Найти заказ", "🏢 О компании", "🔄 Сменить организацию"
MENU = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=BTN_FIND)],
                                     [KeyboardButton(text=BTN_ABOUT), KeyboardButton(text=BTN_SWITCH)]],
                           resize_keyboard=True)


class Auth(StatesGroup):
    order = State()
    inn = State()


class Lookup(StatesGroup):
    number = State()


def build_client_router(auth: ClientAuthService, crm: CrmAdapter, settings: Settings) -> Router:
    router = Router(name="client")
    router.message.filter(F.chat.type == "private")

    def banner() -> str:
        return MOCK_BANNER if crm.is_mock else ""

    async def ask_order(message: Message, state: FSMContext) -> None:
        await state.set_state(Auth.order)
        await message.answer(banner() + "Для доступа введите <b>номер вашего заказа</b>.")

    async def pick_org(message: Message, state: FSMContext, tg_id: int) -> str | None:
        """Возвращает org_key текущей организации или None (если пошёл диалог выбора/авторизации)."""
        bindings = await auth.list_valid_bindings(tg_id)
        keys = {b.org_key for b in bindings}
        current = (await state.get_data()).get("org_key")
        if current in keys:
            return current
        await state.update_data(org_key=None)
        if not bindings:
            await ask_order(message, state)
            return None
        if len(bindings) == 1:
            await state.update_data(org_key=bindings[0].org_key)
            return bindings[0].org_key
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=(b.org_name or b.org_key)[:50],
                                                                          callback_data=f"org:{b.org_key}")] for b in bindings])
        await message.answer("Выберите организацию:", reply_markup=kb)
        return None

    @router.message(Command("start"))
    async def start(message: Message, state: FSMContext):
        await state.clear()
        org = await pick_org(message, state, message.from_user.id)
        if org:
            await message.answer(banner() + "Здравствуйте! Выберите действие.", reply_markup=MENU)

    @router.callback_query(F.data.startswith("org:"))
    async def choose_org(cb: CallbackQuery, state: FSMContext):
        org_key = cb.data[4:]
        if await auth.get_valid_binding(cb.from_user.id, org_key) is None:   # не доверяем callback
            await cb.answer("Нет доступа", show_alert=True)
            return
        await state.update_data(org_key=org_key)
        await cb.message.answer("Организация выбрана.", reply_markup=MENU)
        await cb.answer()

    @router.message(F.text == BTN_SWITCH)
    async def switch(message: Message, state: FSMContext):
        await state.update_data(org_key=None)
        bindings = await auth.list_valid_bindings(message.from_user.id)
        if len(bindings) > 1:
            await pick_org(message, state, message.from_user.id)
        else:
            await ask_order(message, state)

    @router.message(F.text == BTN_ABOUT)
    async def about(message: Message):
        parts = [f"<b>{esc(settings.company_name)}</b>"]
        if settings.company_phone:
            parts.append(f"Телефон: {esc(settings.company_phone)}")
        if settings.company_requisites:
            parts.append(esc(settings.company_requisites))
        await message.answer("\n".join(parts))

    @router.message(Auth.order)
    async def got_order(message: Message, state: FSMContext):
        await state.update_data(order_number=(message.text or "").strip()[:60])
        await state.set_state(Auth.inn)
        await message.answer("Введите <b>последние 4 цифры ИНН</b> вашей организации.")

    @router.message(Auth.inn)
    async def got_inn(message: Message, state: FSMContext):
        inn4 = (message.text or "").strip()
        try:
            await message.delete()                     # не оставляем ИНН в переписке
        except Exception:  # noqa: BLE001
            pass
        number = (await state.get_data()).get("order_number", "")
        res = await auth.verify(message.from_user.id, number, inn4)
        st = res.status
        if st in (VerifyStatus.OK, VerifyStatus.PENDING_APPROVAL) and res.binding:
            if st is VerifyStatus.OK:
                await state.clear()
                await state.update_data(org_key=res.binding.org_key)
                await message.answer(f"✅ Доступ подтверждён: {esc(res.binding.org_name)}", reply_markup=MENU)
            else:
                await state.clear()
                await message.answer("⏳ Заявка принята. Доступ откроется после подтверждения менеджером.")
        elif st is VerifyStatus.INVALID:
            await state.set_state(Auth.order)
            await message.answer("Не удалось подтвердить данные. Проверьте номер заказа и введите его снова.")
        elif st is VerifyStatus.BAD_INPUT:
            await state.set_state(Auth.order)
            await message.answer("Неверный формат. Введите номер заказа ещё раз, затем 4 цифры ИНН.")
        elif st is VerifyStatus.LOCKED:
            await state.clear()
            await message.answer(f"Слишком много попыток. Повторите через {res.retry_after_minutes} мин. или свяжитесь с менеджером.")
        elif st is VerifyStatus.REVOKED:
            await state.clear()
            await message.answer("Доступ ограничен. Пожалуйста, свяжитесь с менеджером WWK FAST.")
        else:
            await state.clear()
            await message.answer("Сервис временно недоступен. Попробуйте позже.")

    @router.message(F.text == BTN_FIND)
    async def find(message: Message, state: FSMContext):
        if await pick_org(message, state, message.from_user.id):
            await state.set_state(Lookup.number)
            await message.answer("Введите номер заказа:")

    @router.message(Lookup.number)
    async def lookup(message: Message, state: FSMContext):
        org = await pick_org(message, state, message.from_user.id)
        if not org:
            return
        try:
            order = await auth.find_order_for_client(message.from_user.id, org, message.text or "")
        except AccessDenied:
            await message.answer("Нет доступа. Нажмите /start.")
            return
        except CrmError as e:
            log.warning("Ошибка CRM при поиске заказа: %s", type(e).__name__)
            await message.answer("Сервис временно недоступен. Попробуйте позже.")
            return
        await state.set_state(None)
        if order is None:
            await message.answer("Заказ не найден среди заказов вашей организации.")
            return
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📄 Документы", callback_data=f"docs:{order.order_id}")]])
        await message.answer(banner() + f"📦 Заказ <b>{esc(order.number)}</b>\nСтатус: {esc(order.status) or 'нет данных'}\n"
                             "Подробности по грузу и местоположению уточняйте у менеджера.", reply_markup=kb)

    @router.callback_query(F.data.startswith("docs:"))
    async def docs(cb: CallbackQuery, state: FSMContext):
        org = (await state.get_data()).get("org_key")
        try:
            items = await auth.list_client_documents(cb.from_user.id, org or "", cb.data[5:])
        except AccessDenied:
            await cb.answer("Нет доступа", show_alert=True)
            return
        except CrmError:
            await cb.answer("Сервис временно недоступен", show_alert=True)
            return
        if not items:
            await cb.message.answer("Документы для этого заказа пока недоступны.")
        else:
            kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
                text=d.title[:50], callback_data=f"doc:{cb.data[5:]}:{d.document_id}")] for d in items])
            await cb.message.answer("Документы:", reply_markup=kb)
        await cb.answer()

    @router.callback_query(F.data.startswith("doc:"))
    async def doc(cb: CallbackQuery, state: FSMContext):
        org = (await state.get_data()).get("org_key")
        try:
            _, order_id, document_id = cb.data.split(":", 2)
            content = await auth.get_client_document(cb.from_user.id, org or "", order_id, document_id)
        except (AccessDenied, ValueError):
            await cb.answer("Нет доступа", show_alert=True)
            return
        except DocumentTooLarge:
            await cb.answer("Файл слишком большой", show_alert=True)
            return
        except CrmError:
            await cb.answer("Сервис временно недоступен", show_alert=True)
            return
        await cb.message.answer_document(BufferedInputFile(content.content, filename=content.info.title[:80]))
        await cb.answer()

    @router.message()
    async def fallback(message: Message, state: FSMContext):
        if await state.get_state() is None:
            await message.answer("Нажмите /start, чтобы начать.")

    return router
