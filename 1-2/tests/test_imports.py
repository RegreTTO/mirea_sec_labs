"""Domain commands must also import without assembling the CLI first."""

import subprocess
import sys
import unittest


class Imports(unittest.TestCase):
    def test_commands_can_be_imported_independently(self):
        for module in ["auth.commands", "files.commands", "documents.commands", "archives.commands", "audit.commands",
                       "disks.commands"]:
            with self.subTest(module=module):
                code = (
                    "import sys, types; "
                    "sys.modules.setdefault('fcntl', types.ModuleType('fcntl')) "
                    "if sys.platform == 'win32' else None; "
                    f"import file_manager.{module}; "
                    "from file_manager.cli.application import app, main; "
                    "assert callable(app) and callable(main)"
                )
                result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
