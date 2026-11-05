"""CLI-команды домена audit."""

import typer

from .service import AuditService
from ..auth.middleware import AuthorizedTyper
from ..cli.common import settings
from ..cli.common import print_json


app = AuthorizedTyper()


@app.command("audit")
def show_audit(context: typer.Context):
    """Показать журнал текущего пользователя, включая удалённые файлы и отказы."""
    print_json(AuditService(settings(context)["root"], settings(context)["identity"]).history())
