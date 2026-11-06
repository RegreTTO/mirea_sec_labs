import hashlib
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from pathlib import Path

from ..auth.operations import Operation, operation_scope
from ..auth.security import Identity
from ..db.audit import audit
from ..errors import ManagerError
from ..limits import MAX_FILE_BYTES, MAX_USER_BYTES, MAX_USER_FILES


def validate_path(path: str) -> str:
    if (
        not path
        or len(path.encode("utf-8")) > 1024
        or path.startswith("/")
        or "\\" in path
        or ":" in path
        or any(ord(char) < 32 or ord(char) == 127 for char in path)
        or any(
            part in ("", ".", "..") or len(part.encode("utf-8")) > 255 for part in path.split("/")
        )
    ):
        raise ManagerError("Нужен относительный путь без '.', '..', ссылок и управляющих символов.")
    return path


class FileService:
    def __init__(self, root: Path, identity: Identity):
        self.root = root
        self.identity = identity

    def operation(
        self, action: str, path: str | None = None, *, mutates_blobs: bool = False
    ) -> AbstractContextManager[Operation]:
        return operation_scope(
            self.root,
            self.identity,
            action,
            path,
            recover_blobs=mutates_blobs,
        )

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
            raise ManagerError(
                "Содержимое файла не соответствует метаданным: обнаружено повреждение."
            )
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
        return {
            key: row[key]
            for key in ("id", "path", "name", "created_at", "size", "owner_id", "revision")
        }

    def insert_file(self, operation: Operation, path: str, content: bytes, action: str) -> dict:
        validate_path(path)
        if len(content) > MAX_FILE_BYTES:
            raise ManagerError("Файл превышает лимит 8 МиБ.")
        # Запрет файлового префикса: a и a/b не должны сосуществовать.
        existing = operation.connection.execute(
            "SELECT path FROM Files WHERE owner_id = ? AND deleted = 0", (operation.user_id,)
        )
        for row in existing:
            if (
                path == row["path"]
                or path.startswith(row["path"] + "/")
                or row["path"].startswith(path + "/")
            ):
                raise ManagerError("Путь занят файлом или конфликтует с существующим каталогом.")
        self.quota(operation, len(content), 1)
        blob = operation.put(content)
        # Версия монотонна за всю историю пути, включая delete/recreate (защита от ABA).
        revision = operation.connection.execute(
            "SELECT COALESCE(MAX(revision), 0) + 1 FROM Files WHERE owner_id = ? AND path = ?",
            (operation.user_id, path),
        ).fetchone()[0]
        cursor = operation.connection.execute(
            "INSERT INTO Files("
            "owner_id, path, name, created_at, size, blob, digest, revision"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                operation.user_id,
                path,
                path.rsplit("/", 1)[-1],
                datetime.now(UTC).isoformat(),
                len(content),
                blob,
                hashlib.sha256(content).hexdigest(),
                revision,
            ),
        )
        audit(operation.connection, action, operation.user_id, path, cursor.lastrowid)
        return self.metadata(self.owned_file(operation, path))

    def create(self, path: str, content: bytes) -> dict:
        with self.operation("file_create", path, mutates_blobs=True) as operation:
            validate_path(path)
            return self.insert_file(operation, path, content, "file_create")

    def read(self, path: str) -> bytes:
        with self.operation("file_read", path) as operation:
            row = self.owned_file(operation, path)
            content = self.read_content(operation, row)
            audit(operation.connection, "file_read", operation.user_id, path, row["id"])
            return content

    def write(self, path: str, content: bytes, revision: int) -> dict:
        with self.operation("file_write", path, mutates_blobs=True) as operation:
            row = self.owned_file(operation, path)
            if row["revision"] != revision:
                raise ManagerError(
                    "Конфликт версий: перечитайте файл и укажите его текущую revision."
                )
            if len(content) > MAX_FILE_BYTES:
                raise ManagerError("Файл превышает лимит 8 МиБ.")
            self.read_content(operation, row)
            self.quota(operation, len(content) - row["size"], 0)
            blob = operation.put(content)
            operation.retire(row["blob"])
            operation.connection.execute(
                "UPDATE Files SET size = ?, blob = ?, digest = ?, "
                "revision = revision + 1 WHERE id = ?",
                (len(content), blob, hashlib.sha256(content).hexdigest(), row["id"]),
            )
            audit(operation.connection, "file_write", operation.user_id, path, row["id"])
            return self.metadata(self.owned_file(operation, path))

    def delete(self, path: str, revision: int) -> None:
        with self.operation("file_delete", path, mutates_blobs=True) as operation:
            row = self.owned_file(operation, path)
            if row["revision"] != revision:
                raise ManagerError("Конфликт версий: укажите текущую revision файла.")
            operation.retire(row["blob"])
            operation.connection.execute(
                "UPDATE Files SET deleted = 1, blob = NULL, revision = revision + 1 WHERE id = ?",
                (row["id"],),
            )
            audit(operation.connection, "file_delete", operation.user_id, path, row["id"])

    def files(self) -> list[dict]:
        with self.operation("file_list") as operation:
            rows = operation.connection.execute(
                "SELECT * FROM Files WHERE owner_id = ? AND deleted = 0 ORDER BY path",
                (operation.user_id,),
            )
            result = [self.metadata(row) for row in rows]
            audit(operation.connection, "file_list", operation.user_id)
            return result
