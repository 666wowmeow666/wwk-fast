# Changelog

## Amvera-ready package

- Подключён OAuth2 4logist: password grant, refresh grant, ротация refresh token, безопасное обновление после HTTP 401 на read-only запросах.
- OAuth-реквизиты берутся из ENV; токены не пишутся в логи и хранятся в памяти процесса.
- Добавлены unit-тесты OAuth и ротации токенов.
- Обновлены `.env.example`, README, CRM API notes и инструкция Amvera.
- Добавлена краткая инструкция `START_HERE_AMVERA.md`.
- Обновлён `amvera.yml` по Docker-конфигурации Amvera.
