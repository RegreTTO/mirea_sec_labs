"""Регистрация, вход, выход и ввод учётных данных."""

import getpass
import os
import sys
import warnings
from typing import Annotated

import typer

from ..cli.common import auth_service, settings
from ..errors import ManagerError
from .middleware import AuthorizedTyper
from .security import Identity
from .service import AuthService


def credentials(
    context: typer.Context,
    user: str | None,
    password: str | None,
    *,
    registration: bool = False,
) -> AuthService:
    config = settings(context)
    username = user if user is not None else config["user"] or os.environ.get("FM_USER", "")
    if not username:
        username = typer.prompt("Логин", err=True)
    if password is not None:
        typer.echo(
            "Предупреждение: --password виден в истории shell и списке процессов. "
            "Предпочитайте скрытый ввод.",
            err=True,
        )
    else:
        password = os.environ.get("FM_PASSWORD")
    if password is None:
        # getpass отключает эхо до показа приглашения. Запрещаем небезопасный
        # fallback на открытый ввод, когда терминал недоступен.
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            try:
                password = getpass.getpass("Пароль: ", stream=sys.stderr)
                if registration and password != getpass.getpass(
                    "Повторите пароль: ", stream=sys.stderr
                ):
                    raise ManagerError("Пароли не совпадают.")
            except getpass.GetPassWarning as error:
                raise ManagerError(
                    "Для скрытого ввода нужен терминал. Для автоматизации задайте FM_PASSWORD."
                ) from error
    return AuthService(config["root"], Identity(username, password))


app = AuthorizedTyper()


@app.command()
def register(
    context: typer.Context,
    user: Annotated[
        str | None, typer.Option("--user", help="Логин; без опции запрашивается интерактивно.")
    ] = None,
    password: Annotated[
        str | None, typer.Option(help="Пароль. Небезопасно: аргумент виден в истории shell и ps.")
    ] = None,
) -> None:
    """Создать пользователя. Без опций логин и пароль запрашиваются интерактивно."""
    credentials(context, user, password, registration=True).register()
    typer.echo("Пользователь создан. Выполните file-manager login.")


@app.command()
def login(
    context: typer.Context,
    user: Annotated[
        str | None, typer.Option("--user", help="Логин; без опции запрашивается интерактивно.")
    ] = None,
    password: Annotated[
        str | None, typer.Option(help="Пароль. Небезопасно: аргумент виден в истории shell и ps.")
    ] = None,
) -> None:
    """Войти и сохранить локальную сессию на 24 часа без сохранения пароля."""
    credentials(context, user, password).login()
    typer.echo("Вход выполнен. Сессия сохранена на 24 часа.")


@app.command()
def logout(context: typer.Context) -> None:
    """Завершить текущую сессию и удалить локальный токен."""
    auth_service(context).logout()
    typer.echo("Выход выполнен.")
