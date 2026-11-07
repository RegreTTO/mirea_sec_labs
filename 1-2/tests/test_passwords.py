"""Пароли Argon2id и перенос существующих scrypt-аккаунтов."""

import hashlib
import secrets
import sqlite3

import pytest
from argon2 import PasswordHasher

from file_manager.auth.security import authenticate_password, register_user
from file_manager.db.database import SCHEMA
from file_manager.errors import ManagerError


@pytest.fixture
def connection():
    with sqlite3.connect(":memory:", isolation_level=None) as database:
        database.row_factory = sqlite3.Row
        database.executescript(SCHEMA)
        yield database


@pytest.mark.parametrize("legacy", [False, True])
def test_password_verification_and_migration(connection, legacy):
    password = secrets.token_urlsafe(24)
    if legacy:
        salt = secrets.token_bytes(16)
        digest = hashlib.scrypt(
            password.encode(), salt=salt, n=32768, r=8, p=1, maxmem=64 * 1024 * 1024,
        )
        user_id = connection.execute(
            "INSERT INTO Users(username, salt, password_hash) VALUES (?, ?, ?)",
            ("alice", salt, digest),
        ).lastrowid
    else:
        user_id = register_user(connection, "alice", password)
    original = connection.execute("SELECT password_hash FROM Users").fetchone()[0]

    with pytest.raises(ManagerError, match="Неверный логин или пароль"):
        authenticate_password(connection, "alice", "wrong-password")
    assert connection.execute("SELECT password_hash FROM Users").fetchone()[0] == original

    assert authenticate_password(connection, "alice", password) == user_id
    stored = connection.execute("SELECT password_hash FROM Users").fetchone()[0]
    assert stored.startswith(b"$argon2id$")
    assert PasswordHasher().verify(stored, password)
    assert authenticate_password(connection, "alice", password) == user_id
    assert connection.execute("SELECT password_hash FROM Users").fetchone()[0] == stored


def test_unknown_user_is_denied(connection):
    with pytest.raises(ManagerError, match="Неверный логин или пароль"):
        authenticate_password(connection, "missing", secrets.token_urlsafe(24))
    assert connection.execute("SELECT COUNT(*) FROM Users").fetchone()[0] == 0
