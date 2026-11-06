class ManagerError(Exception):
    """Ошибка, которую можно безопасно показать пользователю."""


class CommittedCleanupError(ManagerError):
    """Изменение зафиксировано, но физическая очистка не завершилась."""
