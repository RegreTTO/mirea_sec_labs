"""Concurrency and identity checks around slow operating-system disk discovery."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path
from threading import Event
from unittest.mock import patch

import pytest

from file_manager.auth.security import Identity
from file_manager.auth.service import AuthService
from file_manager.disks.service import DiskService
from file_manager.errors import ManagerError
from file_manager.files.service import FileService


def services(tmp_path: Path) -> tuple[Path, FileService, DiskService]:
    root = tmp_path / "data"
    credentials = Identity("alice", "password123")
    AuthService(root, credentials).register()
    AuthService(root, credentials).login()
    identity = AuthService.current(root)
    return root, FileService(root, identity), DiskService(root, identity)


def test_slow_disk_discovery_does_not_hold_storage_lock(tmp_path: Path) -> None:
    _, files, disks = services(tmp_path)
    entered = Event()
    release = Event()

    def slow_discovery() -> list[dict[str, object]]:
        entered.set()
        assert release.wait(timeout=2)
        return [{"mount": "/", "filesystem": "test"}]

    with patch("file_manager.disks.service.list_disks", side_effect=slow_discovery):
        with ThreadPoolExecutor(max_workers=2) as executor:
            disk_result = executor.submit(disks.list)
            assert entered.wait(timeout=1)
            file_result = executor.submit(files.files)
            try:
                assert file_result.result(timeout=0.5) == []
            except TimeoutError:
                pytest.fail("disk discovery held the global storage lock")
            finally:
                release.set()
            assert disk_result.result(timeout=1)[0]["mount"] == "/"


def test_session_is_revalidated_after_disk_discovery(tmp_path: Path) -> None:
    root, _, disks = services(tmp_path)

    def revoke_session() -> list[dict[str, object]]:
        with sqlite3.connect(root / "manager.sqlite3", timeout=0) as connection:
            connection.execute("DELETE FROM Sessions")
        return [{"mount": "/", "filesystem": "test"}]

    with (
        patch("file_manager.disks.service.list_disks", side_effect=revoke_session),
        pytest.raises(ManagerError),
    ):
        disks.list()

    with sqlite3.connect(root / "manager.sqlite3") as connection:
        successful = connection.execute(
            "SELECT COUNT(*) FROM Operations WHERE action = 'disks' AND status = 'success'"
        ).fetchone()[0]
    assert successful == 0


def test_disk_discovery_error_is_audited(tmp_path: Path) -> None:
    root, _, disks = services(tmp_path)

    with patch("file_manager.disks.service.list_disks", side_effect=OSError("unavailable")):
        with pytest.raises(OSError):
            disks.list()

    with sqlite3.connect(root / "manager.sqlite3") as connection:
        statuses = {
            row[0]
            for row in connection.execute("SELECT status FROM Operations WHERE action = 'disks'")
        }
    assert "error" in statuses
