"""Создание и безопасная атомарная распаковка ZIP."""

import io
import stat
import zipfile
import zlib

from ..audit.service import audit
from ..errors import ManagerError
from ..files.service import FileService, validate_path
from ..limits import MAX_ARCHIVE_ENTRIES, MAX_EXTRACTED_BYTES, MAX_FILE_BYTES


def create_zip(files: dict[str, bytes]) -> bytes:
    if not files or len(files) > MAX_ARCHIVE_ENTRIES:
        raise ManagerError("ZIP должен содержать от 1 до 128 файлов.")
    if sum(len(content) for content in files.values()) > MAX_EXTRACTED_BYTES:
        raise ManagerError("Суммарный размер исходных файлов превышает 32 МиБ.")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, content in files.items():
            validate_path(path)
            if len(content) > MAX_FILE_BYTES:
                raise ManagerError("Файл превышает лимит 8 МиБ.")
            archive.writestr(path, content)
            if output.tell() > MAX_FILE_BYTES:
                raise ManagerError("ZIP превышает лимит 8 МиБ.")
    result = output.getvalue()
    if len(result) > MAX_FILE_BYTES:
        raise ManagerError("ZIP превышает лимит 8 МиБ.")
    return result


def extract_zip(content: bytes) -> dict[str, bytes]:
    if len(content) > MAX_FILE_BYTES:
        raise ManagerError("ZIP превышает лимит 8 МиБ.")
    result = {}
    seen = set()
    total = 0
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ARCHIVE_ENTRIES:
                raise ManagerError("В ZIP больше 128 элементов.")
            for entry in entries:
                # orig_filename сохраняет NUL, который ZipInfo.filename отбрасывает.
                original = entry.orig_filename
                path = validate_path(original[:-1] if original.endswith("/") else original)
                if path in seen:
                    raise ManagerError("ZIP содержит повторяющиеся пути.")
                seen.add(path)
                mode = entry.external_attr >> 16
                kind = stat.S_IFMT(mode)
                if entry.flag_bits & 1 or kind not in (0, stat.S_IFREG, stat.S_IFDIR):
                    raise ManagerError("Зашифрованные, специальные файлы и ссылки в ZIP запрещены.")
                if entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                    raise ManagerError("Поддерживаются только ZIP Stored и Deflate.")
                if entry.is_dir():
                    if entry.file_size != 0 or kind == stat.S_IFREG:
                        raise ManagerError("Некорректная запись каталога ZIP.")
                    continue
                if kind == stat.S_IFDIR or entry.file_size > MAX_FILE_BYTES:
                    raise ManagerError("Некорректный файл ZIP или размер больше 8 МиБ.")
                parts = []
                size = 0
                with archive.open(entry) as stream:
                    while True:
                        chunk = stream.read(min(65536, MAX_FILE_BYTES - size + 1,
                                                MAX_EXTRACTED_BYTES - total + 1))
                        if not chunk:
                            break
                        size += len(chunk)
                        total += len(chunk)
                        if size > MAX_FILE_BYTES or total > MAX_EXTRACTED_BYTES:
                            raise ManagerError("Превышен фактический лимит распаковки ZIP.")
                        parts.append(chunk)
                result[path] = b"".join(parts)
        if not result:
            raise ManagerError("ZIP не содержит файлов.")
        paths = set(result)
        for path in seen:
            pieces = path.split("/")
            if any("/".join(pieces[:index]) in paths for index in range(1, len(pieces))):
                raise ManagerError("Путь ZIP одновременно обозначает файл и каталог.")
        return result
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError, EOFError, ValueError, zlib.error) as error:
        raise ManagerError("Повреждённый или неподдерживаемый ZIP.") from error


class ArchiveService:
    def __init__(self, files: FileService):
        self.files = files

    def create(self, path: str, sources: list[str]) -> dict:
        with self.files.operation("zip_create", path) as operation:
            validate_path(path)
            if len(sources) != len(set(sources)):
                raise ManagerError("Исходные пути ZIP не должны повторяться.")
            if not 1 <= len(sources) <= MAX_ARCHIVE_ENTRIES:
                raise ManagerError("ZIP должен содержать от 1 до 128 файлов.")
            files = {}
            total = 0
            for source in sources:
                row = self.files.owned_file(operation, source)
                total += row["size"]
                if total > MAX_EXTRACTED_BYTES:
                    raise ManagerError("Суммарный размер исходных файлов превышает 32 МиБ.")
                files[source] = self.files.read_content(operation, row)
                audit(operation.connection, "zip_source_read", operation.user_id, source, row["id"])
            return self.files.insert_file(operation, path, create_zip(files), "zip_create")

    def extract(self, path: str, destination: str) -> list[dict]:
        with self.files.operation("zip_extract", path) as operation:
            validate_path(destination)
            row = self.files.owned_file(operation, path)
            entries = extract_zip(self.files.read_content(operation, row))
            result = []
            for name, content in entries.items():
                result.append(self.files.insert_file(operation, f"{destination}/{name}", content, "zip_extract_file"))
            audit(operation.connection, "zip_extract", operation.user_id, path, row["id"])
            return result
