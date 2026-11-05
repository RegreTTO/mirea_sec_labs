"""Контекст CLI, ограниченный ввод и безопасный вывод."""

import json
import sys

import typer

from ..errors import ManagerError
from ..files.service import FileService
from ..limits import MAX_FILE_BYTES


def settings(context: typer.Context) -> dict:
    return context.find_root().obj


def file_service(context: typer.Context) -> FileService:
    config = settings(context)
    return FileService(config["root"], config["identity"])


def input_content(text: str | None) -> bytes:
    if text is not None:
        content = text.encode("utf-8")
    elif sys.stdin.isatty():
        content = typer.prompt("Содержимое", default="", err=True).encode("utf-8")
    else:
        content = sys.stdin.buffer.read(MAX_FILE_BYTES + 1)
    # Проверка размера внутри файлового сервиса сохраняет отказ в аудите.
    return content


def print_json(value) -> None:
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
