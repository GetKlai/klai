"""Every SQL migration the installed procrastinate ships after 3.10.0 is applied by a revision here.

Up to procrastinate 3.10.0 the queue schema was installed outside Alembic. From
3.11 on, an upstream migration reaches the database only through a revision in
alembic/versions that names its file, and the service's code expects the schema
of the version it runs: 3.12 failed every defer on the 3.10 schema. A bump that
brings a migration file no revision names turns this red with the file names,
instead of failing at the first defer in production.

Upstream marks each file ``pre`` (apply before the new code runs) or ``post``
(apply once no older code runs). Here that is expand-then-contract: the pre
files ship in a revision with the bump, the post files wait in
``_POST_DEPLOY_PENDING`` and ship in a later revision without application code,
once no image an automatic rollback could restore runs the older version
(scripts/check-expand-contract.py, deploy/scripts/deploy-with-rollback.sh).

This is the schema half of a bump. The code half is
tests/test_worker_lane_supervision_contract.py, which pins the version because
WorkerLifecycle relies on procrastinate internals; a bump changes that pin and
answers both.
"""

from __future__ import annotations

import re
from importlib.metadata import distribution
from pathlib import Path

_VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"
_MIGRATION = re.compile(r"(\d\d)\.(\d\d)\.(\d\d)_\d\d_(?:(pre|post)_)?\w+\.sql")
# The schema this release left behind was in place before Alembic took over.
_INSTALLED_OUTSIDE_ALEMBIC = (3, 10, 0)
# Post-deploy files the bump that brought them cannot apply yet, because the
# previous image still runs the older code: the bump adds them here, and the
# contract revision that applies them later removes them again.
_POST_DEPLOY_PENDING: set[str] = set()


def _kind(name: str) -> str:
    # Upstream: a file that says neither is a post migration.
    match = _MIGRATION.fullmatch(name)
    assert match is not None, name
    return match.group(4) or "post"


def _shipped_after_alembic_took_over() -> set[str]:
    names = set()
    for file in distribution("procrastinate").files or ():
        match = _MIGRATION.fullmatch(file.name)
        if match and tuple(int(part) for part in match.groups()[:3]) > _INSTALLED_OUTSIDE_ALEMBIC:
            names.add(file.name)
    return names


def _named_by_revision() -> dict[str, set[str]]:
    return {
        path.name: {match.group(0) for match in _MIGRATION.finditer(path.read_text())}
        for path in sorted(_VERSIONS.glob("*.py"))
    }


def test_every_procrastinate_migration_since_3_10_is_applied_by_a_revision() -> None:
    shipped = _shipped_after_alembic_took_over()
    named = set().union(*_named_by_revision().values())

    missing = sorted(shipped - named - _POST_DEPLOY_PENDING)
    assert not missing, (
        f"procrastinate ships migrations no Alembic revision applies: {', '.join(missing)}. "
        "Apply the pre files in a revision that ships with the bump; list the post files in "
        "_POST_DEPLOY_PENDING until a later revision without application code applies them "
        "(see this module's docstring)."
    )
    assert not _POST_DEPLOY_PENDING & named, "a revision applies these now; drop them from the list"
    assert all(_kind(name) == "post" for name in _POST_DEPLOY_PENDING)


def test_no_revision_applies_pre_and_post_migrations_together() -> None:
    for revision, names in _named_by_revision().items():
        assert {_kind(name) for name in names} != {"pre", "post"}, (
            f"{revision} applies pre- and post-deploy migrations together; the post ones "
            "must wait until no image an automatic rollback could restore runs the older code"
        )
