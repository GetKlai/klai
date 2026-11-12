from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "check-versions-doc.py"

COMPOSE = """services:
  alpha-db:
    image: example/alpha-db:1.2.3
  beta:
    image: registry.example.com/team/beta:v4.5.6
volumes:
  alpha-data:
"""

DEV_COMPOSE = """services:
  alpha-db:
    image: example/alpha-db:1.2.3
"""

CLEAN_DOC = """# Pins

## Core stack: `deploy/docker-compose.yml`

| Service | Why it is held |
|---|---|
| `alpha-db` | Major bumps need a dump and restore. |
| `beta` | Bump only after re-reading its changelog. |

## Dev stack: `docker-compose.dev.yml`

| Service | Notes |
|---|---|
| `alpha-db` | Same as the core stack. |
"""


class VersionsDocTest(unittest.TestCase):
    def run_tree(self, doc: str, compose: str = COMPOSE) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "deploy").mkdir()
            (root / "deploy" / "docker-compose.yml").write_text(compose, encoding="utf-8")
            (root / "docker-compose.dev.yml").write_text(DEV_COMPOSE, encoding="utf-8")
            (root / "deploy" / "VERSIONS.md").write_text(doc, encoding="utf-8")
            return subprocess.run(
                [sys.executable, "-B", str(SCRIPT), str(root)],
                check=False,
                capture_output=True,
                text=True,
            )

    def test_accepts_a_document_that_only_names_existing_services(self) -> None:
        result = self.run_tree(CLEAN_DOC)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_rejects_a_service_compose_no_longer_has(self) -> None:
        result = self.run_tree(CLEAN_DOC.replace("`beta`", "`beta-old`"))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("beta-old", result.stderr)
        self.assertIn("deploy/docker-compose.yml", result.stderr)

    def test_rejects_a_service_cell_that_names_no_compose_service(self) -> None:
        # Without backticks the name was never compared with compose, so a row
        # for a service that no longer exists passed.
        result = self.run_tree(CLEAN_DOC.replace("`beta`", "beta-old"))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("beta-old", result.stderr)

    def test_rejects_a_service_missing_from_the_dev_compose(self) -> None:
        doc = CLEAN_DOC.replace("`alpha-db` | Same", "`beta` | Same")
        result = self.run_tree(doc)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("docker-compose.dev.yml", result.stderr)

    def test_rejects_an_image_column_that_repeats_a_tag(self) -> None:
        doc = CLEAN_DOC.replace(
            "| `alpha-db` | Major",
            "| `alpha-db` | `example/alpha-db:1.2.2` Major",
        )
        result = self.run_tree(doc)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("example/alpha-db:1.2.2", result.stderr)
        self.assertIn("only place a tag or digest lives", result.stderr)

    def test_rejects_a_digest_pinned_reference(self) -> None:
        doc = CLEAN_DOC + "\nPinned at `example/gamma@sha256:0605d1e9`.\n"
        result = self.run_tree(doc)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("example/gamma@sha256", result.stderr)

    def test_rejects_a_dated_sync_footer(self) -> None:
        doc = CLEAN_DOC + "\n*Last repo pin sync: 2026-07-08*\n"
        result = self.run_tree(doc)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("pin sync", result.stderr)

    def test_prose_without_a_backticked_image_reference_is_allowed(self) -> None:
        doc = CLEAN_DOC + "\nThe `ghcr.io/team/*` images use a rolling tag.\n"
        result = self.run_tree(doc)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
