"""Regression scenarios; Windows substitutes only the POSIX storage boundary."""

import importlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import types
import unittest
from contextlib import closing, contextmanager, ExitStack
from unittest.mock import patch

if sys.platform == "win32":
    # Production targets Linux/macOS. No fake POSIX implementation ships with it.
    sys.modules.setdefault("fcntl", types.ModuleType("fcntl"))

from typer.testing import CliRunner
from file_manager.cli.application import app
from file_manager.errors import ManagerError
from file_manager.auth.service import AuthService
from file_manager.files.service import FileService


class Workflows(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory())) / "data"
        self.runner = CliRunner()
        if sys.platform == "win32":
            self.install_storage_boundary()

    def install_storage_boundary(self):
        database = importlib.import_module("file_manager.db.database")
        sessions = importlib.import_module("file_manager.auth.security")
        blobs = {}
        tokens = {}

        class Storage:
            def __init__(self, root):
                self.root = root
                root.mkdir(parents=True, exist_ok=True)

            def close(self):
                pass

            @contextmanager
            def locked(self):
                yield

            def put(self, content):
                import uuid
                name = uuid.uuid4().hex
                blobs[name] = content
                return name

            def read(self, name):
                return blobs[name]

            def prune(self, referenced):
                for name in set(blobs) - referenced:
                    del blobs[name]

        @contextmanager
        def connect(storage):
            connection = sqlite3.connect(storage.root / "manager.sqlite3", isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript(database.SCHEMA)
            try:
                yield connection
            finally:
                connection.close()

        targets = ["file_manager.db.operations", "file_manager.auth.service"]
        for target in targets:
            self.stack.enter_context(patch(target + ".Storage", Storage))
            self.stack.enter_context(patch(target + ".connect", connect))

        def read_token(storage):
            if storage.root not in tokens:
                raise ManagerError(sessions.LOGIN_REQUIRED)
            return tokens[storage.root]

        self.stack.enter_context(patch.object(sessions, "read_token", read_token))
        self.stack.enter_context(patch.object(sessions, "write_token", lambda storage, token: tokens.__setitem__(storage.root, token)))
        self.stack.enter_context(patch.object(sessions, "remove_token", lambda storage: tokens.pop(storage.root, None)))

    def invoke(self, *args, ok=True):
        result = self.runner.invoke(app, ["--root", str(self.root), *args])
        if ok:
            self.assertEqual(result.exit_code, 0, (result.output, result.exception))
        else:
            self.assertNotEqual(result.exit_code, 0, result.output)
        return result

    def login(self, user="alice"):
        self.invoke("register", "--user", user, "--password", "password123")
        self.invoke("login", "--user", user, "--password", "password123")

    def test_help_and_argument_errors_need_no_session(self):
        for command in [(), ("file",), ("json",), ("xml",), ("zip",), ("file", "write")]:
            self.invoke(*command, "--help")
        self.assertFalse(self.root.exists())
        self.invoke("file", "write", "a.txt", ok=False)
        self.assertFalse(self.root.exists())

    def test_session_and_crud_revision(self):
        self.invoke("file", "list", ok=False)
        self.login()
        self.invoke("file", "create", "a.txt", "--text", "hello")
        self.assertEqual(self.invoke("file", "read", "a.txt").stdout, "hello")
        self.invoke("file", "write", "a.txt", "--revision", "1", "--text", "updated")
        self.invoke("file", "write", "a.txt", "--revision", "1", "--text", "lost", ok=False)
        self.assertEqual(self.invoke("file", "read", "a.txt").stdout, "updated")
        self.invoke("file", "delete", "a.txt", "--revision", "2")
        self.invoke("file", "read", "a.txt", ok=False)
        self.invoke("file", "create", "a.txt", "--text", "again")
        self.assertGreater(json.loads(self.invoke("file", "list").stdout)[0]["revision"], 2)
        self.invoke("logout")
        self.invoke("file", "list", ok=False)

    def test_documents_and_zip_atomicity(self):
        self.login()
        self.invoke("json", "write", "a.json", "--text", '{"x":1}')
        self.assertEqual(json.loads(self.invoke("json", "read", "a.json").stdout), {"x": 1})
        self.invoke("xml", "write", "b.xml", "--text", "<root>ok</root>")
        self.assertIn("<root>ok</root>", self.invoke("xml", "read", "b.xml").stdout)
        self.invoke("json", "write", "bad.json", "--text", '{"x":1,"x":2}', ok=False)
        self.invoke("xml", "write", "bad.xml", "--text", '<!DOCTYPE root><root/>', ok=False)
        self.invoke("zip", "create", "backup.zip", "a.json", "b.xml")
        self.invoke("zip", "extract", "backup.zip", "restored")
        self.assertEqual(self.invoke("file", "read", "restored/a.json").stdout, self.invoke("file", "read", "a.json").stdout)
        self.invoke("file", "create", "conflict/b.xml", "--text", "keep")
        self.invoke("zip", "extract", "backup.zip", "conflict", ok=False)
        self.invoke("file", "read", "conflict/a.json", ok=False)
        self.assertEqual(self.invoke("file", "read", "conflict/b.xml").stdout, "keep")

    def test_user_isolation_and_stale_session(self):
        self.login()
        old = FileService(self.root, AuthService.current(self.root))
        self.invoke("file", "create", "private.txt", "--text", "secret")
        self.login("bob")
        self.invoke("file", "read", "private.txt", ok=False)
        self.assertEqual(json.loads(self.invoke("file", "list").stdout), [])
        with self.assertRaises(ManagerError):
            old.files()
        self.invoke("--user", "alice", "file", "list", ok=False)
        self.invoke("login", "--user", "bob", "--password", "incorrect", ok=False)

    def test_rejections_are_audited(self):
        self.login()
        self.invoke("file", "create", "../escape", "--text", "bad", ok=False)
        self.invoke("file", "create", "a.txt", "--text", "ok")
        self.invoke("file", "delete", "a.txt", "--revision", "1")
        rows = json.loads(self.invoke("audit").stdout)
        self.assertTrue(any(row["status"] == "denied" and row["path"] == "../escape" for row in rows))
        self.assertTrue(any(row["action"] == "file_delete" for row in rows))

    def test_session_expiry_and_failed_login_preserve_files(self):
        self.login()
        self.invoke("file", "create", "a.txt", "--text", "keep")
        with closing(sqlite3.connect(self.root / "manager.sqlite3")) as connection, connection:
            connection.execute("UPDATE Sessions SET expires_at = 0")
        self.invoke("file", "read", "a.txt", ok=False)
        self.invoke("login", "--user", "alice", "--password", "password123")
        with patch("file_manager.auth.security.write_token", side_effect=OSError("disk full")):
            self.invoke("login", "--user", "alice", "--password", "password123", ok=False)
        self.assertEqual(self.invoke("file", "read", "a.txt").stdout, "keep")
        rows = json.loads(self.invoke("audit").stdout)
        self.assertTrue(any(row["action"] == "session_check" and row["status"] == "denied" for row in rows))
        self.assertTrue(any(row["action"] == "login" and row["status"] == "error" for row in rows))

    def test_disks_uses_authenticated_identity_and_audit(self):
        self.invoke("disks", ok=False)
        self.login()
        mounts = [{"mount": "/", "filesystem": "ext4", "total_bytes": 1024}]
        with patch("file_manager.disks.service.list_disks", return_value=mounts):
            self.assertEqual(json.loads(self.invoke("disks").stdout), mounts)
        rows = json.loads(self.invoke("audit").stdout)
        self.assertTrue(any(row["action"] == "disks" and row["status"] == "success" for row in rows))


if __name__ == "__main__":
    unittest.main()
