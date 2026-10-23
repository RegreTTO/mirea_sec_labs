"""Пароли: уникальная соль, scrypt, сравнение за постоянное время."""

import hashlib
import hmac
import re
import secrets
import sqlite3

from .database import audit
from .errors import ManagerError


def hash_password(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=32768, r=8, p=1, maxmem=64 * 1024 * 1024)


def register(connection, username: str, password: str) -> int:
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


def authenticate(connection, username: str, password: str) -> int:
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
    audit(connection, "login", row["id"])
    return row["id"]
