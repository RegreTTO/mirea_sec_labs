import typer

from ..auth.middleware import AuthorizedTyper
from ..cli.common import file_service, print_json
from .service import ArchiveService

app = AuthorizedTyper(no_args_is_help=True, help="Создание и безопасная распаковка ZIP.")


@app.command("create")
def create_zip(context: typer.Context, path: str, sources: list[str]):
    """Создать ZIP из своих файлов: zip create backup.zip a.txt b.txt."""
    print_json(ArchiveService(file_service(context)).create(path, sources))


@app.command("extract")
def extract_zip(context: typer.Context, path: str, destination: str):
    """Распаковать ZIP в новый логический каталог без перезаписи файлов."""
    print_json(ArchiveService(file_service(context)).extract(path, destination))
