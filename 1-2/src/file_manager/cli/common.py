"""Контекст CLI, ограниченный ввод и безопасный вывод."""

import json
import sys
from pathlib import Path
from typing import NotRequired, TypedDict, cast

import typer

from ..audit.service import AuditService
from ..auth.security import Identity
from ..auth.service import AuthService
from ..disks.service import DiskService
from ..errors import ManagerError
from ..files.service import FileService
from ..limits import MAX_FILE_BYTES


class AppSettings(TypedDict):
    root: Path
    user: str
    identity: NotRequired[Identity]


def settings(context: typer.Context) -> AppSettings:
    return cast(AppSettings, context.find_root().obj)


def current_identity(context: typer.Context) -> Identity:
    return settings(context)["identity"]


def file_service(context: typer.Context) -> FileService:
    config = settings(context)
    return FileService(config["root"], current_identity(context))


def auth_service(context: typer.Context) -> AuthService:
    config = settings(context)
    return AuthService(config["root"], current_identity(context))


def audit_service(context: typer.Context) -> AuditService:
    config = settings(context)
    return AuditService(config["root"], current_identity(context))


def disk_service(context: typer.Context) -> DiskService:
    config = settings(context)
    return DiskService(config["root"], current_identity(context))


def input_content(text: str | None) -> bytes:
    if text is not None:
        content = text.encode("utf-8")
    elif sys.stdin.isatty():
        content = typer.prompt("Содержимое", default="", err=True).encode("utf-8")
    else:
        content = sys.stdin.buffer.read(MAX_FILE_BYTES + 1)
    # Проверка размера внутри файлового сервиса сохраняет отказ в аудите.
    return content


def print_json(value: object) -> None:
    # ensure_ascii экранирует также управляющие символы и подмену направления текста.
    typer.echo(json.dumps(value, ensure_ascii=True, indent=2))


def print_content(content: bytes) -> None:
    if sys.stdout.isatty():
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ManagerError("Двоичный файл: перенаправьте вывод в файл.") from error
        # Не исполнять escape-последовательности из недоверенного файла в терминале.
        if any(not char.isprintable() and char not in "\n\t" for char in text):
            raise ManagerError("Файл содержит управляющие символы; перенаправьте вывод в файл.")
        typer.echo(text, nl=False)
    else:
        sys.stdout.buffer.write(content)
        sys.stdout.buffer.flush()
