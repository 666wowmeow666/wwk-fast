class CrmError(Exception):
    """Базовая ошибка интеграции с 4logist."""


class CrmUnavailable(CrmError):
    """CRM недоступна (сеть, 5xx после повторов)."""


class CrmTimeout(CrmUnavailable):
    """Превышено время ожидания."""


class CrmAuthError(CrmError):
    """401/403: токен неверен или нет прав."""


class CrmNotFound(CrmError):
    """404."""


class CrmBadResponse(CrmError):
    """Ответ CRM не соответствует ожидаемой структуре (структура не подтверждена документацией)."""


class CrmForbiddenOperation(CrmError):
    """Операция запрещена политикой безопасности адаптера."""


class CrmWriteOutcomeUnknown(CrmError):
    """Запись могла выполниться, но ответа нет. НЕ повторять автоматически."""
