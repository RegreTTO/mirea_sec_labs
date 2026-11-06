import sqlite3
from pathlib import Path
from typing import Annotated

import typer

from ..archives.commands import app as zip_app
from ..audit.commands import app as audit_app
from ..auth.commands import app as auth_app
from ..auth.middleware import AuthorizedTyper
from ..disks.commands import app as disks_app
from ..documents.commands import json_app, xml_app
from ..errors import ManagerError
from ..files.commands import app as file_app
from .common import AppSettings

app = AuthorizedTyper(
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
    help="""1. Зарегистрируйтесь и войдите:

file-manager register
file-manager login

2. Создайте, прочитайте и измените новый файл:
file-manager file create note.txt --text 'Привет'
file-manager file read note.txt
file-manager file list
file-manager file write note.txt --revision 1 --text 'Мир'
file-manager file delete note.txt --revision 2

3. Сохраните и прочитайте JSON/XML:
file-manager json write data.json --text '{"x":1}'
file-manager json read data.json
file-manager xml write data.xml --text '<root>ok</root>'
file-manager xml read data.xml

4. Создайте ZIP и распакуйте его без перезаписи файлов:
file-manager zip create backup.zip data.json data.xml
file-manager zip extract backup.zip restored

5. Посмотрите диски и журнал своих действий:
file-manager disks
file-manager audit

6. Войдите под указанным пользователем:
file-manager login --user alice

7. Завершите сессию:
file-manager logout

Справка по отдельной команде:
file-manager file write --help
""",
)
app.add_typer(auth_app)
app.add_typer(disks_app)
app.add_typer(audit_app)
app.add_typer(file_app, name="file")
app.add_typer(json_app, name="json")
app.add_typer(xml_app, name="xml")
app.add_typer(zip_app, name="zip")


@app.callback()
def configure(
    context: typer.Context,
    root: Annotated[
        Path, typer.Option(help="Закрытый каталог данных (0700).", envvar="FM_ROOT")
    ] = Path(".data"),
    user: Annotated[
        str,
        typer.Option(help="Логин для register/login; ожидаемый логин сессии для остальных команд."),
    ] = "",
) -> None:
    context.obj = AppSettings(root=root, user=user)


def main() -> None:
    try:
        app()
    except ManagerError as error:
        typer.echo(f"Ошибка: {error}", err=True)
        raise SystemExit(1) from None
    except (OSError, sqlite3.Error) as error:
        # Системное сообщение может содержать недоверенный путь или части SQL.
        typer.echo(
            f"Ошибка хранилища ({type(error).__name__}). "
            "Операция не подтверждена; проверьте аудит.",
            err=True,
        )
        raise SystemExit(1) from None
    except (UnicodeError, ValueError, RecursionError):
        typer.echo("Ошибка: некорректные входные данные.", err=True)
        raise SystemExit(1) from None
