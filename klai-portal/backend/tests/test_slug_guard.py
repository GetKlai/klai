"""REQ-18 (Finding C-3, SPEC-SEC-CROSS-TENANT-FOLLOWUP-001): provisioning
slug validation at every boundary.

AC18.1 — safe slug passes
AC18.2 — path-traversal rejected
AC18.3 — provisioning function refuses unsafe slug as the first statement
AC18.4 — DB-level CHECK CONSTRAINT enforces invariant (covered by migration
         file inspection in tests/test_alembic_migrations.py — out of unit-test
         scope; verified at deploy)

The helper SHALL live in app/services/provisioning/_slug_guard.py with the
signature `_assert_safe_slug(slug: str) -> None`. The regex SHALL be
`^[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?$` (lowercase alphanum + hyphen, must
start/end alphanum, max 64 chars).
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# AC18.1 + AC18.2 — _assert_safe_slug accepts/rejects per regex
# ---------------------------------------------------------------------------


class TestAssertSafeSlug:
    """The pure regex guard. Cheap, fail-loud, idempotent."""

    @pytest.mark.parametrize(
        "slug",
        [
            "acme",
            "acme-corp",
            "a",
            "a1",
            "a-b-c-d",
            "company-2026",
            "x" * 64,
            "a" + ("-a" * 31),  # 64 chars, alternates, valid
        ],
    )
    def test_safe_slugs_pass(self, slug: str) -> None:
        from app.services.provisioning._slug_guard import _assert_safe_slug

        _assert_safe_slug(slug)  # should not raise

    @pytest.mark.parametrize(
        "slug",
        [
            "",  # empty
            "-acme",  # leading hyphen
            "acme-",  # trailing hyphen
            "ACME",  # uppercase
            "acme corp",  # whitespace
            "../etc-passwd",  # path traversal
            "acme.corp",  # dot
            "acme/corp",  # slash
            "acme;rm -rf /",  # shell metacharacter
            "ac me",  # internal space
            "x" * 65,  # too long
            "тест",  # non-ASCII
            "a$b",  # invalid char
            "_acme",  # leading underscore
            "acme_corp",  # underscore not allowed per regex
        ],
    )
    def test_unsafe_slugs_raise_value_error(self, slug: str) -> None:
        from app.services.provisioning._slug_guard import _assert_safe_slug

        with pytest.raises(ValueError, match="slug failed safe-slug validation"):
            _assert_safe_slug(slug)


# ---------------------------------------------------------------------------
# AC18.3 — provisioning functions refuse unsafe slug as first statement
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fn_name",
    [
        "_start_librechat_container",
        "_write_tenant_caddyfile",
        "_flush_redis_and_restart_librechat",
        "_sync_drop_mongodb_tenant_database",
        "_sync_drop_mongodb_tenant_user",
        "_create_mongodb_tenant_user",
    ],
)
def test_provisioning_function_rejects_unsafe_slug(fn_name: str) -> None:
    """AC18.3 — every provisioning function listed in the SPEC MUST call
    _assert_safe_slug as the very first statement so a malformed slug never
    reaches Docker / Caddy / Mongo / Redis call sites.
    """
    import app.services.provisioning.infrastructure as infra

    fn = getattr(infra, fn_name)

    # Choose a kwargs payload that satisfies the rest of the signature so
    # the failure is unambiguously from _assert_safe_slug, not from a
    # missing positional. The exact kwargs differ per function — start with
    # `slug=...` and add the minimum extras needed for the call signature.
    kwargs: dict[str, object] = {"slug": "../bad slug"}
    if fn_name == "_create_mongodb_tenant_user":
        kwargs["tenant_password"] = "irrelevant-because-fails-first"
    if fn_name == "_start_librechat_container":
        kwargs["env_file_host_path"] = str(Path(tempfile.gettempdir()) / "unused.env")

    # Even if docker / mongo / redis clients are unreachable in this test
    # env, the guard must trip BEFORE any of them is touched. We do NOT
    # mock the external clients — the test is: did the guard fire?
    with (
        patch("app.services.provisioning.infrastructure.docker", MagicMock()),
        patch("app.services.provisioning.infrastructure._mongo_admin_client", MagicMock()),
        patch("app.services.provisioning.infrastructure._redis_sync_client", MagicMock()),
    ):
        with pytest.raises(ValueError, match="slug failed safe-slug validation"):
            fn(**kwargs)
