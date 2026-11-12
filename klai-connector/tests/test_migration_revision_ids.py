"""Alembic stores the current revision in ``alembic_version.version_num``, a varchar(32).

A longer revision id passes every test and then fails on deploy, when alembic
tries to record it: the container restarts in a loop until the deploy is rolled
back. That happened twice (the second time on 2026-11-12 with a 37-character id).
"""

import re
from pathlib import Path

VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"


def test_every_revision_id_fits_the_alembic_version_column() -> None:
    too_long = {
        path.name: revision
        for path in VERSIONS.glob("*.py")
        for revision in re.findall(r'^revision(?::\s*str)?\s*=\s*"([^"]+)"', path.read_text(), re.M)
        if len(revision) > 32
    }
    assert not too_long, f"revision ids longer than 32 characters: {too_long}"
