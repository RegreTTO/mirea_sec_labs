"""Чтение журнала текущего пользователя."""

from pathlib import Path

from ..auth.security import Identity
from ..db.audit import audit
from ..auth.operations import operation_scope


class AuditService:
    def __init__(self, root: Path, identity: Identity):
        self.root = root
        self.identity = identity

    def history(self) -> list[dict]:
        with operation_scope(self.root, self.identity, "audit_read") as operation:
            rows = operation.connection.execute(
                "SELECT id, occurred_at, action, file_id, path, status FROM Operations WHERE user_id = ? ORDER BY id",
                (operation.user_id,),
            )
            result = [dict(row) for row in rows]
            audit(operation.connection, "audit_read", operation.user_id)
            return result
