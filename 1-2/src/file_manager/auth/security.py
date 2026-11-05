"""Пароли, токены сессий и проверка учётных данных одного вызова."""

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import time
from dataclasses import dataclass

from ..db.audit import audit
from ..errors import ManagerError
from ..db.storage import Storage, check_private_file


def hash_password(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=32768, r=8, p=1, maxmem=64 * 1024 * 1024)


def register_user(connection, username: str, password: str) -> int:
    if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,64}", username):
        raise ManagerError("Логин: 1–64 латинских букв, цифр, точек, '_' или '-'.")
    if not 8 <= len(password) <= 1024:
        raise ManagerError("Пароль должен содержать от 8 до 1024 символов.")
    salt = secrets.token_bytes(16)
    digest = hash_password(password, salt)
    try:
        cursor = connection.execute(
            "INSERT INTO Users(username, salt, password_hash) VALUES (?, ?, ?)",
            (username, salt, digest),
        )
    except sqlite3.IntegrityError as error:
        raise ManagerError("Этот логин уже занят.") from error
    audit(connection, "register", cursor.lastrowid)
    return cursor.lastrowid


def authenticate_password(connection, username: str, password: str, *, record_success: bool = True) -> int:
    row = connection.execute("SELECT * FROM Users WHERE username = ?", (username,)).fetchone()
    if len(password) > 1024:
        audit(connection, "login", row["id"] if row else None, status="denied")
        raise ManagerError("Неверный логин или пароль.")
    # Даже отсутствующий логин требует вычисления хеша.
    salt = row["salt"] if row else b"\0" * 16
    expected = row["password_hash"] if row else b"\0" * 64
    if not hmac.compare_digest(hash_password(password, salt), expected) or row is None:
        audit(connection, "login", row["id"] if row else None, status="denied")
        raise ManagerError("Неверный логин или пароль.")
    if record_success:
        audit(connection, "login", row["id"])
    return row["id"]


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


def authenticate_session(connection, token: str):
    row = connection.execute(
        "SELECT Users.id, Users.username, Sessions.expires_at FROM Sessions "
        "JOIN Users ON Users.id = Sessions.user_id WHERE Sessions.id = 1 AND token_hash = ?",
        (token_hash(token),),
    ).fetchone()
    if row is None or row["expires_at"] <= int(time.time()):
        audit(connection, "session_check", row["id"] if row else None, status="denied")
        raise ManagerError(LOGIN_REQUIRED)
    return row


@dataclass(frozen=True)
class Identity:
    username: str = ""
    password: str = ""
    session_token: str | None = None

    def authenticate(self, connection) -> int:
        if self.session_token is not None:
            return authenticate_session(connection, self.session_token)["id"]
        if self.username:
            return authenticate_password(connection, self.username, self.password)
        raise ManagerError(LOGIN_REQUIRED)
