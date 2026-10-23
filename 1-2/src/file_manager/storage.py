"""Логические пути не становятся путями ОС. Физические файлы неизменяемы."""

import fcntl
import os
import re
import stat
import uuid
from contextlib import contextmanager
from pathlib import Path

from .errors import ManagerError
from .limits import MAX_FILE_BYTES

BLOB_NAME = re.compile(r"[0-9a-f]{32}\Z")


def validate_path(path: str) -> str:
    if (
        not path
        or len(path.encode("utf-8")) > 1024
        or path.startswith("/")
        or "\\" in path
        or ":" in path
        or any(ord(char) < 32 or ord(char) == 127 for char in path)
        or any(part in ("", ".", "..") or len(part.encode("utf-8")) > 255
               for part in path.split("/"))
    ):
        raise ManagerError("Нужен относительный путь без '.', '..', ссылок и управляющих символов.")
    return path


def check_private_file(fd: int) -> None:
    info = os.fstat(fd)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_nlink != 1
        or info.st_mode & 0o077
    ):
        raise ManagerError("Небезопасные тип, владелец, ссылки или права файла хранилища.")


class Storage:
    def __init__(self, root: Path):
        root = root.expanduser().absolute()
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.root = root
        self.root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        self.blobs_fd = None
        try:
            info = os.fstat(self.root_fd)
            if info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ManagerError("Каталог данных должен принадлежать текущему пользователю и иметь права 0700.")
            try:
                os.mkdir("blobs", 0o700, dir_fd=self.root_fd)
            except FileExistsError:
                pass
            self.blobs_fd = os.open(
                "blobs", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.root_fd
            )
            info = os.fstat(self.blobs_fd)
            if info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ManagerError("Небезопасные права каталога blobs.")
            os.fsync(self.root_fd)
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        if self.blobs_fd is not None:
            os.close(self.blobs_fd)
            self.blobs_fd = None
        if self.root_fd is not None:
            os.close(self.root_fd)
            self.root_fd = None

    @contextmanager
    def locked(self):
        fd = os.open(
            "lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
            0o600, dir_fd=self.root_fd,
        )
        try:
            check_private_file(fd)
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def put(self, content: bytes) -> str:
        if len(content) > MAX_FILE_BYTES:
            raise ManagerError("Файл превышает лимит 8 МиБ.")
        name = uuid.uuid4().hex
        fd = os.open(
            name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600, dir_fd=self.blobs_fd,
        )
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.fsync(self.blobs_fd)
        except BaseException:
            os.unlink(name, dir_fd=self.blobs_fd)
            raise
        return name

    def read(self, name: str) -> bytes:
        if not BLOB_NAME.fullmatch(name):
            raise ManagerError("Повреждены метаданные физического файла.")
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.blobs_fd)
        with os.fdopen(fd, "rb") as stream:
            check_private_file(stream.fileno())
            content = stream.read(MAX_FILE_BYTES + 1)
        if len(content) > MAX_FILE_BYTES:
            raise ManagerError("Физический файл превышает лимит 8 МиБ.")
        return content

    def prune(self, referenced: set[str]) -> None:
        """Удалить только созданные приложением файлы без ссылки из SQLite."""
        for name in os.listdir(self.blobs_fd):
            if BLOB_NAME.fullmatch(name) and name not in referenced:
                os.unlink(name, dir_fd=self.blobs_fd)
        os.fsync(self.blobs_fd)
