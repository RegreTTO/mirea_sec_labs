"""SQLite: связи, tombstone-записи файлов и сохраняемый аудит."""

import os
import sqlite3
from contextlib import contextmanager

from .errors import ManagerError
from .storage import Storage, check_private_file

SCHEMA = """
CREATE TABLE IF NOT EXISTS Users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    salt BLOB NOT NULL,
    password_hash BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS Sessions (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    user_id INTEGER NOT NULL REFERENCES Users(id),
    token_hash TEXT NOT NULL,
    expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS Files (
    id INTEGER PRIMARY KEY,
    owner_id INTEGER NOT NULL REFERENCES Users(id),
    path TEXT NOT NULL,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    size INTEGER NOT NULL CHECK(size >= 0),
    blob TEXT,
    digest TEXT,
    revision INTEGER NOT NULL DEFAULT 1,
    deleted INTEGER NOT NULL DEFAULT 0 CHECK(deleted IN (0, 1))
);
CREATE UNIQUE INDEX IF NOT EXISTS active_file_path
    ON Files(owner_id, path) WHERE deleted = 0;
CREATE TABLE IF NOT EXISTS Operations (
    id INTEGER PRIMARY KEY,
    occurred_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    action TEXT NOT NULL,
    user_id INTEGER REFERENCES Users(id),
    file_id INTEGER REFERENCES Files(id),
    path TEXT,
    status TEXT NOT NULL CHECK(status IN ('success', 'denied', 'error'))
);
"""


@contextmanager
def connect(storage: Storage):
    # SQLite открывает БД по пути, а не по dir_fd. Каталог закрыт от других UID;
    # одинаковый UID с прямым доступом к хранилищу не входит в модель угроз.
    path = storage.root / "manager.sqlite3"
    existed = os.path.lexists(path)
    if not existed and os.listdir(storage.blobs_fd):
        raise ManagerError("БД отсутствует при существующих файлах. Восстановите БД из резервной копии.")
    fd = os.open(
        "manager.sqlite3", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
        0o600, dir_fd=storage.root_fd,
    )
    try:
        check_private_file(fd)
    finally:
        os.close(fd)
    for suffix in ("-journal", "-wal", "-shm"):
        if os.path.lexists(str(path) + suffix):
            fd = os.open(str(path) + suffix, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                check_private_file(fd)
            finally:
                os.close(fd)
    connection = sqlite3.connect(path, timeout=10, isolation_level=None)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA synchronous = FULL")
        connection.executescript(SCHEMA)
        yield connection
    finally:
        connection.close()


def audit(connection, action: str, user_id: int | None, path: str | None = None,
          file_id: int | None = None, status: str = "success") -> None:
    # Не сохраняем содержимое, пароль, исключение или неограниченный пользовательский ввод.
    connection.execute(
        "INSERT INTO Operations(action, user_id, file_id, path, status) VALUES (?, ?, ?, ?, ?)",
        (action, user_id, file_id, path[:1024] if path else None, status),
    )
