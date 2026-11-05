"""Проверки файлового менеджера через его публичный интерфейс."""

import io
import json
import secrets
import sqlite3
import zipfile

import pytest

from file_manager.errors import ManagerError
from file_manager.manager import FileManager


@pytest.fixture
def manager(tmp_path):
    root = tmp_path / "data"
    user = FileManager(root, "alice", secrets.token_urlsafe(24))
    user.register()
    user.login()
    return FileManager.current(root)


def test_file_lifecycle_and_retained_audit(manager):
    created = manager.create("notes/message.txt", b"hello")
    assert created["size"] == 5
    assert manager.read("notes/message.txt") == b"hello"
    changed = manager.write("notes/message.txt", b"world", created["revision"])
    assert changed["revision"] > created["revision"]
    assert manager.read("notes/message.txt") == b"world"
    manager.delete("notes/message.txt", changed["revision"])
    assert manager.files() == []
    with pytest.raises(ManagerError):
        manager.read("notes/message.txt")
    successful = {row["action"] for row in manager.history() if row["status"] == "success"}
    assert {"file_create", "file_write", "file_delete"} <= successful


def test_stale_revision_preserves_content(manager):
    created = manager.create("note.txt", b"first")
    manager.write("note.txt", b"second", created["revision"])
    with pytest.raises(ManagerError):
        manager.write("note.txt", b"lost update", created["revision"])
    assert manager.read("note.txt") == b"second"


@pytest.mark.parametrize("path", ["../escape.txt", "/absolute.txt", "a/../../escape.txt"])
def test_rejects_paths_outside_workspace(manager, path):
    with pytest.raises(ManagerError):
        manager.create(path, b"outside")
    assert manager.files() == []


def test_user_isolation(manager):
    manager.create("private.txt", b"alice only")
    other = FileManager(manager.root, "bob", secrets.token_urlsafe(24))
    other.register()
    other.login()
    bob = FileManager.current(manager.root)
    assert bob.files() == []
    with pytest.raises(ManagerError):
        bob.read("private.txt")
    bob.create("private.txt", b"bob only")
    assert bob.read("private.txt") == b"bob only"


def test_json_and_xml_round_trip(manager):
    manager.create("data.json", b'{"name":"Alice","items":[1,2]}', "json")
    assert json.loads(manager.read("data.json", "json")) == {"name": "Alice", "items": [1, 2]}
    manager.create("data.xml", b"<root><name>Alice</name></root>", "xml")
    assert b"Alice" in manager.read("data.xml", "xml")


@pytest.mark.parametrize("kind,content", [
    ("json", b'{"broken":'),
    ("xml", b"<unclosed>"),
    ("xml", b'<!DOCTYPE root [<!ENTITY x SYSTEM "file:///etc/passwd">]><root>&x;</root>'),
])
def test_invalid_documents_are_not_saved(manager, kind, content):
    with pytest.raises(ManagerError):
        manager.create(f"bad.{kind}", content, kind)
    assert manager.files() == []


def test_zip_round_trip(manager):
    manager.create("source.txt", b"archived")
    manager.zip_create("backup.zip", ["source.txt"])
    manager.zip_extract("backup.zip", "restored")
    assert manager.read("restored/source.txt") == b"archived"


def test_zip_slip_is_rejected(manager):
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("../escape.txt", b"outside")
    manager.create("bad.zip", archive.getvalue())
    with pytest.raises(ManagerError):
        manager.zip_extract("bad.zip", "restored")
    assert [item["path"] for item in manager.files()] == ["bad.zip"]


def test_reopen_preserves_file_metadata_and_audit(manager):
    manager.create("persisted.txt", b"persistent")
    reopened = FileManager.current(manager.root)
    assert reopened.read("persisted.txt") == b"persistent"
    with sqlite3.connect(manager.root / "manager.sqlite3") as database:
        assert database.execute("SELECT size FROM Files WHERE path = ?", ("persisted.txt",)).fetchone() == (10,)
        assert database.execute(
            "SELECT COUNT(*) FROM Operations WHERE action = ? AND status = ?", ("file_create", "success")
        ).fetchone()[0] == 1


def test_logout_revokes_session(manager):
    manager.logout()
    with pytest.raises(ManagerError):
        FileManager.current(manager.root)
