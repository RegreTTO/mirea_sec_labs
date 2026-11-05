"""CLI-команды домена disks."""

import typer

from .service import DiskService
from ..auth.middleware import AuthorizedTyper
from ..cli.common import settings
from ..cli.common import print_json


app = AuthorizedTyper()


@app.command()
def disks(context: typer.Context):
    """Показать точки монтирования, файловые системы и объём в байтах."""
    print_json(DiskService(settings(context)["root"], settings(context)["identity"]).list())
