"""Transactional lifecycle checks for immutable physical blobs."""

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from file_manager.auth.security import Identity
from file_manager.auth.service import AuthService
from file_manager.db.storage import Storage
from file_manager.errors import ManagerError
from file_manager.files.service import FileService


def authenticated_files(tmp_path: Path) -> tuple[Path, FileService]:
    root = tmp_path / "data"
    credentials = Identity("alice", "password123")
    AuthService(root, credentials).register()
    AuthService(root, credentials).login()
    return root, FileService(root, AuthService.current(root))


def blob_names(root: Path) -> set[str]:
    return {entry.name for entry in (root / "blobs").iterdir()}


def referenced_blobs(root: Path) -> set[str]:
    with sqlite3.connect(root / "manager.sqlite3") as connection:
        return {
            row[0]
            for row in connection.execute("SELECT blob FROM Files WHERE deleted = 0")
            if row[0] is not None
        }


def test_read_only_operation_does_not_scan_orphan_blobs(tmp_path: Path) -> None:
    root, files = authenticated_files(tmp_path)
    files.create("kept.txt", b"kept")
    storage = Storage(root)
    try:
        orphan = storage.put(b"orphan")
    finally:
        storage.close()

    assert files.read("kept.txt") == b"kept"
    assert orphan in blob_names(root)

    files.create("trigger.txt", b"mutation")
    assert orphan not in blob_names(root)


def test_rollback_removes_only_blob_created_by_failed_write(tmp_path: Path) -> None:
    root, files = authenticated_files(tmp_path)
    created = files.create("kept.txt", b"old")
    before = blob_names(root)

    with (
        patch("file_manager.files.service.audit", side_effect=sqlite3.OperationalError("failed")),
        pytest.raises(sqlite3.OperationalError),
    ):
        files.write("kept.txt", b"new", created["revision"])

    assert files.read("kept.txt") == b"old"
    assert blob_names(root) == before == referenced_blobs(root)


def test_rollback_cleanup_failure_preserves_action_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, files = authenticated_files(tmp_path)
    created = files.create("kept.txt", b"old")

    def fail_remove(self: Storage, names: object) -> None:
        raise OSError("cleanup failed")

    monkeypatch.setattr(Storage, "remove", fail_remove)
    with (
        patch("file_manager.files.service.audit", side_effect=sqlite3.OperationalError("failed")),
        pytest.raises(ManagerError, match="временные blobs"),
    ):
        files.write("kept.txt", b"new", created["revision"])

    assert files.read("kept.txt") == b"old"
    with sqlite3.connect(root / "manager.sqlite3") as connection:
        events = connection.execute(
            "SELECT action, path, status FROM Operations "
            "WHERE action IN ('file_write', 'cleanup') ORDER BY id"
        ).fetchall()
    assert ("file_write", "kept.txt", "error") in events
    assert ("cleanup", None, "error") in events


def test_post_commit_cleanup_failure_reports_committed_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, files = authenticated_files(tmp_path)
    created = files.create("kept.txt", b"old")

    def fail_remove(self: Storage, names: object) -> None:
        raise OSError("disk failure")

    monkeypatch.setattr(Storage, "remove", fail_remove, raising=False)
    with pytest.raises(ManagerError, match="зафиксировано"):
        files.write("kept.txt", b"new", created["revision"])

    assert files.read("kept.txt") == b"new"


def test_successful_mutations_leave_only_referenced_blobs(tmp_path: Path) -> None:
    root, files = authenticated_files(tmp_path)
    created = files.create("file.txt", b"first")
    changed = files.write("file.txt", b"second", created["revision"])
    assert blob_names(root) == referenced_blobs(root)

    files.delete("file.txt", changed["revision"])
    assert blob_names(root) == referenced_blobs(root) == set()
