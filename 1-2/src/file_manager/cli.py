"""Тонкий CLI: ввод, вызов FileManager, безопасный вывод."""

import getpass
import json
import os
import sqlite3
import sys
import warnings
from pathlib import Path
from typing import Annotated

import typer

from .errors import ManagerError
from .limits import MAX_FILE_BYTES
from .manager import FileManager

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
    help="""Безопасный локальный файловый менеджер.

Краткий гайд (запускайте из каталога проекта).
Пароль вводится скрыто. Данные по умолчанию — в .data/.
Пути относятся только к файлам текущего пользователя.

1. Выберите логин и зарегистрируйтесь:
  export FM_USER=alice
  uv run --frozen file-manager register

2. Создайте, прочитайте и измените новый файл:
  uv run --frozen file-manager file create note.txt --text 'Привет'
  uv run --frozen file-manager file read note.txt
  uv run --frozen file-manager file list
  uv run --frozen file-manager file write note.txt --revision 1 --text 'Мир'
  uv run --frozen file-manager file delete note.txt --revision 2
Для своих файлов берите --revision из file list, а не из примера.
Без --text команды записи читают stdin; в терминале запрашивают текст.

3. Сохраните и прочитайте JSON/XML:
  uv run --frozen file-manager json write data.json --text '{"x":1}'
  uv run --frozen file-manager json read data.json
  uv run --frozen file-manager xml write data.xml --text '<root>ok</root>'
  uv run --frozen file-manager xml read data.xml

4. Создайте ZIP и распакуйте его без перезаписи файлов:
  uv run --frozen file-manager zip create backup.zip data.json data.xml
  uv run --frozen file-manager zip extract backup.zip restored

5. Посмотрите диски и журнал своих действий:
  uv run --frozen file-manager disks
  uv run --frozen file-manager audit

Справка по отдельной команде:
  uv run --frozen file-manager file write --help
""",
)
file_app = typer.Typer(no_args_is_help=True, help="Создание, чтение, запись и удаление файлов.")
json_app = typer.Typer(no_args_is_help=True, help="Проверка и сохранение JSON.")
xml_app = typer.Typer(no_args_is_help=True, help="Проверка и сохранение XML без DTD и сущностей.")
zip_app = typer.Typer(no_args_is_help=True, help="Создание и безопасная распаковка ZIP.")
app.add_typer(file_app, name="file")
app.add_typer(json_app, name="json")
app.add_typer(xml_app, name="xml")
app.add_typer(zip_app, name="zip")


@app.callback()
def configure(
    context: typer.Context,
    root: Annotated[Path, typer.Option(help="Закрытый каталог данных (0700).", envvar="FM_ROOT")] = Path(".data"),
    user: Annotated[str, typer.Option(help="Логин пользователя.", envvar="FM_USER")] = "",
):
    context.obj = {"root": root, "user": user}


def manager(context: typer.Context, *, registration=False) -> FileManager:
    settings = context.obj
    if not settings["user"]:
        raise ManagerError("Укажите логин через --user или FM_USER.")
    # Пароль не принимается аргументом: он не должен попадать в историю shell/ps.
    password = os.environ.get("FM_PASSWORD")
    if password is None:
        # getpass отключает эхо до показа приглашения. Запрещаем небезопасный
        # fallback на открытый ввод, когда терминал недоступен.
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            try:
                password = getpass.getpass("Пароль: ", stream=sys.stderr)
                if registration and password != getpass.getpass("Повторите пароль: ", stream=sys.stderr):
                    raise ManagerError("Пароли не совпадают.")
            except getpass.GetPassWarning as error:
                raise ManagerError("Для скрытого ввода нужен терминал. Для автоматизации задайте FM_PASSWORD.") from error
    return FileManager(settings["root"], settings["user"], password)


def input_content(text: str | None) -> bytes:
    if text is not None:
        content = text.encode("utf-8")
    elif sys.stdin.isatty():
        content = typer.prompt("Содержимое", default="", err=True).encode("utf-8")
    else:
        content = sys.stdin.buffer.read(MAX_FILE_BYTES + 1)
    # Проверка размера внутри FileManager сохраняет отказ в аудите.
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


@app.command()
def register(context: typer.Context):
    """Создать пользователя. Пароль запрашивается скрыто с подтверждением."""
    manager(context, registration=True).register()
    typer.echo("Пользователь создан.")


@app.command()
def disks(context: typer.Context):
    """Показать точки монтирования, файловые системы и объём в байтах."""
    print_json(manager(context).disks())


@app.command("audit")
def show_audit(context: typer.Context):
    """Показать журнал текущего пользователя, включая удалённые файлы и отказы."""
    print_json(manager(context).history())


@file_app.command("list")
def list_files(context: typer.Context):
    """Показать свои файлы, метаданные и revision для изменения/удаления."""
    print_json(manager(context).files())


@file_app.command("create")
def create_file(context: typer.Context, path: str,
                text: Annotated[str | None, typer.Option(help="Текст; без опции читается stdin.")] = None):
    """Создать новый файл. Существующие файлы не перезаписываются."""
    client = manager(context)
    print_json(client.create(path, input_content(text)))


@file_app.command("read")
def read_file(context: typer.Context, path: str):
    """Прочитать свой файл. Для двоичных данных перенаправьте stdout."""
    print_content(manager(context).read(path))


@file_app.command("write")
def write_file(context: typer.Context, path: str,
               revision: Annotated[int, typer.Option(min=1, help="Ожидаемая revision из file list.")],
               text: Annotated[str | None, typer.Option(help="Новое содержимое; иначе stdin.")] = None):
    """Заменить содержимое только при совпадении версии файла."""
    client = manager(context)
    print_json(client.write(path, input_content(text), revision))


@file_app.command("delete")
def delete_file(context: typer.Context, path: str,
                revision: Annotated[int, typer.Option(min=1, help="Ожидаемая revision из file list.")]):
    """Удалить файл, сохранив его метаданные и историю в SQLite."""
    manager(context).delete(path, revision)
    typer.echo("Файл удалён.")


@json_app.command("write")
def write_json(context: typer.Context, path: str,
               text: Annotated[str | None, typer.Option(help="JSON; без опции читается stdin.")] = None):
    """Проверить JSON и сохранить новым файлом. Для замены используйте file write."""
    client = manager(context)
    print_json(client.create(path, input_content(text), "json"))


@json_app.command("read")
def read_json(context: typer.Context, path: str):
    """Прочитать и проверить JSON."""
    print_content(manager(context).read(path, "json"))


@xml_app.command("write")
def write_xml(context: typer.Context, path: str,
              text: Annotated[str | None, typer.Option(help="XML; без опции читается stdin.")] = None):
    """Проверить XML и сохранить новым файлом. Для замены используйте file write."""
    client = manager(context)
    print_json(client.create(path, input_content(text), "xml"))


@xml_app.command("read")
def read_xml(context: typer.Context, path: str):
    """Прочитать и проверить XML."""
    print_content(manager(context).read(path, "xml"))


@zip_app.command("create")
def create_zip(context: typer.Context, path: str, sources: list[str]):
    """Создать ZIP из своих файлов: zip create backup.zip a.txt b.txt."""
    print_json(manager(context).zip_create(path, sources))


@zip_app.command("extract")
def extract_zip(context: typer.Context, path: str, destination: str):
    """Распаковать ZIP в новый логический каталог без перезаписи файлов."""
    print_json(manager(context).zip_extract(path, destination))


def main() -> None:
    try:
        app()
    except ManagerError as error:
        typer.echo(f"Ошибка: {error}", err=True)
        raise SystemExit(1) from None
    except (OSError, sqlite3.Error) as error:
        # Системное сообщение может содержать недоверенный путь или части SQL.
        typer.echo(f"Ошибка хранилища ({type(error).__name__}). Операция не подтверждена; проверьте аудит.", err=True)
        raise SystemExit(1) from None
    except (UnicodeError, ValueError, RecursionError):
        typer.echo("Ошибка: некорректные входные данные.", err=True)
        raise SystemExit(1) from None
