from typing import Annotated

import typer

from ..auth.middleware import AuthorizedTyper
from ..cli.common import file_service, input_content, print_content, print_json

app = AuthorizedTyper(no_args_is_help=True, help="Создание, чтение, запись и удаление файлов.")


@app.command("list")
def list_files(context: typer.Context):
    """Показать свои файлы, метаданные и revision для изменения/удаления."""
    print_json(file_service(context).files())


@app.command("create")
def create_file(
    context: typer.Context,
    path: str,
    text: Annotated[str | None, typer.Option(help="Текст; без опции читается stdin.")] = None,
):
    """Создать новый файл. Существующие файлы не перезаписываются."""
    client = file_service(context)
    print_json(client.create(path, input_content(text)))


@app.command("read")
def read_file(context: typer.Context, path: str):
    """Прочитать свой файл. Для двоичных данных перенаправьте stdout."""
    print_content(file_service(context).read(path))


@app.command("write")
def write_file(
    context: typer.Context,
    path: str,
    revision: Annotated[int, typer.Option(min=1, help="Ожидаемая revision из file list.")],
    text: Annotated[str | None, typer.Option(help="Новое содержимое; иначе stdin.")] = None,
):
    """Заменить содержимое только при совпадении версии файла."""
    client = file_service(context)
    print_json(client.write(path, input_content(text), revision))


@app.command("delete")
def delete_file(
    context: typer.Context,
    path: str,
    revision: Annotated[int, typer.Option(min=1, help="Ожидаемая revision из file list.")],
):
    """Удалить файл, сохранив его метаданные и историю в SQLite."""
    file_service(context).delete(path, revision)
    typer.echo("Файл удалён.")
