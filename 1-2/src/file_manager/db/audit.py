"""SQL-запись события аудита без зависимостей от сервисов приложения."""


def audit(
    connection,
    action: str,
    user_id: int | None,
    path: str | None = None,
    file_id: int | None = None,
    status: str = "success",
) -> None:
    # Не сохраняем содержимое, пароль, исключение или неограниченный пользовательский ввод.
    connection.execute(
        "INSERT INTO Operations(action, user_id, file_id, path, status) VALUES (?, ?, ?, ?, ?)",
        (action, user_id, file_id, path[:1024] if path else None, status),
    )
