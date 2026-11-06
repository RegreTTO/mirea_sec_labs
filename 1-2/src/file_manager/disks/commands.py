import typer

from ..auth.middleware import AuthorizedTyper
from ..cli.common import disk_service, print_json

app = AuthorizedTyper()


@app.command()
def disks(context: typer.Context) -> None:
    """Показать точки монтирования, файловые системы и объём в байтах."""
    print_json(disk_service(context).list())
