from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "check-env-file-scope.py"


class EnvFileScopeTest(unittest.TestCase):
    def run_fixture(self, content: str) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as temp_dir:
            fixture = Path(temp_dir) / "compose.yml"
            fixture.write_text(content, encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(SCRIPT), str(fixture)],
                check=False,
                capture_output=True,
                text=True,
            )

    def test_rejects_all_supported_bare_env_forms(self) -> None:
        fixtures = {
            "scalar": "services:\n  app:\n    env_file: .env\n",
            "scalar-comment": "services:\n  app:\n    env_file: .env # shared\n",
            "scalar-single-quoted": "services:\n  app:\n    env_file: '.env'\n",
            "scalar-double-quoted": 'services:\n  app:\n    env_file: ".env"\n',
            "block-list": "services:\n  app:\n    env_file:\n      - .env",
            "block-list-comment": "services:\n  app:\n    env_file:\n      - '.env' # shared\n",
            "block-long-syntax": "services:\n  app:\n    env_file:\n      - path: .env\n        required: true\n",
            "block-flow-mapping": "services:\n  app:\n    env_file:\n      - {path: '.env', required: true}\n",
            "inline-list": "services:\n  app:\n    env_file: [./app/.env, .env]\n",
            "inline-long-syntax": 'services:\n  app:\n    env_file: [{path: ".env", required: false}]\n',
            "multiline-flow-list": "services:\n  app:\n    env_file: [\n      ./app/.env,\n      \".env\"\n    ]\n",
        }

        for name, fixture in fixtures.items():
            with self.subTest(name=name):
                result = self.run_fixture(fixture)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("bare .env", result.stderr)

    def test_allows_scoped_paths_and_explicit_environment(self) -> None:
        fixture = """services:
  app:
    env_file:
      - ./app/.env
      - path: ./.env
        required: false
    environment:
      ENV_PATH: .env
  worker:
    env_file: [./worker/.env, ".env.production"]
"""
        result = self.run_fixture(fixture)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("env-scope-guard: OK", result.stdout)


if __name__ == "__main__":
    unittest.main()
