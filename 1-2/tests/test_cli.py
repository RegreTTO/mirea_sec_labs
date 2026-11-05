"""Проверки CLI без доступа к внутренним обработчикам команд."""

import os
import secrets
import subprocess
import sys


def invoke(root, *arguments, password=None):
    environment = {key: value for key, value in os.environ.items() if key not in {"FM_ROOT", "FM_USER", "FM_PASSWORD"}}
    if password is not None:
        environment["FM_PASSWORD"] = password
    return subprocess.run(
        [sys.executable, "-m", "file_manager", "--root", str(root), *arguments],
        input="", capture_output=True, text=True, env=environment, check=False,
    )


def test_cli_session_between_processes(tmp_path):
    root = tmp_path / "data"
    password = secrets.token_urlsafe(24)
    assert invoke(root, "register", "--user", "alice", password=password).returncode == 0
    assert invoke(root, "login", "--user", "alice", password=password).returncode == 0
    assert invoke(root, "file", "create", "hello.txt", "--text", "hello").returncode == 0
    result = invoke(root, "file", "read", "hello.txt")
    assert result.returncode == 0
    assert result.stdout.strip() == "hello"
    assert password not in result.stdout + result.stderr
    assert invoke(root, "logout").returncode == 0
    assert invoke(root, "file", "read", "hello.txt").returncode != 0


def test_cli_requires_login(tmp_path):
    result = invoke(tmp_path / "data", "file", "list")
    assert result.returncode != 0
    assert "login" in result.stderr


def test_cli_help_without_session(tmp_path):
    result = invoke(tmp_path / "data", "--help")
    assert result.returncode == 0
    assert "register" in result.stdout
