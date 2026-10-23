"""Общая авторизация leaf-команд после разбора аргументов и --help."""

import typer
from typer.core import TyperCommand

from .manager import FileManager


class AuthorizedCommand(TyperCommand):
    def invoke(self, context: typer.Context):
        public = (context.parent is not None and context.parent.parent is None
                  and self.name in {"register", "login"})
        if not public:
            settings = context.find_root().obj
            settings["manager"] = FileManager.current(settings["root"], settings["user"])
        return super().invoke(context)


class AuthorizedTyper(typer.Typer):
    def command(self, *args, **kwargs):
        kwargs["cls"] = AuthorizedCommand
        return super().command(*args, **kwargs)
