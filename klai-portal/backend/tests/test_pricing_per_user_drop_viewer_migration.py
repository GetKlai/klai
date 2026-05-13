"""SPEC-PORTAL-PRICING-PER-USER-001 v0.5.0 — drop-viewer migration shape.

Pure file-content regex checks for ``f1ff304b7b0a_drop_viewer_seat_value.py``,
the migration that narrows both ``seat_type`` CHECK constraints to
``IN ('chat', 'knowledge')`` after Mark's live-UI pivot dropped the viewer
tier.

The migration is structurally safe today (only DROP CONSTRAINT +
CREATE CONSTRAINT DDL — no UPDATE/INSERT on portal_users, so the
``rls-with-check-blocks-migration-update`` trap does NOT trigger here),
but a future "let's also clean up legacy viewer rows" edit could quietly
add an ``UPDATE portal_users SET seat_type = 'chat' WHERE seat_type =
'viewer'`` line — at which point the alembic transaction would crash
on prod with a WITH-CHECK violation, same outcome as the Phase 1
incident.

These tests pin the structural shape so that regression cannot land
silently. See also ``test_pricing_per_user_phase1_migration.py`` for
the same pattern guarding Phase 1's migration.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

MIGRATION_PATH = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "f1ff304b7b0a_drop_viewer_seat_value.py"


@pytest.fixture(scope="module")
def migration_src() -> str:
    return MIGRATION_PATH.read_text()


class TestMigrationChain:
    def test_revision_id_matches_filename(self, migration_src: str) -> None:
        assert re.search(r'^revision\s*=\s*"f1ff304b7b0a"\s*$', migration_src, re.M)

    def test_chains_off_phase1(self, migration_src: str) -> None:
        # f7ef774cdd6b is the parent SPEC's Phase 1 head (after the
        # alembic-rename refactor). f1ff304b7b0a is the only migration
        # that depends on it for the viewer-drop sweep — drift here
        # means we forgot to rebase against the latest Phase 1 fix.
        assert re.search(r'^down_revision\s*=\s*"f7ef774cdd6b"\s*$', migration_src, re.M)


class TestUpgradeDoesNoPortalUsersWrites:
    """Mirror of Phase 1's guard. The migration MUST stay pure-DDL.

    Any UPDATE/INSERT on portal_users will be rejected by the Cat-A
    inline-NULLIF WITH CHECK policy (alembic runs without a tenant
    context — ``current_setting('app.current_org_id', true)`` returns
    empty, NULLIF returns NULL, ``org_id = NULL`` evaluates to NULL,
    every row rejected, transaction rolls back, container crashloops.
    See ``rls-with-check-blocks-migration-update`` pitfall.
    """

    def test_no_update_on_portal_users(self, migration_src: str) -> None:
        # A future edit that "just cleans up the viewer rows" would
        # add ``op.execute("UPDATE portal_users SET seat_type = ...")``
        # — that's the regression.
        forbidden = re.compile(
            r"(?i)UPDATE\s+portal_users\b",
        )
        assert not forbidden.search(migration_src), (
            "Drop-viewer migration must NOT contain UPDATE on portal_users. "
            "Move the data cleanup to a post_deploy_*.sql so it runs as "
            "klai superuser (bypasses FORCE RLS). See pitfall: "
            "rls-with-check-blocks-migration-update."
        )

    def test_no_insert_into_portal_users(self, migration_src: str) -> None:
        forbidden = re.compile(
            r"(?i)INSERT\s+INTO\s+portal_users\b",
        )
        assert not forbidden.search(migration_src)

    def test_no_update_on_portal_user_seat_history(self, migration_src: str) -> None:
        # The history table is Cat-D strict-tenant RLS with the helper
        # call — same crash class. Same guard.
        forbidden = re.compile(
            r"(?i)UPDATE\s+portal_user_seat_history\b",
        )
        assert not forbidden.search(migration_src)


class TestCheckConstraintShape:
    def test_upgrade_drops_old_portal_users_constraint(self, migration_src: str) -> None:
        # IF EXISTS so the migration is idempotent if a partial run left
        # the constraint already-dropped.
        assert re.search(
            r"ALTER\s+TABLE\s+portal_users\s+DROP\s+CONSTRAINT\s+IF\s+EXISTS\s+ck_portal_users_seat_type",
            migration_src,
            re.I,
        )

    def test_upgrade_creates_narrowed_portal_users_constraint(self, migration_src: str) -> None:
        # Two-value set only — viewer is gone. Order of values inside
        # the IN clause does not matter; the test accepts either.
        narrowed_pattern = re.compile(
            r'create_check_constraint\(\s*"ck_portal_users_seat_type"\s*,\s*"portal_users"\s*,'
            r'\s*"seat_type IN \([\'\"](chat|knowledge)[\'\"], [\'\"](chat|knowledge)[\'\"]\)"',
        )
        assert narrowed_pattern.search(migration_src), (
            "Upgrade must re-create ck_portal_users_seat_type with "
            'exactly IN ("chat", "knowledge") (any order). A drift to '
            "three values would re-admit viewer rows."
        )

    def test_upgrade_drops_old_seat_history_constraint(self, migration_src: str) -> None:
        assert re.search(
            r"ALTER\s+TABLE\s+portal_user_seat_history\s+DROP\s+CONSTRAINT\s+IF\s+EXISTS\s+ck_pu_seat_hist_seat_type",
            migration_src,
            re.I,
        )

    def test_upgrade_creates_narrowed_seat_history_constraint(self, migration_src: str) -> None:
        narrowed_pattern = re.compile(
            r'create_check_constraint\(\s*"ck_pu_seat_hist_seat_type"\s*,\s*"portal_user_seat_history"\s*,'
            r'\s*"seat_type IN \([\'\"](chat|knowledge)[\'\"], [\'\"](chat|knowledge)[\'\"]\)"',
        )
        assert narrowed_pattern.search(migration_src)

    def test_upgrade_does_not_admit_viewer(self, migration_src: str) -> None:
        # Defence-in-depth: any literal 'viewer' string inside the
        # upgrade() body (between ``def upgrade`` and ``def downgrade``)
        # is a regression. ``downgrade()`` is allowed to mention viewer
        # (it restores the three-value set).
        upgrade_match = re.search(
            r"def\s+upgrade\(\)\s*->\s*None:\s*\n(.*?)\n(?=def\s+downgrade)",
            migration_src,
            re.S,
        )
        assert upgrade_match is not None, "Could not locate upgrade() body"
        upgrade_body = upgrade_match.group(1)
        assert "viewer" not in upgrade_body.lower(), (
            "upgrade() body contains the string 'viewer' — that string "
            "belongs only in downgrade() (which restores the three-value "
            "set). A viewer reference inside upgrade() means either the "
            "narrowed CHECK still includes viewer (regression) or a stray "
            "comment is misleading."
        )


class TestDowngradeRestoresThreeValueSet:
    def test_downgrade_restores_viewer_on_portal_users(self, migration_src: str) -> None:
        # The downgrade path MUST re-admit viewer so a forward-deploy
        # of stale code with seat_type='viewer' rows can be applied
        # cleanly after rolling back this migration. Without this,
        # rollback breaks every downstream integration test that
        # backfilled viewer rows.
        downgrade_match = re.search(
            r"def\s+downgrade\(\)\s*->\s*None:\s*\n(.*?)\Z",
            migration_src,
            re.S,
        )
        assert downgrade_match is not None
        downgrade_body = downgrade_match.group(1)
        assert "viewer" in downgrade_body.lower()
        # And both constraints are restored to the three-value set.
        assert re.search(
            r"seat_type IN \('viewer', 'chat', 'knowledge'\)",
            downgrade_body,
        )
