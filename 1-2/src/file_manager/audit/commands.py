"""CLI-команды домена audit."""

import typer

from ..auth.middleware import AuthorizedTyper
from ..cli.common import audit_service, print_json

app = AuthorizedTyper()


@app.command("audit")
def show_audit(context: typer.Context) -> None:
    """Показать журнал текущего пользователя, включая удалённые файлы и отказы."""
    print_json(audit_service(context).history())
