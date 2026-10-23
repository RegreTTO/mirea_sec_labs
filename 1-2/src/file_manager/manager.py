"""Файловые операции: права, неизменяемое содержимое, метаданные и аудит."""

import hashlib
import secrets
import time
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import archives, auth, formats, sessions
from .database import audit, connect
from .disks import list_disks
from .errors import ManagerError
from .limits import (MAX_ARCHIVE_ENTRIES, MAX_EXTRACTED_BYTES, MAX_FILE_BYTES,
                     MAX_USER_BYTES, MAX_USER_FILES)
from .storage import Storage, validate_path


@dataclass
class Operation:
    connection: sqlite3.Connection
    storage: Storage
    user_id: int | None


class FileManager:
    def __init__(self, root: Path, username: str = "", password: str = "", *, session_token: str | None = None):
        self.root = root
        self.username = username
        self.password = password
        self.session_token = session_token

    @classmethod
    def current(cls, root: Path, expected_user: str = ""):
        if not root.expanduser().exists():
            raise ManagerError(sessions.LOGIN_REQUIRED)
        storage = Storage(root)
        try:
            with storage.locked(), connect(storage) as connection:
                try:
                    token = sessions.read_token(storage)
                except ManagerError:
                    audit(connection, "session_check", None, status="denied")
                    raise
                user = sessions.authenticate(connection, token)
                if expected_user and expected_user != user["username"]:
                    audit(connection, "session_check", user["id"], status="denied")
                    raise ManagerError("Сессия другого пользователя. Выполните file-manager login.")
                return cls(root, session_token=token)
        finally:
            storage.close()

    def login(self) -> None:
        storage = Storage(self.root)
        try:
            with storage.locked(), connect(storage) as connection:
                # Отказ проверки пароля сохраняется в аудите до транзакции сессии.
                user_id = auth.authenticate(connection, self.username, self.password, record_success=False)
                connection.execute("BEGIN IMMEDIATE")
                try:
                    token = secrets.token_hex(32)
                    connection.execute(
                        "INSERT INTO Sessions(id, user_id, token_hash, expires_at) VALUES (1, ?, ?, ?) "
                        "ON CONFLICT(id) DO UPDATE SET user_id=excluded.user_id, "
                        "token_hash=excluded.token_hash, expires_at=excluded.expires_at",
                        (user_id, sessions.token_hash(token), int(time.time()) + sessions.SESSION_SECONDS),
                    )
                    sessions.write_token(storage, token)
                    audit(connection, "login", user_id)
                    connection.commit()
                except BaseException as error:
                    connection.rollback()
                    # При сбое после замены файла токен не совпадёт с БД: повторный
                    # login восстановит сессию. Старые данные файлов не затронуты.
                    audit(connection, "login", user_id, status="denied" if isinstance(error, ManagerError) else "error")
                    raise
        finally:
            storage.close()

    def logout(self) -> None:
        with self.operation("logout") as operation:
            operation.connection.execute("DELETE FROM Sessions WHERE id = 1")
            sessions.remove_token(operation.storage)
            audit(operation.connection, "logout", operation.user_id)

    @contextmanager
    def operation(self, action: str, path: str | None = None, *, registration=False):
        storage = Storage(self.root)
        try:
            with storage.locked(), connect(storage) as connection:
                referenced = {row[0] for row in connection.execute(
                    "SELECT blob FROM Files WHERE deleted = 0"
                )}
                storage.prune(referenced)
                if registration:
                    user_id = None
                elif self.session_token is not None:
                    user_id = sessions.authenticate(connection, self.session_token)["id"]
                elif self.username:
                    user_id = auth.authenticate(connection, self.username, self.password)
                else:
                    raise ManagerError(sessions.LOGIN_REQUIRED)
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

    def register(self) -> None:
        with self.operation("register", registration=True) as operation:
            auth.register(operation.connection, self.username, self.password)

    @staticmethod
    def owned_file(operation: Operation, path: str):
        validate_path(path)
        row = operation.connection.execute(
            "SELECT * FROM Files WHERE path = ? AND owner_id = ? AND deleted = 0",
            (path, operation.user_id),
        ).fetchone()
        if row is None:
            raise ManagerError("Файл не найден или недоступен текущему пользователю.")
        return row

    @staticmethod
    def read_content(operation: Operation, row) -> bytes:
        content = operation.storage.read(row["blob"])
        if len(content) != row["size"] or hashlib.sha256(content).hexdigest() != row["digest"]:
            raise ManagerError("Содержимое файла не соответствует метаданным: обнаружено повреждение.")
        return content

    @staticmethod
    def quota(operation: Operation, extra_size: int, extra_files: int) -> None:
        row = operation.connection.execute(
            "SELECT COALESCE(SUM(size), 0), COUNT(*) FROM Files WHERE owner_id = ? AND deleted = 0",
            (operation.user_id,),
        ).fetchone()
        if row[0] + extra_size > MAX_USER_BYTES or row[1] + extra_files > MAX_USER_FILES:
            raise ManagerError("Превышена квота пользователя: 64 МиБ или 1024 файла.")

    @staticmethod
    def metadata(row) -> dict:
        return {key: row[key] for key in ("id", "path", "name", "created_at", "size", "owner_id", "revision")}

    def insert_file(self, operation: Operation, path: str, content: bytes, action: str) -> dict:
        validate_path(path)
        if len(content) > MAX_FILE_BYTES:
            raise ManagerError("Файл превышает лимит 8 МиБ.")
        # Запрет файлового префикса: a и a/b не должны сосуществовать.
        existing = operation.connection.execute(
            "SELECT path FROM Files WHERE owner_id = ? AND deleted = 0", (operation.user_id,)
        )
        for row in existing:
            if path == row["path"] or path.startswith(row["path"] + "/") or row["path"].startswith(path + "/"):
                raise ManagerError("Путь занят файлом или конфликтует с существующим каталогом.")
        self.quota(operation, len(content), 1)
        blob = operation.storage.put(content)
        # Версия монотонна за всю историю пути, включая delete/recreate (защита от ABA).
        revision = operation.connection.execute(
            "SELECT COALESCE(MAX(revision), 0) + 1 FROM Files WHERE owner_id = ? AND path = ?",
            (operation.user_id, path),
        ).fetchone()[0]
        cursor = operation.connection.execute(
            "INSERT INTO Files(owner_id, path, name, created_at, size, blob, digest, revision) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (operation.user_id, path, path.rsplit("/", 1)[-1], datetime.now(timezone.utc).isoformat(),
             len(content), blob, hashlib.sha256(content).hexdigest(), revision),
        )
        audit(operation.connection, action, operation.user_id, path, cursor.lastrowid)
        return self.metadata(self.owned_file(operation, path))

    def create(self, path: str, content: bytes, kind: str = "file") -> dict:
        with self.operation(f"{kind}_create", path) as operation:
            validate_path(path)
            if kind == "json":
                content = formats.json_document(content)
            elif kind == "xml":
                content = formats.xml_document(content)
            elif kind != "file":
                raise ManagerError("Неизвестный формат.")
            return self.insert_file(operation, path, content, f"{kind}_create")

    def read(self, path: str, kind: str = "file") -> bytes:
        with self.operation(f"{kind}_read", path) as operation:
            row = self.owned_file(operation, path)
            content = self.read_content(operation, row)
            if kind == "json":
                content = formats.json_document(content)
            elif kind == "xml":
                content = formats.xml_document(content)
            elif kind != "file":
                raise ManagerError("Неизвестный формат.")
            audit(operation.connection, f"{kind}_read", operation.user_id, path, row["id"])
            return content

    def write(self, path: str, content: bytes, revision: int) -> dict:
        with self.operation("file_write", path) as operation:
            row = self.owned_file(operation, path)
            if row["revision"] != revision:
                raise ManagerError("Конфликт версий: перечитайте файл и укажите его текущую revision.")
            if len(content) > MAX_FILE_BYTES:
                raise ManagerError("Файл превышает лимит 8 МиБ.")
            self.read_content(operation, row)
            self.quota(operation, len(content) - row["size"], 0)
            blob = operation.storage.put(content)
            operation.connection.execute(
                "UPDATE Files SET size = ?, blob = ?, digest = ?, revision = revision + 1 WHERE id = ?",
                (len(content), blob, hashlib.sha256(content).hexdigest(), row["id"]),
            )
            audit(operation.connection, "file_write", operation.user_id, path, row["id"])
            return self.metadata(self.owned_file(operation, path))

    def delete(self, path: str, revision: int) -> None:
        with self.operation("file_delete", path) as operation:
            row = self.owned_file(operation, path)
            if row["revision"] != revision:
                raise ManagerError("Конфликт версий: укажите текущую revision файла.")
            operation.connection.execute(
                "UPDATE Files SET deleted = 1, blob = NULL, revision = revision + 1 WHERE id = ?", (row["id"],)
            )
            audit(operation.connection, "file_delete", operation.user_id, path, row["id"])

    def files(self) -> list[dict]:
        with self.operation("file_list") as operation:
            rows = operation.connection.execute(
                "SELECT * FROM Files WHERE owner_id = ? AND deleted = 0 ORDER BY path", (operation.user_id,)
            )
            result = [self.metadata(row) for row in rows]
            audit(operation.connection, "file_list", operation.user_id)
            return result

    def zip_create(self, path: str, sources: list[str]) -> dict:
        with self.operation("zip_create", path) as operation:
            validate_path(path)
            if len(sources) != len(set(sources)):
                raise ManagerError("Исходные пути ZIP не должны повторяться.")
            if not 1 <= len(sources) <= MAX_ARCHIVE_ENTRIES:
                raise ManagerError("ZIP должен содержать от 1 до 128 файлов.")
            files = {}
            total = 0
            for source in sources:
                row = self.owned_file(operation, source)
                total += row["size"]
                if total > MAX_EXTRACTED_BYTES:
                    raise ManagerError("Суммарный размер исходных файлов превышает 32 МиБ.")
                files[source] = self.read_content(operation, row)
                audit(operation.connection, "zip_source_read", operation.user_id, source, row["id"])
            return self.insert_file(operation, path, archives.create_zip(files), "zip_create")

    def zip_extract(self, path: str, destination: str) -> list[dict]:
        with self.operation("zip_extract", path) as operation:
            validate_path(destination)
            row = self.owned_file(operation, path)
            entries = archives.extract_zip(self.read_content(operation, row))
            result = []
            for name, content in entries.items():
                result.append(self.insert_file(operation, f"{destination}/{name}", content, "zip_extract_file"))
            audit(operation.connection, "zip_extract", operation.user_id, path, row["id"])
            return result

    def disks(self) -> list[dict]:
        with self.operation("disks") as operation:
            result = list_disks()
            audit(operation.connection, "disks", operation.user_id)
            return result

    def history(self) -> list[dict]:
        with self.operation("audit_read") as operation:
            rows = operation.connection.execute(
                "SELECT id, occurred_at, action, file_id, path, status FROM Operations WHERE user_id = ? ORDER BY id",
                (operation.user_id,),
            )
            result = [dict(row) for row in rows]
            audit(operation.connection, "audit_read", operation.user_id)
            return result
