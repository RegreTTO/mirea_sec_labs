"""Одна локальная сессия на каталог данных; пароль на диск не записывается."""

import hashlib
import os
import re
import time

from .database import audit
from .errors import ManagerError
from .storage import Storage, check_private_file

SESSION_SECONDS = 24 * 60 * 60
LOGIN_REQUIRED = "Нет действующей сессии. Выполните file-manager login."


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def read_token(storage: Storage) -> str:
    try:
        fd = os.open("session", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=storage.root_fd)
        with os.fdopen(fd, "rb") as stream:
            check_private_file(stream.fileno())
            content = stream.read(65)
        token = content.decode("ascii")
        if not re.fullmatch(r"[0-9a-f]{64}", token):
            raise ManagerError(LOGIN_REQUIRED)
        return token
    except (OSError, UnicodeError, ManagerError) as error:
        raise ManagerError(LOGIN_REQUIRED) from error


def write_token(storage: Storage, token: str) -> None:
    # lock хранилища уже удерживается. Удаляем temp, оставшийся после сбоя login.
    try:
        os.unlink("session.tmp", dir_fd=storage.root_fd)
    except FileNotFoundError:
        pass
    fd = os.open("session.tmp", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                 0o600, dir_fd=storage.root_fd)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(token.encode("ascii"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace("session.tmp", "session", src_dir_fd=storage.root_fd, dst_dir_fd=storage.root_fd)
        os.fsync(storage.root_fd)
    finally:
        try:
            os.unlink("session.tmp", dir_fd=storage.root_fd)
        except FileNotFoundError:
            pass


def remove_token(storage: Storage) -> None:
    try:
        os.unlink("session", dir_fd=storage.root_fd)
    except FileNotFoundError:
        pass
    os.fsync(storage.root_fd)


def authenticate(connection, token: str):
    row = connection.execute(
        "SELECT Users.id, Users.username, Sessions.expires_at FROM Sessions "
        "JOIN Users ON Users.id = Sessions.user_id WHERE Sessions.id = 1 AND token_hash = ?",
        (token_hash(token),),
    ).fetchone()
    if row is None or row["expires_at"] <= int(time.time()):
        audit(connection, "session_check", row["id"] if row else None, status="denied")
        raise ManagerError(LOGIN_REQUIRED)
    return row
