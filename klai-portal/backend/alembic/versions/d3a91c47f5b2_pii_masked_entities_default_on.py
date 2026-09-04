"""SPEC-PRIVACY-PII-POLICY-ADMIN-001 D2: pii_masked_entities default-on

Revision ID: d3a91c47f5b2
Revises: b7c1d2e3f4a5
Create Date: 2026-09-03

Flips ``portal_orgs.pii_masked_entities`` from "per-org, default off"
(migration ``5d8cef52b18c``, REQ-7's original position) to D2's default-on:
the whole return set — ``CREDIT_CARD``, ``EMAIL_ADDRESS``, ``IBAN_CODE``,
``NL_BTW``, ``NL_KVK``, ``NL_POSTCODE``, ``PHONE_NUMBER`` — for every existing
tenant and for every tenant created after this runs.

**Why this is a flip and not a staged rollout.** The ADMIN SPEC's REQ-7 asks
for a tenant-by-tenant rollout via ``KLAI_PII_ENFORCE_ORG_IDS``. That was
overridden by an explicit owner decision on 2026-09-03. What makes the
override cheap rather than reckless: the presidio ``/analyze`` call already
runs on every org-attributed request today, because ``SECRET`` and ``NL_BSN``
are masked unconditionally (``deploy/litellm/klai_pii_enforce.py``
``async_pre_call_hook``). Default-on therefore adds no analyzer call, no
latency, no capacity and no new failure mode — only the substitution changes,
and every entity in this set is restored in the response, so the user's own
text is never lost.

**What this does NOT do.** It is a column default plus a one-time backfill,
not the platform default/floor/versioning model of D4 — PR 3 of the SPEC's
handoff table is still unbuilt and still owed. Concretely: there is no
platform floor here, so a tenant that disables an entity type is simply
disabled; the only entities structurally out of tenant reach remain ``SECRET``
and ``NL_BSN``, which are not stored in this column at all.

Two statements, both idempotent:

1. ``ALTER COLUMN ... SET DEFAULT`` is catalog-only — no table rewrite, no
   row touched — and is what covers tenants created later. All three
   ``PortalOrg(...)`` construction sites omit the field, so the ORM-side
   ``default=`` in ``app/models/portal.py`` covers the same ground for
   application inserts; the server default is the backstop for anything that
   inserts by raw SQL.
2. The backfill is guarded by set equality (``@>`` AND ``<@``) rather than
   array equality, because ``=`` on ``text[]`` is order-sensitive and the one
   org that already opted in (``slug=voys``, all seven) stores them in a
   different order than an arbitrary literal would. Set-guarding makes the
   statement a genuine no-op for any row that is already correct, so a re-run
   — or a manual pre-deploy run — rewrites nothing.

``portal_orgs`` has RLS disabled (``relrowsecurity=f``, verified on
production) and is owned by ``portal_api``, which is the role alembic runs as,
so the UPDATE here needs no policy exemption and no post-deploy superuser
script. That is why this migration may contain DML at all, where
``5d8cef52b18c`` deliberately stayed pure DDL.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "d3a91c47f5b2"
down_revision = "b7c1d2e3f4a5"
branch_labels = None
depends_on = None

# The entity list below is written out in full in both statements rather than
# interpolated from a constant. An f-string would have read better and did, until
# Semgrep's sqlalchemy-execute-raw-query rule blocked CI on it: the rule cannot
# tell a module-level literal from user input, and suppressing a SQL-injection
# rule to keep a nicer-looking string is the wrong trade in a migration. Spelling
# it out removes the question. The list is kept in step with
# ``app.services.pii_entity_policy.PII_DEFAULT_MASKED_ENTITIES`` by
# ``tests/services/test_pii_entity_policy.py::TestDefaultOnIsSingleSourced``,
# which parses this file.


def upgrade() -> None:
    """Make the full return set the default, and give it to every existing org."""
    op.execute(
        """
        ALTER TABLE public.portal_orgs
            ALTER COLUMN pii_masked_entities SET DEFAULT ARRAY[
                'CREDIT_CARD', 'EMAIL_ADDRESS', 'IBAN_CODE', 'NL_BTW',
                'NL_KVK', 'NL_POSTCODE', 'PHONE_NUMBER'
            ]::text[];
        """
    )

    op.execute(
        """
        UPDATE public.portal_orgs
           SET pii_masked_entities = ARRAY[
                'CREDIT_CARD', 'EMAIL_ADDRESS', 'IBAN_CODE', 'NL_BTW',
                'NL_KVK', 'NL_POSTCODE', 'PHONE_NUMBER'
           ]::text[]
         WHERE NOT (
                   pii_masked_entities @> ARRAY[
                        'CREDIT_CARD', 'EMAIL_ADDRESS', 'IBAN_CODE', 'NL_BTW',
                        'NL_KVK', 'NL_POSTCODE', 'PHONE_NUMBER'
                   ]::text[]
               AND pii_masked_entities <@ ARRAY[
                        'CREDIT_CARD', 'EMAIL_ADDRESS', 'IBAN_CODE', 'NL_BTW',
                        'NL_KVK', 'NL_POSTCODE', 'PHONE_NUMBER'
                   ]::text[]
         );
        """
    )


def downgrade() -> None:
    """Restore the empty default. Deliberately does NOT revert the data.

    After ``upgrade()`` there is no column value that distinguishes "this
    tenant was backfilled" from "this tenant admin chose these seven", and no
    value that distinguishes a tenant who has since switched everything off
    from a tenant who never had them on. Clearing the arrays on downgrade
    would therefore destroy real tenant choices to undo a default. The
    reversible half is the default; the data stays.
    """
    op.execute(
        """
        ALTER TABLE public.portal_orgs
            ALTER COLUMN pii_masked_entities SET DEFAULT '{}'::text[];
        """
    )
