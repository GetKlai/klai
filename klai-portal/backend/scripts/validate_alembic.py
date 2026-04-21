"""Alembic integrity check — fails CI on duplicate rev ids or multiple heads.

Run from ``klai-portal/backend`` (or set CWD there via the caller). Used by
``.github/workflows/portal-api.yml`` to prevent regressions like SPEC-KB-020
(duplicate rev id `a1b2c3d4e5f6`) and SPEC-PROV-001 (fake hand-typed rev id
plus orphan head) from landing on main again.

Two invariants:

1. **Single head.** ``alembic heads`` must return exactly one revision id.
   Multiple heads mean two migration branches never merged — running
   ``alembic upgrade head`` will fail at deploy time.

2. **No duplicate rev ids.** Alembic emits a ``UserWarning`` when two files
   declare the same ``revision = "xxx"``. This script promotes that warning
   to an error via ``warnings.filterwarnings("error")`` and surfaces it as
   a non-zero exit code.

Exit codes:
  0 — all checks pass
  1 — duplicate rev id, multiple heads, or DAG parse failure
"""

from __future__ import annotations

import sys
import warnings
from pathlib import Path


def main() -> int:
    # Promote any alembic UserWarning (including "Revision X is present more
    # than once") to an error so it breaks the build.
    warnings.filterwarnings("error", category=UserWarning, module=r"alembic\..*")

    script_location = Path(__file__).resolve().parent.parent / "alembic"
    if not script_location.is_dir():
        print(f"ERROR: alembic directory not found at {script_location}", file=sys.stderr)
        return 1

    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory
    except ImportError as exc:
        print(f"ERROR: alembic is not installed ({exc})", file=sys.stderr)
        return 1

    alembic_ini = script_location.parent / "alembic.ini"
    cfg = Config(str(alembic_ini))

    try:
        script = ScriptDirectory.from_config(cfg)
        heads = list(script.get_heads())
    except UserWarning as exc:
        print(f"FAIL: alembic DAG parse failed: {exc}", file=sys.stderr)
        print(
            "\nA migration was likely added with a revision id that already exists. "
            "Generate ids via `alembic revision -m 'message'` — never hand-type them.",
            file=sys.stderr,
        )
        return 1
    except Exception as exc:
        print(f"ERROR: could not parse alembic DAG: {exc!r}", file=sys.stderr)
        return 1

    if len(heads) != 1:
        print(
            f"FAIL: alembic has {len(heads)} heads, expected 1: {heads}",
            file=sys.stderr,
        )
        print(
            "\nFix with a merge migration: `alembic merge heads -m 'merge heads'`.",
            file=sys.stderr,
        )
        return 1

    print(f"OK: single alembic head = {heads[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
