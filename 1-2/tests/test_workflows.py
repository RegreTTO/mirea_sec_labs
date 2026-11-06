import json
import sqlite3
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from file_manager.auth.service import AuthService
from file_manager.cli.application import app
from file_manager.errors import ManagerError
from file_manager.files.service import FileService


class WorkflowClient:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.runner = CliRunner()

    def invoke(self, *arguments: str, ok: bool = True):
        result = self.runner.invoke(app, ["--root", str(self.root), *arguments])
        if ok:
            assert result.exit_code == 0, (result.output, result.exception)
        else:
            assert result.exit_code != 0, result.output
        return result

    def login(self, user: str = "alice") -> None:
        self.invoke("register", "--user", user, "--password", "password123")
        self.invoke("login", "--user", user, "--password", "password123")


@pytest.fixture
def client(tmp_path: Path) -> WorkflowClient:
    return WorkflowClient(tmp_path / "data")


def test_help_and_argument_errors_need_no_session(client: WorkflowClient) -> None:
    for command in [(), ("file",), ("json",), ("xml",), ("zip",), ("file", "write")]:
        client.invoke(*command, "--help")
    assert not client.root.exists()
    client.invoke("file", "write", "a.txt", ok=False)
    assert not client.root.exists()


def test_session_and_crud_revision(client: WorkflowClient) -> None:
    client.invoke("file", "list", ok=False)
    client.login()
    client.invoke("file", "create", "a.txt", "--text", "hello")
    assert client.invoke("file", "read", "a.txt").stdout == "hello"
    client.invoke("file", "write", "a.txt", "--revision", "1", "--text", "updated")
    client.invoke("file", "write", "a.txt", "--revision", "1", "--text", "lost", ok=False)
    assert client.invoke("file", "read", "a.txt").stdout == "updated"
    client.invoke("file", "delete", "a.txt", "--revision", "2")
    client.invoke("file", "read", "a.txt", ok=False)
    client.invoke("file", "create", "a.txt", "--text", "again")
    assert json.loads(client.invoke("file", "list").stdout)[0]["revision"] > 2
    client.invoke("logout")
    client.invoke("file", "list", ok=False)


def test_documents_and_zip_atomicity(client: WorkflowClient) -> None:
    client.login()
    client.invoke("json", "write", "a.json", "--text", '{"x":1}')
    assert json.loads(client.invoke("json", "read", "a.json").stdout) == {"x": 1}
    client.invoke("xml", "write", "b.xml", "--text", "<root>ok</root>")
    assert "<root>ok</root>" in client.invoke("xml", "read", "b.xml").stdout
    client.invoke("json", "write", "bad.json", "--text", '{"x":1,"x":2}', ok=False)
    client.invoke("xml", "write", "bad.xml", "--text", "<!DOCTYPE root><root/>", ok=False)
    client.invoke("zip", "create", "backup.zip", "a.json", "b.xml")
    client.invoke("zip", "extract", "backup.zip", "restored")
    assert (
        client.invoke("file", "read", "restored/a.json").stdout
        == client.invoke("file", "read", "a.json").stdout
    )
    client.invoke("file", "create", "conflict/b.xml", "--text", "keep")
    client.invoke("zip", "extract", "backup.zip", "conflict", ok=False)
    client.invoke("file", "read", "conflict/a.json", ok=False)
    assert client.invoke("file", "read", "conflict/b.xml").stdout == "keep"


def test_user_isolation_and_stale_session(client: WorkflowClient) -> None:
    client.login()
    old = FileService(client.root, AuthService.current(client.root))
    client.invoke("file", "create", "private.txt", "--text", "secret")
    client.login("bob")
    client.invoke("file", "read", "private.txt", ok=False)
    assert json.loads(client.invoke("file", "list").stdout) == []
    with pytest.raises(ManagerError):
        old.files()
    client.invoke("--user", "alice", "file", "list", ok=False)
    client.invoke("login", "--user", "bob", "--password", "incorrect", ok=False)


def test_rejections_are_audited(client: WorkflowClient) -> None:
    client.login()
    client.invoke("file", "create", "../escape", "--text", "bad", ok=False)
    client.invoke("file", "create", "a.txt", "--text", "ok")
    client.invoke("file", "delete", "a.txt", "--revision", "1")
    rows = json.loads(client.invoke("audit").stdout)
    assert any(row["status"] == "denied" and row["path"] == "../escape" for row in rows)
    assert any(row["action"] == "file_delete" for row in rows)


def test_session_expiry_and_failed_login_preserve_files(client: WorkflowClient) -> None:
    client.login()
    client.invoke("file", "create", "a.txt", "--text", "keep")
    with closing(sqlite3.connect(client.root / "manager.sqlite3")) as connection, connection:
        connection.execute("UPDATE Sessions SET expires_at = 0")
    client.invoke("file", "read", "a.txt", ok=False)
    client.invoke("login", "--user", "alice", "--password", "password123")
    with patch("file_manager.auth.security.write_token", side_effect=OSError("disk full")):
        client.invoke("login", "--user", "alice", "--password", "password123", ok=False)
    assert client.invoke("file", "read", "a.txt").stdout == "keep"
    rows = json.loads(client.invoke("audit").stdout)
    assert any(row["action"] == "session_check" and row["status"] == "denied" for row in rows)
    assert any(row["action"] == "login" and row["status"] == "error" for row in rows)


def test_disks_uses_authenticated_identity_and_audit(client: WorkflowClient) -> None:
    client.invoke("disks", ok=False)
    client.login()
    mounts = [{"mount": "/", "filesystem": "ext4", "total_bytes": 1024}]
    with patch("file_manager.disks.service.list_disks", return_value=mounts):
        assert json.loads(client.invoke("disks").stdout) == mounts
    rows = json.loads(client.invoke("audit").stdout)
    assert any(row["action"] == "disks" and row["status"] == "success" for row in rows)
