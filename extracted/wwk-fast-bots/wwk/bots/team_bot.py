"""WWK FAST TEAM - внутренний бот. Этап A: доступ по Telegram ID, роли, управление сотрудниками,
подтверждение клиентов, диагностика CRM. Разделы заявок/ставок/задач/AI - этапы B-F."""
from __future__ import annotations

import json

from aiogram import BaseMiddleware, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

from wwk.config import Settings
from wwk.core.client_auth import ClientAuthService
from wwk.core.employees import AccessDenied, EmployeeService
from wwk.core.models import Employee, Permission as P, Role
from wwk.crm.base import CrmAdapter
from wwk.crm.endpoints import PROBE_KEYS
from wwk.crm.errors import CrmError
from wwk.crm.mappers import describe_shape

from .common import MOCK_BANNER, clip, esc, guard


def _tg_id(arg: str) -> int:
    try:
        return int(arg)
    except (TypeError, ValueError):
        raise ValueError("Telegram ID должен быть числом") from None


def _role(arg: str) -> Role:
    try:
        return Role(arg.strip().upper())
    except ValueError:
        raise ValueError("Роль: ADMIN, MANAGER или LOGISTICIAN") from None


def _args(command: CommandObject, n: int, usage: str) -> list[str]:
    parts = (command.args or "").split(maxsplit=n - 1)
    if len(parts) < n:
        raise ValueError(f"Формат: {usage}")
    return parts


class EmployeeMiddleware(BaseMiddleware):
    """Пускает только зарегистрированных, не заблокированных сотрудников. Остальным - только их Telegram ID."""

    def __init__(self, employees: EmployeeService) -> None:
        self._employees = employees

    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if user is None:
            return None
        try:
            data["employee"] = await self._employees.authenticate(user.id)
        except AccessDenied as e:
            if isinstance(event, Message) and event.chat.type == "private":
                await event.answer(f"⛔ {esc(e)}\nВаш Telegram ID: <code>{user.id}</code> — передайте его администратору.")
            elif isinstance(event, CallbackQuery):
                await event.answer("Нет доступа", show_alert=True)
            return None
        return await handler(event, data)


def build_team_router(employees: EmployeeService, client_auth: ClientAuthService, crm: CrmAdapter,
                      settings: Settings) -> Router:
    router = Router(name="team")
    mw = EmployeeMiddleware(employees)
    router.message.outer_middleware(mw)
    router.callback_query.outer_middleware(mw)

    def banner() -> str:
        return MOCK_BANNER if crm.is_mock else ""

    @router.message(Command("start", "menu", "help"))
    async def start(message: Message, employee: Employee):
        roles = ", ".join(sorted(r.value for r in employee.roles))
        lines = [banner() + f"Здравствуйте, <b>{esc(employee.full_name)}</b>!", f"Роли: {esc(roles)}", "",
                 "<b>Доступно сейчас:</b>", "/me — мой профиль"]
        if employees.has(employee, P.APPROVE_CLIENT_BINDING):
            lines += ["/pending — заявки клиентов на привязку", "/approve &lt;id&gt; — подтвердить",
                      "/revokeclient &lt;id&gt; — отозвать доступ клиента"]
        if employees.has(employee, P.MANAGE_STAFF):
            lines += ["/staff — сотрудники", "/addstaff &lt;tg_id&gt; &lt;ROLE&gt; &lt;ФИО&gt;",
                      "/grant &lt;tg_id&gt; &lt;ROLE&gt; · /revoke &lt;tg_id&gt; &lt;ROLE&gt;",
                      "/block &lt;tg_id&gt; · /unblock &lt;tg_id&gt;", "/bindcrm &lt;tg_id&gt; &lt;crm_user_id&gt;",
                      "/crmusers [телефон] — пользователи CRM"]
        if employees.has(employee, P.VIEW_AUDIT):
            lines.append("/audit — журнал действий")
        if employees.has(employee, P.CRM_DIAGNOSTICS):
            lines.append("/crmprobe — структура ответов CRM (без значений)")
        lines += ["", "<i>В разработке (этапы B–F): заявки, поиск, создание заявки, задачи, ставки, AI-помощники.</i>"]
        await message.answer(clip("\n".join(lines)))

    @router.message(Command("me"))
    async def me(message: Message, employee: Employee):
        await message.answer(f"ФИО: {esc(employee.full_name)}\nTelegram ID: <code>{employee.telegram_id}</code>\n"
                             f"Роли: {esc(', '.join(sorted(r.value for r in employee.roles)))}\n"
                             f"CRM user ID: {esc(employee.crm_user_id) or 'не привязан'}")

    @router.message(Command("staff"))
    async def staff(message: Message, employee: Employee):
        async with guard(message):
            rows = []
            for e in await employees.list_staff(employee):
                flag = " 🚫" if e.is_blocked else ""
                rows.append(f"<code>{e.telegram_id}</code> {esc(e.full_name)} [{esc(','.join(sorted(r.value for r in e.roles)))}]"
                            f" crm={esc(e.crm_user_id) or '—'}{flag}")
            await message.answer(clip("\n".join(rows) or "Список пуст"))

    @router.message(Command("addstaff"))
    async def addstaff(message: Message, command: CommandObject, employee: Employee):
        async with guard(message):
            tg, roles_s, name = _args(command, 3, "/addstaff <tg_id> <ROLE[,ROLE]> <ФИО>")
            roles = [_role(r) for r in roles_s.split(",")]
            await employees.register(employee, _tg_id(tg), name, roles)
            await message.answer("✅ Сотрудник добавлен. Он может написать боту /start.")

    @router.message(Command("grant"))
    async def grant(message: Message, command: CommandObject, employee: Employee):
        async with guard(message):
            tg, role = _args(command, 2, "/grant <tg_id> <ROLE>")
            await employees.grant_role(employee, _tg_id(tg), _role(role))
            await message.answer("✅ Роль назначена")

    @router.message(Command("revoke"))
    async def revoke(message: Message, command: CommandObject, employee: Employee):
        async with guard(message):
            tg, role = _args(command, 2, "/revoke <tg_id> <ROLE>")
            await employees.revoke_role(employee, _tg_id(tg), _role(role))
            await message.answer("✅ Роль снята")

    @router.message(Command("block"))
    async def block(message: Message, command: CommandObject, employee: Employee):
        async with guard(message):
            (tg,) = _args(command, 1, "/block <tg_id>")
            await employees.block(employee, _tg_id(tg))
            await message.answer("✅ Сотрудник заблокирован")

    @router.message(Command("unblock"))
    async def unblock(message: Message, command: CommandObject, employee: Employee):
        async with guard(message):
            (tg,) = _args(command, 1, "/unblock <tg_id>")
            await employees.unblock(employee, _tg_id(tg))
            await message.answer("✅ Сотрудник разблокирован")

    @router.message(Command("bindcrm"))
    async def bindcrm(message: Message, command: CommandObject, employee: Employee):
        async with guard(message):
            tg, crm_id = _args(command, 2, "/bindcrm <tg_id> <crm_user_id>")
            await employees.set_crm_user(employee, _tg_id(tg), crm_id)
            await message.answer("✅ CRM user ID привязан")

    @router.message(Command("crmusers"))
    async def crmusers(message: Message, command: CommandObject, employee: Employee):
        async with guard(message):
            employees.require(employee, P.MANAGE_STAFF)
            users = await crm.list_users(phone=(command.args or "").strip() or None)
            rows = [f"<code>{esc(u.user_id)}</code> {esc(u.name)} {esc(u.email)}" for u in users]
            await message.answer(clip(banner() + ("\n".join(rows) or "Ничего не найдено")))

    @router.message(Command("crmprobe"))
    async def crmprobe(message: Message, employee: Employee):
        async with guard(message):
            employees.require(employee, P.CRM_DIAGNOSTICS)
            await message.answer(banner() + "Структура ответов CRM (только имена полей и типы, без значений):")
            for key in PROBE_KEYS:
                try:
                    shape = json.dumps(describe_shape(await crm.probe(key)), ensure_ascii=False, indent=1)
                except CrmError as e:
                    shape = f"ошибка: {type(e).__name__}"
                await message.answer(clip(f"<b>{key}</b>\n<pre>{esc(shape[:1500])}</pre>"))

    @router.message(Command("audit"))
    async def audit(message: Message, employee: Employee):
        async with guard(message):
            rows = [f"{e.at:%d.%m %H:%M} {esc(e.actor)} {esc(e.action)} {esc(e.target)}"
                    for e in await employees.recent_audit(employee, 20)]
            await message.answer(clip("\n".join(rows) or "Журнал пуст"))

    @router.message(Command("pending"))
    async def pending(message: Message, employee: Employee):
        async with guard(message):
            items = await client_auth.list_pending(employee, employees)
            rows = [f"#{b.id} tg=<code>{b.telegram_id}</code> {esc(b.org_name)} (org {esc(b.org_key)})" for b in items]
            await message.answer(clip("\n".join(rows) or "Нет заявок на подтверждение"))

    @router.message(Command("approve"))
    async def approve(message: Message, command: CommandObject, employee: Employee):
        async with guard(message):
            (bid,) = _args(command, 1, "/approve <id>")
            await client_auth.approve_binding(employee, _tg_id(bid), employees)
            await message.answer("✅ Привязка клиента подтверждена")

    @router.message(Command("revokeclient"))
    async def revokeclient(message: Message, command: CommandObject, employee: Employee):
        async with guard(message):
            (bid,) = _args(command, 1, "/revokeclient <id>")
            await client_auth.revoke_binding(employee, _tg_id(bid), employees)
            await message.answer("✅ Доступ клиента отозван")

    return router
