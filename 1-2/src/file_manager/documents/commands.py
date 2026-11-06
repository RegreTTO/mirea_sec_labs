from typing import Annotated

import typer

from ..auth.middleware import AuthorizedTyper
from ..cli.common import file_service, input_content, print_content, print_json
from .service import DocumentService

json_app = AuthorizedTyper(no_args_is_help=True, help="Проверка и сохранение JSON.")


@json_app.command("write")
def write_json(
    context: typer.Context,
    path: str,
    text: Annotated[str | None, typer.Option(help="JSON; без опции читается stdin.")] = None,
):
    """Проверить JSON и сохранить новым файлом. Для замены используйте file write."""
    client = DocumentService(file_service(context))
    print_json(client.create(path, input_content(text), "json"))


@json_app.command("read")
def read_json(context: typer.Context, path: str):
    """Прочитать и проверить JSON."""
    print_content(DocumentService(file_service(context)).read(path, "json"))


xml_app = AuthorizedTyper(
    no_args_is_help=True, help="Проверка и сохранение XML без DTD и сущностей."
)


@xml_app.command("write")
def write_xml(
    context: typer.Context,
    path: str,
    text: Annotated[str | None, typer.Option(help="XML; без опции читается stdin.")] = None,
):
    """Проверить XML и сохранить новым файлом. Для замены используйте file write."""
    client = DocumentService(file_service(context))
    print_json(client.create(path, input_content(text), "xml"))


@xml_app.command("read")
def read_xml(context: typer.Context, path: str):
    """Прочитать и проверить XML."""
    print_content(DocumentService(file_service(context)).read(path, "xml"))
