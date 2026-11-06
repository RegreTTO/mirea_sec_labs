"""Регистрация, вход, проверка текущей сессии и выход."""

import secrets
import time
from pathlib import Path

from ..db.audit import audit
from ..db.database import connect
from ..db.storage import Storage
from ..errors import ManagerError
from . import security
from .operations import operation_scope
from .security import Identity


class AuthService:
    def __init__(self, root: Path, identity: Identity | None = None) -> None:
        self.root = root
        self.identity = identity or Identity()

    @classmethod
    def current(cls, root: Path, expected_user: str = "") -> Identity:
        if not root.expanduser().exists():
            raise ManagerError(security.LOGIN_REQUIRED)
        storage = Storage(root)
        try:
            with storage.locked(), connect(storage) as connection:
                try:
                    token = security.read_token(storage)
                except ManagerError:
                    audit(connection, "session_check", None, status="denied")
                    raise
                user = security.authenticate_session(connection, token)
                if expected_user and expected_user != user["username"]:
                    audit(connection, "session_check", user["id"], status="denied")
                    raise ManagerError("Сессия другого пользователя. Выполните file-manager login.")
                return Identity(session_token=token)
        finally:
            storage.close()

    def login(self) -> None:
        storage = Storage(self.root)
        try:
            with storage.locked(), connect(storage) as connection:
                # Отказ проверки пароля сохраняется в аудите до транзакции сессии.
                user_id = security.authenticate_password(
                    connection, self.identity.username, self.identity.password, record_success=False
                )
                connection.execute("BEGIN IMMEDIATE")
                try:
                    token = secrets.token_hex(32)
                    connection.execute(
                        "INSERT INTO Sessions(id, user_id, token_hash, expires_at) "
                        "VALUES (1, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                        "user_id=excluded.user_id, token_hash=excluded.token_hash, "
                        "expires_at=excluded.expires_at",
                        (
                            user_id,
                            security.token_hash(token),
                            int(time.time()) + security.SESSION_SECONDS,
                        ),
                    )
                    security.write_token(storage, token)
                    audit(connection, "login", user_id)
                    connection.commit()
                except Exception as error:
                    connection.rollback()
                    # При сбое после замены файла токен не совпадёт с БД: повторный
                    # login восстановит сессию. Старые данные файлов не затронуты.
                    audit(
                        connection,
                        "login",
                        user_id,
                        status="denied" if isinstance(error, ManagerError) else "error",
                    )
                    raise
        finally:
            storage.close()

    def logout(self) -> None:
        with operation_scope(self.root, self.identity, "logout") as operation:
            operation.connection.execute("DELETE FROM Sessions WHERE id = 1")
            security.remove_token(operation.storage)
            audit(operation.connection, "logout", operation.user_id)

    def register(self) -> None:
        with operation_scope(self.root, self.identity, "register", registration=True) as operation:
            security.register_user(
                operation.connection, self.identity.username, self.identity.password
            )
