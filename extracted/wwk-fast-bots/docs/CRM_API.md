# Интеграция с 4logist: что подтверждено документацией, а что нет

Источник: `docs/4logist_openapi.json` (OpenAPI 3.0, «Transport api» v1.0) и сохранённая страница Swagger (`https://wwkfast.4logist.com/doc`).
Реестр методов в коде: `wwk/crm/endpoints.py`; тест `tests/test_endpoints.py` сверяет его с этим файлом.

## Подтверждено
* Базовый адрес из снимка: `https://wwkfast.4logist.com`.
* Тела запросов — `multipart/form-data` с «скобочными» именами полей, например `form_order_api_filter[orderCode]`, `form[clientId]`.
* Пагинация в query: `limit` (максимум 100), `page`, `sort` (`ASC|DESC`).
* **Списки читаются через POST** (`/api/orders/list`, `/api/clients/list`, `/api/requests/list`, `/api/order-documents/list` …). POST ≠ запись.
* Фильтры заказов: `orderCode`, `orderId`, `clientId`, `requestNumber`, `invoiceNumber`, `contractNumber`, `cmrNumber`, `carNumber`, `orderStatus`, даты.
* Фильтры клиентов: только `clientId`, `companyCode`, `phone` — **поиска по названию организации нет**.
* Фильтры документов: `order`, `trip`, `group`, `user`, даты.
* Фильтры пользователей: `phone`, `email`.
* Заявки (`requests`): есть список, архивный список, создание. **Нет** получения по id, фильтров и редактирования.
* Задачи: только `POST /api/tasks/list`. **Создания/изменения задач в API нет** — локальные задачи/напоминания хранятся в нашей БД.
* Назначение ответственных: при создании заявки (`form[user]`, `form[logisticians]`), у заказа — `orders/edit` и `updateAdditionalManager`.
* Рейсы: список, `current-status`, статусы, консигнации, документы рейса.
* Поле ИНН у клиента по формам: `inn_code` («TIN») и `company_code` («TIN/taxpayer ID/VAT№»).

## GET-методы, которые меняют состояние (бот их НЕ вызывает — `Safety.BLOCKED`)
| Метод | Что делает по описанию |
|---|---|
| `GET /api/invoices/send/{invoice}` | отправляет счёт клиенту по email |
| `GET /api/order-documents/{invoiceId}/act/send/{documentTemplateId}` | отправляет акт клиенту |
| `GET /api/warehouses/handle-arrival/{trip}` | меняет статус рейса |
| `GET /api/warehouses/trips/{trip}/handle-departure` | завершает загрузку |
| `GET /api/warehouses/trips/{trip}/handle-arrival` | завершает разгрузку |

## Аутентификация — подтверждено владельцем проекта
Владелец проекта предоставил OAuth2 flow: `POST /oauth/v2/token` с JSON-телом. При первом запросе приложение отправляет `grant_type=password`, `client_id`, `client_secret`, `username`, `password`. Полученные `access_token`, `refresh_token`, `expires_in` хранятся только в памяти процесса. Перед истечением access token обновляется запросом с `grant_type=refresh_token`; если refresh token отклонён с HTTP 400/401, выполняется повторная password-аутентификация. При неожиданном HTTP 401 на безопасном чтении токен обновляется и чтение повторяется один раз. Автоматического повтора записи после 401 нет.

В заголовках запросов используется `Authorization: Bearer <access_token>`. Секреты и токены не записываются в журнал и не сохраняются в Git. После перезапуска приложения выполняется новый password grant. Временный legacy `CRM_TOKEN` оставлен для ручной диагностики, но он не обновляется автоматически.

## НЕ подтверждено — проверить на реальном API
1. **Структура ответов.** Схем ответов в документации нет (только «Success»). Имена полей в `wwk/crm/mappers.py` — предположения. Если поле не найдено, бот **отказывает**, а не угадывает (для авторизации клиента — fail closed).
   Сверка: в боте TEAM команда `/crmprobe` (только ADMIN) показывает структуру ответов **без значений** — её можно безопасно переслать разработчику.
2. Точное ли совпадение фильтра `orderCode` (код дополнительно сверяет точное совпадение сам).
3. Формат `form[clientId]` для массива (используется `form[clientId][]`).
4. Какие группы документов можно показывать клиенту — решает владелец: `CLIENT_VISIBLE_DOC_GROUPS`. По умолчанию клиенту документы **не показываются**, потому что `get-document` в API не проверяет принадлежность документа клиенту, а во внутренних документах могут быть расчёты и маржа.
5. Идемпотентность операций записи — не подтверждена, поэтому запись не повторяется автоматически, а по умолчанию отключена (`CRM_ALLOW_WRITES=0`).
6. Автоповтор чтений (по 502/503/504/сеть/таймаут, до `CRM_MAX_READ_RETRIES`) основан на описаниях «Get …», а не на проверке реального API.
