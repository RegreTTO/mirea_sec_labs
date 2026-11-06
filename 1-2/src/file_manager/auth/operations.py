import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from ..db.audit import audit
from ..db.database import connect
from ..db.storage import Storage
from ..errors import CommittedCleanupError, ManagerError
from .security import Identity


@dataclass
class Operation:
    connection: sqlite3.Connection
    storage: Storage
    user_id: int | None
    created_blobs: set[str] = field(default_factory=set)
    retired_blobs: set[str] = field(default_factory=set)

    def put(self, content: bytes) -> str:
        blob = self.storage.put(content)
        self.created_blobs.add(blob)
        return blob

    def retire(self, blob: str | None) -> None:
        if blob is not None:
            self.retired_blobs.add(blob)


def _saved_file_id(
    connection: sqlite3.Connection, user_id: int | None, path: str | None
) -> int | None:
    row = connection.execute(
        "SELECT id FROM Files WHERE owner_id = ? AND path = ? ORDER BY id DESC LIMIT 1",
        (user_id, path),
    ).fetchone()
    return row["id"] if row else None


def _audit_cleanup_error(connection: sqlite3.Connection, user_id: int | None) -> None:
    try:
        audit(connection, "cleanup", user_id, status="error")
    except sqlite3.Error:
        # Доменное изменение уже зафиксировано или откатилось. Ошибка аудита
        # не должна скрывать более точный результат физической очистки.
        pass


@contextmanager
def operation_scope(
    root: Path,
    identity: Identity,
    action: str,
    path: str | None = None,
    *,
    registration: bool = False,
    recover_blobs: bool = False,
) -> Iterator[Operation]:
    storage = Storage(root)
    try:
        with storage.locked(), connect(storage) as connection:
            user_id = None if registration else identity.authenticate(connection)
            if recover_blobs:
                referenced = {
                    row[0]
                    for row in connection.execute(
                        "SELECT blob FROM Files WHERE deleted = 0 AND blob IS NOT NULL"
                    )
                }
                storage.prune(referenced)
            operation = Operation(connection, storage, user_id)
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield operation
                connection.commit()
            except Exception as error:
                connection.rollback()
                cleanup_error: OSError | None = None
                if operation.created_blobs:
                    try:
                        storage.remove(operation.created_blobs)
                    except OSError as caught:
                        cleanup_error = caught
                        _audit_cleanup_error(connection, user_id)
                audit(
                    connection,
                    action,
                    user_id,
                    path,
                    _saved_file_id(connection, user_id, path),
                    "denied" if isinstance(error, ManagerError) else "error",
                )
                if cleanup_error is not None:
                    raise ManagerError(
                        "Операция не зафиксирована, но временные blobs не удалены; "
                        "следующая изменяющая операция повторит очистку."
                    ) from cleanup_error
                raise
            if operation.retired_blobs:
                try:
                    storage.remove(operation.retired_blobs)
                except OSError as error:
                    _audit_cleanup_error(connection, user_id)
                    raise CommittedCleanupError(
                        "Изменение зафиксировано, но старые физические файлы не удалены; "
                        "следующая изменяющая операция повторит очистку."
                    ) from error
    finally:
        storage.close()
