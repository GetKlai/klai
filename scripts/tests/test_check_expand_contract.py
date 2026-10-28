"""Tests for scripts/check-expand-contract.py (SPEC-INFRA-AUTO-ROLLBACK-001 AT-a4).

Each case builds a throwaway git repository with a synthetic service, commits a
base, commits the PR change on top and runs the check against the base.

Run: python3 -m unittest scripts/tests/test_check_expand_contract.py
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "check-expand-contract.py"
MIGRATIONS = "svc/alembic/versions"
APP = "svc/app"

DROP_COLUMN = """
from alembic import op

def upgrade():
    op.drop_column("widgets", "legacy_flag")

def downgrade():
    pass
"""

# The downgrade of an additive migration drops what upgrade added; that must
# not count, and neither may a destructive word in a comment or a docstring.
ADDITIVE = '''
"""Add widgets.colour. Later: op.drop_column("widgets", "legacy_flag")."""
import sqlalchemy as sa
from alembic import op

NOTE = "DROP TABLE widgets is what we will not do here"

def upgrade():
    # op.drop_table("widgets") once nothing reads it
    op.add_column("widgets", sa.Column("colour", sa.Text(), nullable=True))

def downgrade():
    op.drop_column("widgets", "colour")
'''

RAW_SQL = '''
from alembic import op

SQL = """
ALTER TABLE widgets
    DROP COLUMN legacy_flag
"""

def upgrade():
    op.execute(SQL)

def downgrade():
    pass
'''


class ExpandContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        self._git("init", "-q", "-b", "main")
        self._write(f"{APP}/models.py", "class Widget: ...\n")
        self._write(f"{MIGRATIONS}/0001_base.py", "def upgrade():\n    pass\n")
        self.base = self._commit("base")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _git(self, *args: str) -> str:
        return subprocess.run(
            ["git", "-c", "user.email=test@example.com", "-c", "user.name=test", *args],
            cwd=self.repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def _write(self, path: str, content: str) -> None:
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(textwrap.dedent(content))

    def _commit(self, message: str) -> str:
        self._git("add", "-A")
        self._git("commit", "-q", "-m", message)
        return self._git("rev-parse", "HEAD")

    def _check(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), self.base, MIGRATIONS, APP],
            check=False,
            cwd=self.repo,
            capture_output=True,
            text=True,
        )

    def test_drop_column_with_application_code_fails(self) -> None:
        self._write(f"{MIGRATIONS}/0002_drop_legacy.py", DROP_COLUMN)
        self._write(f"{APP}/models.py", "class Widget:\n    colour: str\n")
        self._commit("pr")
        result = self._check()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("0002_drop_legacy.py", result.stdout)
        self.assertIn("drop_column", result.stdout)

    def test_same_migration_alone_passes(self) -> None:
        self._write(f"{MIGRATIONS}/0002_drop_legacy.py", DROP_COLUMN)
        self._write("svc/tests/test_models.py", "def test_x(): ...\n")
        self._commit("contract pr")
        result = self._check()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("contract PR", result.stdout)

    def test_additive_migration_with_code_passes(self) -> None:
        self._write(f"{MIGRATIONS}/0002_add_colour.py", ADDITIVE)
        self._write(f"{APP}/models.py", "class Widget:\n    colour: str\n")
        self._commit("pr")
        result = self._check()
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_raw_sql_drop_in_execute_with_code_fails(self) -> None:
        self._write(f"{MIGRATIONS}/0002_drop_legacy_sql.py", RAW_SQL)
        self._write(f"{APP}/models.py", "class Widget:\n    colour: str\n")
        self._commit("pr")
        result = self._check()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("DROP COLUMN", result.stdout)

    def test_edit_to_an_existing_destructive_migration_does_not_count(self) -> None:
        # A lint sweep over old migrations is not a new contract step.
        self._write(f"{MIGRATIONS}/0002_drop_legacy.py", DROP_COLUMN)
        self.base = self._commit("old contract release")
        self._write(
            f"{MIGRATIONS}/0002_drop_legacy.py", DROP_COLUMN + "\n# reformatted\n"
        )
        self._write(f"{APP}/models.py", "class Widget:\n    colour: str\n")
        self._commit("pr")
        result = self._check()
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_post_deploy_sql_counts_but_comments_and_rollback_scripts_do_not(
        self,
    ) -> None:
        self._write(f"{APP}/models.py", "class Widget:\n    colour: str\n")
        self._write(
            f"{MIGRATIONS}/post_deploy_0002_note.sql",
            "-- DROP TABLE widgets later\nSELECT 1;\n",
        )
        self._write(
            f"{MIGRATIONS}/post_deploy_0002_rollback_x.sql", "DROP TABLE widgets;\n"
        )
        self._commit("pr")
        self.assertEqual(self._check().returncode, 0)
        self._write(
            f"{MIGRATIONS}/post_deploy_0003_drop.sql",
            "/* contract */ DROP TABLE widgets;\n",
        )
        self._commit("pr 2")
        result = self._check()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("post_deploy_0003_drop.sql", result.stdout)


if __name__ == "__main__":
    unittest.main()
