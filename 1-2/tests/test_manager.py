"""Интеграционные проверки через публичные доменные сервисы."""

import io
import json
import secrets
import sqlite3
import zipfile

import pytest

from file_manager.errors import ManagerError
from file_manager.archives.service import ArchiveService
from file_manager.audit.service import AuditService
from file_manager.auth.security import Identity
from file_manager.auth.service import AuthService
from file_manager.documents.service import DocumentService
from file_manager.files.service import FileService


@pytest.fixture
def manager(tmp_path):
    root = tmp_path / "data"
    user = AuthService(root, Identity("alice", secrets.token_urlsafe(24)))
    user.register()
    user.login()
    return FileService(root, AuthService.current(root))


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
    successful = {
        row["action"] for row in AuditService(manager.root, manager.identity).history()
        if row["status"] == "success"
    }
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
    other = AuthService(manager.root, Identity("bob", secrets.token_urlsafe(24)))
    other.register()
    other.login()
    bob = FileService(manager.root, AuthService.current(manager.root))
    assert bob.files() == []
    with pytest.raises(ManagerError):
        bob.read("private.txt")
    bob.create("private.txt", b"bob only")
    assert bob.read("private.txt") == b"bob only"


def test_json_and_xml_round_trip(manager):
    documents = DocumentService(manager)
    documents.create("data.json", b'{"name":"Alice","items":[1,2]}', "json")
    assert json.loads(documents.read("data.json", "json")) == {"name": "Alice", "items": [1, 2]}
    documents.create("data.xml", b"<root><name>Alice</name></root>", "xml")
    assert b"Alice" in documents.read("data.xml", "xml")


@pytest.mark.parametrize("kind,content", [
    ("json", b'{"broken":'),
    ("xml", b"<unclosed>"),
    ("xml", b'<!DOCTYPE root [<!ENTITY x SYSTEM "file:///etc/passwd">]><root>&x;</root>'),
])
def test_invalid_documents_are_not_saved(manager, kind, content):
    with pytest.raises(ManagerError):
        DocumentService(manager).create(f"bad.{kind}", content, kind)
    assert manager.files() == []


def test_zip_round_trip(manager):
    manager.create("source.txt", b"archived")
    archives = ArchiveService(manager)
    archives.create("backup.zip", ["source.txt"])
    archives.extract("backup.zip", "restored")
    assert manager.read("restored/source.txt") == b"archived"


def test_zip_slip_is_rejected(manager):
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("../escape.txt", b"outside")
    manager.create("bad.zip", archive.getvalue())
    with pytest.raises(ManagerError):
        ArchiveService(manager).extract("bad.zip", "restored")
    assert [item["path"] for item in manager.files()] == ["bad.zip"]


def test_reopen_preserves_file_metadata_and_audit(manager):
    manager.create("persisted.txt", b"persistent")
    reopened = FileService(manager.root, AuthService.current(manager.root))
    assert reopened.read("persisted.txt") == b"persistent"
    with sqlite3.connect(manager.root / "manager.sqlite3") as database:
        assert database.execute("SELECT size FROM Files WHERE path = ?", ("persisted.txt",)).fetchone() == (10,)
        assert database.execute(
            "SELECT COUNT(*) FROM Operations WHERE action = ? AND status = ?", ("file_create", "success")
        ).fetchone()[0] == 1


def test_logout_revokes_session(manager):
    AuthService(manager.root, manager.identity).logout()
    with pytest.raises(ManagerError):
        AuthService.current(manager.root)
