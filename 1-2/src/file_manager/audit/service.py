"""Запись аудита и чтение журнала текущего пользователя."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..auth.security import Identity


def audit(connection, action: str, user_id: int | None, path: str | None = None,
          file_id: int | None = None, status: str = "success") -> None:
    # Не сохраняем содержимое, пароль, исключение или неограниченный пользовательский ввод.
    connection.execute(
        "INSERT INTO Operations(action, user_id, file_id, path, status) VALUES (?, ?, ?, ?, ?)",
        (action, user_id, file_id, path[:1024] if path else None, status),
    )


class AuditService:
    def __init__(self, root: Path, identity: Identity):
        self.root = root
        self.identity = identity

    def history(self) -> list[dict]:
        from ..db.operations import operation_scope

        with operation_scope(self.root, self.identity, "audit_read") as operation:
            rows = operation.connection.execute(
                "SELECT id, occurred_at, action, file_id, path, status FROM Operations WHERE user_id = ? ORDER BY id",
                (operation.user_id,),
            )
            result = [dict(row) for row in rows]
            audit(operation.connection, "audit_read", operation.user_id)
            return result
