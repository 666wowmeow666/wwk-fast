# WWK FAST BOTS

Два Telegram-бота для логистической компании WWK FAST с общим ядром и PostgreSQL, интеграция с CRM 4logist.

* **WWK FAST CLIENT** — клиентский бот: вход по номеру заказа + 4 последние цифры ИНН, просмотр заказа и документов.
* **WWK FAST TEAM** — внутренний бот: доступ по Telegram ID, роли ADMIN / MANAGER / LOGISTICIAN, управление сотрудниками, подтверждение клиентов, диагностика CRM.

Стек: Python 3.12, aiogram 3, SQLAlchemy 2 (async) + asyncpg, Alembic, httpx.

## Статус по этапам
| Этап | Состояние |
|---|---|
| A. Основа (конфиг, БД, миграции, авторизация, два бота, CRM-адаптер, тесты) | **сделано**, см. «Что проверено» |
| B. Внутренний бот: заявки, поиск, карточки, комментарии | не начат |
| C. Клиентский бот: связь с менеджером, загрузка документов | не начат (вход, заказ, документы — есть) |
| D. Ставки, пересчёты, согласования, задачи, напоминания | не начат |
| E. Отчёты и аналитика | не начат (управление сотрудниками и журнал — есть) |
| F. AI-модули | не начат |

## Режимы CRM
* `CRM_MODE=mock` (по умолчанию) — демонстрационные данные, боты явно показывают «Тестовый режим». Демо: заказ `WWK-0001`, ИНН-4 `0001`; `WWK-0002` / `0002`.
* `CRM_MODE=real` — подключение к 4logist по OAuth2: access token запрашивается автоматически по `client_id/client_secret/username/password`, затем обновляется через refresh token. **Что подтверждено и что нет — `docs/CRM_API.md`.** Схемы ответов CRM по-прежнему требуют сверки.

## Безопасность (как реализовано)
* Секреты только из переменных окружения; `.env` в `.gitignore` и `.dockerignore`; токены вычищаются из логов.
* Клиент: лимит неудач **и по Telegram ID, и по номеру заказа**; неверный номер и неверный ИНН неразличимы; отозванную привязку нельзя восстановить повторной проверкой; срок жизни привязки; опциональное подтверждение менеджером; принадлежность заказа/документа перепроверяется по CRM при каждом обращении.
* CRM-клиент: вызовы только по реестру методов; автоповтор только чтений; запись — без повторов, по умолчанию выключена; GET-методы, меняющие состояние, заблокированы.
* Сотрудники: по Telegram ID (не username), защита от удаления последнего админа, журнал действий.

## Переменные окружения
См. `.env.example` (все с комментариями). Обязательные: `DATABASE_URL`, хотя бы один из `CLIENT_BOT_TOKEN`/`TEAM_BOT_TOKEN`, `ADMIN_TELEGRAM_IDS` (если задан TEAM-бот). Для `CRM_MODE=real` нужны `CRM_CLIENT_ID`, `CRM_CLIENT_SECRET`, `CRM_USERNAME`, `CRM_PASSWORD` (либо временный legacy `CRM_TOKEN`).

| Переменная | Назначение | По умолчанию |
|---|---|---|
| `CLIENT_BOT_TOKEN`, `TEAM_BOT_TOKEN` | токены ботов | — |
| `ADMIN_TELEGRAM_IDS` | первые админы (Telegram ID через запятую) | — |
| `DATABASE_URL` | PostgreSQL | — |
| `RUN_MIGRATIONS` | применять миграции при старте | `1` |
| `CRM_MODE` | `mock` / `real` | `mock` |
| `CRM_BASE_URL` | адрес 4logist | `https://wwkfast.4logist.com` |
| `CRM_CLIENT_ID`, `CRM_CLIENT_SECRET`, `CRM_USERNAME`, `CRM_PASSWORD` | OAuth2-реквизиты 4logist; токены обновляются автоматически | — |
| `CRM_TOKEN` | временный legacy access token без автообновления | — |
| `CRM_TIMEOUT_SECONDS`, `CRM_MAX_READ_RETRIES` | таймаут и число повторов чтений | `15`, `2` |
| `CRM_ALLOW_WRITES` | разрешить запись в CRM | `0` |
| `CLIENT_MAX_FAILS_PER_TELEGRAM`, `CLIENT_MAX_FAILS_PER_ORDER`, `CLIENT_LOCKOUT_MINUTES` | защита от перебора | `5`, `10`, `30` |
| `CLIENT_BINDING_TTL_DAYS` | срок привязки клиента | `180` |
| `CLIENT_REQUIRE_MANAGER_APPROVAL` | менеджер подтверждает привязку | `0` |
| `CLIENT_VISIBLE_DOC_GROUPS` | группы документов, видимые клиенту | пусто (документы скрыты) |
| `CLIENT_MAX_DOCUMENT_MB` | лимит размера документа | `20` |
| `COMPANY_NAME`, `COMPANY_PHONE`, `COMPANY_REQUISITES` | «О компании» | `WWK FAST` |
| `PORT`, `LOG_LEVEL` | порт `/healthz`, уровень логов | `8080`, `INFO` |

## Команды TEAM-бота (этап A)
`/me`, `/staff`, `/addstaff <tg_id> <ROLE[,ROLE]> <ФИО>`, `/grant`, `/revoke`, `/block`, `/unblock`, `/bindcrm <tg_id> <crm_user_id>`, `/crmusers [телефон]`, `/pending`, `/approve <id>`, `/revokeclient <id>`, `/audit`, `/crmprobe`.

## Структура
```
wwk/config.py, logging_setup.py, app.py, main.py, health.py
wwk/crm/      endpoints.py (реестр методов), client.py (FourlogistClient), mock.py, mappers.py, base.py
wwk/core/     client_auth.py, employees.py, roles.py, models.py, repos.py, memory_repo.py
wwk/db/       models.py, repos.py, migrate.py, session.py
wwk/bots/     client_bot.py, team_bot.py, runner.py
migrations/   Alembic (0001_initial)
tests/        unittest; интеграционные на PostgreSQL — в CI
docs/         CRM_API.md, DEPLOY_AMVERA.md, 4logist_openapi.json
```

## Тесты
`python -m unittest discover -s tests -v`. Без PostgreSQL и aiogram часть тестов пропускается; в GitHub Actions (`.github/workflows/ci.yml`) запускаются все: миграции на пустой БД, интеграционные тесты, сборка ботов, `docker build`.

## Деплой
`docs/DEPLOY_AMVERA.md`.
