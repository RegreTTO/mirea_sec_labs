"""Точки монтирования, сведения о дисках и аудит обращений."""

import os
import platform
import re
import subprocess
from pathlib import Path

from ..auth.operations import operation_scope
from ..auth.security import Identity
from ..db.audit import audit
from ..errors import ManagerError


def _linux_mounts() -> list[dict[str, object]]:
    def unescape(value: str) -> str:
        return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), value)

    mounts: list[dict[str, object]] = []
    with open("/proc/self/mountinfo", encoding="utf-8") as stream:
        for line in stream:
            before, after = line.rstrip("\n").split(" - ", 1)
            fields = before.split()
            source = after.split()
            mounts.append(
                {
                    "mount": unescape(fields[4]),
                    "filesystem": source[0],
                    "device": unescape(source[1]),
                }
            )
    return mounts


def _macos_mounts() -> list[dict[str, object]]:
    try:
        completed = subprocess.run(
            ["/sbin/mount"],
            capture_output=True,
            check=True,
            encoding="utf-8",
            timeout=5,
        )
    except subprocess.TimeoutExpired as error:
        raise ManagerError("ОС не ответила при получении списка точек монтирования.") from error
    except (subprocess.CalledProcessError, OSError) as error:
        raise ManagerError("Не удалось получить список точек монтирования.") from error

    mounts: list[dict[str, object]] = []
    for line in completed.stdout.splitlines():
        if not line:
            continue
        location, details_separator, details = line.rpartition(" (")
        device, mount_separator, mount = location.rpartition(" on ")
        mount_details = details.removesuffix(")")
        filesystem = mount_details.partition(",")[0]
        if (
            not details_separator
            or not mount_separator
            or not details.endswith(")")
            or "(" in mount_details
            or ")" in mount_details
            or not device
            or not mount
            or not filesystem
        ):
            raise ManagerError("ОС вернула некорректную запись о точке монтирования.")
        mounts.append({"mount": mount, "filesystem": filesystem, "device": device})

    if not mounts:
        raise ManagerError("ОС не вернула список точек монтирования.")

    return mounts


def list_disks() -> list[dict[str, object]]:
    system = platform.system()
    if system == "Darwin":
        mounts = _macos_mounts()
    elif system == "Linux":
        mounts = _linux_mounts()
    else:
        raise ManagerError("Поддерживаются macOS и Linux.")
    for mount in mounts:
        try:
            info = os.statvfs(mount["mount"])
            mount.update(
                total_bytes=info.f_blocks * info.f_frsize,
                free_bytes=info.f_bfree * info.f_frsize,
                available_bytes=info.f_bavail * info.f_frsize,
                max_name_length=info.f_namemax,
            )
        except OSError as error:
            mount["error"] = error.strerror or "Накопитель недоступен."
    return mounts


class DiskService:
    def __init__(self, root: Path, identity: Identity) -> None:
        self.root = root
        self.identity = identity

    def list(self) -> list[dict[str, object]]:
        # Не удерживаем lock и SQLite-транзакцию во время потенциально
        # медленного обращения к локальным и сетевым файловым системам.
        with operation_scope(self.root, self.identity, "disks"):
            pass
        try:
            result = list_disks()
        except Exception:
            with operation_scope(self.root, self.identity, "disks") as operation:
                audit(operation.connection, "disks", operation.user_id, status="error")
            raise
        with operation_scope(self.root, self.identity, "disks") as operation:
            audit(operation.connection, "disks", operation.user_id)
        return result
