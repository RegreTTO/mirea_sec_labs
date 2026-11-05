"""Единая граница блокировки, транзакции, отката и очистки blobs."""

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .database import connect
from .storage import Storage
from ..audit.service import audit
from ..auth.security import Identity
from ..errors import ManagerError


@dataclass
class Operation:
    connection: sqlite3.Connection
    storage: Storage
    user_id: int | None


@contextmanager
def operation_scope(root: Path, identity: Identity, action: str, path: str | None = None, *, registration=False):
    storage = Storage(root)
    try:
        with storage.locked(), connect(storage) as connection:
            referenced = {row[0] for row in connection.execute(
                "SELECT blob FROM Files WHERE deleted = 0"
            )}
            storage.prune(referenced)
            if registration:
                user_id = None
            else:
                user_id = identity.authenticate(connection)
            operation = Operation(connection, storage, user_id)
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield operation
                connection.commit()
            except BaseException as error:
                connection.rollback()
                # INSERT мог быть откатан. Связываем отказ только с записью,
                # которая действительно сохранилась, и с исходным путём операции.
                saved_file = connection.execute(
                    "SELECT id FROM Files WHERE owner_id = ? AND path = ? ORDER BY id DESC LIMIT 1",
                    (user_id, path),
                ).fetchone()
                audit(connection, action, user_id, path, saved_file["id"] if saved_file else None,
                      "denied" if isinstance(error, ManagerError) else "error")
                raise
            finally:
                # После rollback новый blob не имеет ссылки и удаляется. После commit
                # удаляются старые версии. При падении процесса это делает следующий запуск.
                referenced = {row[0] for row in connection.execute(
                    "SELECT blob FROM Files WHERE deleted = 0"
                )}
                try:
                    storage.prune(referenced)
                except OSError as error:
                    audit(connection, "cleanup", user_id, status="error")
                    raise ManagerError(
                        "Не удалось очистить ненужные физические файлы; проверьте аудит и состояние БД."
                    ) from error
    finally:
        storage.close()
