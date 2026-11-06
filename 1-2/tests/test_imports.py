import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "module",
    [
        "auth.commands",
        "files.commands",
        "documents.commands",
        "archives.commands",
        "audit.commands",
        "disks.commands",
    ],
)
def test_commands_can_be_imported_independently(module: str) -> None:
    code = (
        f"import file_manager.{module}; "
        "from file_manager.cli.application import app, main; "
        "assert callable(app) and callable(main)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
