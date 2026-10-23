"""Точки монтирования macOS/Linux без запуска команд оболочки."""

import ctypes
import os
import platform
import re

from .errors import ManagerError


def _linux_mounts() -> list[dict]:
    def unescape(value):
        return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), value)

    mounts = []
    with open("/proc/self/mountinfo", encoding="utf-8") as stream:
        for line in stream:
            before, after = line.rstrip("\n").split(" - ", 1)
            fields = before.split()
            source = after.split()
            mounts.append({"mount": unescape(fields[4]), "filesystem": source[0],
                           "device": unescape(source[1])})
    return mounts


def _macos_mounts() -> list[dict]:
    # Darwin statfs из системного <sys/mount.h>; getmntinfo(MNT_NOWAIT)
    # не ждёт ответа отключённого сетевого накопителя.
    class StatFS(ctypes.Structure):
        _fields_ = [
            ("block_size", ctypes.c_uint32), ("io_size", ctypes.c_int32),
            ("blocks", ctypes.c_uint64), ("free", ctypes.c_uint64),
            ("available", ctypes.c_uint64), ("files", ctypes.c_uint64),
            ("free_files", ctypes.c_uint64), ("fsid", ctypes.c_int32 * 2),
            ("owner", ctypes.c_uint32), ("kind", ctypes.c_uint32),
            ("flags", ctypes.c_uint32), ("subtype", ctypes.c_uint32),
            ("filesystem", ctypes.c_char * 16), ("mount", ctypes.c_char * 1024),
            ("device", ctypes.c_char * 1024), ("reserved", ctypes.c_uint32 * 8),
        ]

    libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
    libc.getmntinfo.argtypes = [ctypes.POINTER(ctypes.POINTER(StatFS)), ctypes.c_int]
    libc.getmntinfo.restype = ctypes.c_int
    entries = ctypes.POINTER(StatFS)()
    count = libc.getmntinfo(ctypes.byref(entries), 2)
    if count <= 0:
        raise ManagerError("ОС не вернула список точек монтирования.")
    return [{"mount": os.fsdecode(entries[index].mount),
             "filesystem": os.fsdecode(entries[index].filesystem),
             "device": os.fsdecode(entries[index].device)} for index in range(count)]


def list_disks() -> list[dict]:
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
            mount.update(total_bytes=info.f_blocks * info.f_frsize,
                         free_bytes=info.f_bfree * info.f_frsize,
                         available_bytes=info.f_bavail * info.f_frsize,
                         max_name_length=info.f_namemax)
        except OSError as error:
            mount["error"] = error.strerror or "Накопитель недоступен."
    return mounts
