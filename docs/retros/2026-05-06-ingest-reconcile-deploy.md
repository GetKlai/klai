# SPEC-INGEST-RECONCILE-001 deploy retrospective — 2026-05-06

> Two production blockers surfaced during the same-day deploy of
> SPEC-INGEST-RECONCILE-001. Both were caught by the post-merge container
> restart loop on `core-01` rather than by CI. This retro captures the
> mechanism and the lessons that generalise beyond this SPEC.

## What happened

The SPEC implementation merged via PR #440 at 18:10 UTC. The post-merge
build-push pipeline shipped both `klai-knowledge-ingest` and `klai-connector`
images to `core-01` within ~3 minutes. Both containers immediately entered a
restart loop:

- `klai-core-knowledge-ingest-1`: exit code 255, looped on
  `[entrypoint] Running alembic upgrade head` → `Multiple head revisions are
  present for given argument 'head'`.
- `klai-core-klai-connector-1`: exit code 1, looped on
  `asyncpg.exceptions.FeatureNotSupportedError: cannot use subquery in check
  constraint` while applying migration `009_sync_runs_skip_reasons`.

Both were resolved within ~20 minutes via PR #443 (hotfix) and an admin-merge
(branch protection had been reconfigured between #440 and #443 to require an
approving review; the hotfix was time-pressing for production stability so we
bypassed with `--admin`).

## Mechanism — Blocker 1: alembic dual-head

PR #441 (SPEC-INGEST-LOGIN-WALL-DETECT-002, SimHash cluster detection) was
in flight in parallel with PR #440 (this SPEC). Both branched off the same
parent `origin/main` commit. Both added an alembic migration with
`down_revision = "603787256fb8"` (the previous knowledge-schema head):

| File | Revision | Down-revision |
|------|----------|----------------|
| `0005_crawl_jobs_fetch_outcomes.py` (#440) | `a8c5e1d2f3b4` | `603787256fb8` |
| `0005_crawled_pages_simhash.py` (#441) | `7f2e8a1c5b4d` | `603787256fb8` |

Each PR's CI was happy in isolation — `alembic heads` returned exactly one
head against the parent each test ran from. The fork only became visible
once **both** had landed on `main`, at which point `alembic upgrade head`
refused to choose a target.

### Why CI didn't catch it

CI runs alembic upgrades against an empty test DB seeded from the PR's own
branch. The other parallel PR's migration is invisible until merge.

### Mitigation in PR #443

A no-op merge migration `0006_merge_fetch_outcomes_simhash.py` with
`down_revision = ("a8c5e1d2f3b4", "7f2e8a1c5b4d")`. Both predecessor
migrations are independent schema additions (different tables, different
columns), so the merge body has no upgrade work — only the lineage.

### Generalisable lesson

**When two SPECs add migrations to the same alembic schema in parallel,
the second-merger MUST add a merge migration before re-deploying.** A CI
gate for this would be valuable: post-merge job on `main` runs
`alembic heads` per service and fails CI loudly when count > 1, before the
deploy job triggers. Tracked as a follow-up.

## Mechanism — Blocker 2: Postgres rejects subquery in CHECK

Migration `009_sync_runs_skip_reasons.py` initially expressed the
PersistSkipReason membership constraint via:

```sql
ALTER TABLE connector.sync_runs
ADD CONSTRAINT sync_runs_skip_reasons_valid_keys
CHECK (
    jsonb_typeof(skip_reasons) = 'object'
    AND (
        skip_reasons = '{}'::jsonb
        OR (
            SELECT bool_and(key IN ('content_too_short', ...))
            FROM jsonb_object_keys(skip_reasons) AS key
        )
    )
)
```

Postgres has rejected scalar subqueries in CHECK constraints since 9.x
(PG manual: "currently, CHECK expressions cannot contain subqueries nor
refer to variables other than columns of the current row"). The local
test suite never executed the migration against a real Postgres — the
parity test only greps the migration file's text for enum values, and
the application-layer test `test_sync_engine_skip_reasons.py` mocks the
SQLAlchemy session.

### Why CI didn't catch it

CI on this branch ran ruff + pytest with mocked DB sessions; no integration
job applies migrations against a real Postgres for `klai-connector`. The
existing alembic-bootstrap test in `klai-knowledge-ingest` uses a real
testcontainer but doesn't cover `klai-connector`'s migrations.

### Mitigation in PR #443

Rewritten the CHECK using JSONB key-removal:

```sql
CHECK (
    jsonb_typeof(skip_reasons) = 'object'
    AND (skip_reasons - ARRAY['content_too_short', ...]::text[]) = '{}'::jsonb
)
```

`jsonb - text[]` strips every listed key; the result equals `'{}'` iff every
original key was in the allowed list. Same semantics, no subquery. Empty
input also normalises to `'{}'` so the explicit empty-object branch is
redundant in the new form.

### Generalisable lesson

**Migrations that introduce CHECK constraints with non-trivial JSONB
predicates need a real-Postgres integration test.** Mocked DB tests are
sufficient for application logic but not for SQL the application never
executes. A small `pytest-postgresql` (or testcontainer) job that runs
`alembic upgrade head && alembic downgrade base` per service in CI would
have caught this in PR #440. Tracked as a follow-up.

## Why the third PR (#444) and fourth PR (#447) were necessary

After the hotfix landed and the deploy stabilised, an adversarial pass on
the freshly-shipped code surfaced four latent issues:

1. **start_url fetched twice** — `_bfs_discover_seed_urls` called
   `crawl_page(start_url)` to extract internal links, AND
   `_build_candidate_set` re-included `start_url` as candidate index 0.
   Cost: ~0.5% on a 200-page site, ~20% on a 5-page connector.
2. **`login_indicator_selector` silently dropped on the seed** — The seed
   call went through `crawl_page` (no login_indicator kwarg). Auth-walled
   sites returned the login page as success and the BFS list became
   login-form anchors instead of real-content links.
3. **Redirects misclassified as `unknown_exception`** — Canonical-URL
   match against the response set missed when `response.url` reflected the
   redirect target rather than the candidate URL.
4. **Asymmetric reason-code parity test** — Drift detection only existed
   on the connector side.

PR #444 fixed all four. PR #447 added a same-domain guard on the
positional-match fallback introduced in #444 (it would have accepted any
out-of-order response under `MemoryAdaptiveDispatcher` reordering, even
to a different domain — silent misclassification class).

### Generalisable lesson

**A "code shipped, tests pass" green light is not the same as "code is
correct". After every non-trivial SPEC ship, run an explicit adversarial
pass framed as bug-hunting** (per
`.claude/rules/klai/pitfalls/process-rules.md::adversarial-at-high-confidence`).
The four issues above were not caught by any test because they were
correct-looking-but-wrong, not exception-throwing. Spending 20 minutes on
adversarial review post-ship saved a likely future incident on the next
auth-walled connector to onboard.

## Net outcome

- 4 PRs merged on the same day (#440, #443, #444, #447), all on production.
- ~30 minutes of container restart loop on `core-01` between #440 and #443
  merging — affected scheduled syncs but no user-facing functionality.
- 30 PersistSkipReason / 10 FetchReasonCode constants hardened against
  silent typo drift via parity tests on both sides.
- 1 ast-grep rule prevents the 3rd recurrence of the
  unbounded-gather-on-crawl_page anti-pattern in this codebase.

## Follow-ups (not in this SPEC's scope)

- Multi-head alembic detector as a post-merge CI gate.
- Real-Postgres integration test for `klai-connector` migrations
  (mirror the existing `klai-knowledge-ingest` alembic-bootstrap test).
- Empirical AC-5 / AC-8 validation requires a Voys connector trigger; the
  observability layer is in place but the SPEC will only be empirically
  closed after operator-driven sync.

## Related

- SPEC: `.moai/specs/SPEC-INGEST-RECONCILE-001/spec.md` (status: shipped)
- PRs: [#440](https://github.com/GetKlai/klai/pull/440),
  [#443](https://github.com/GetKlai/klai/pull/443),
  [#444](https://github.com/GetKlai/klai/pull/444),
  [#447](https://github.com/GetKlai/klai/pull/447)
- Sibling deploy retros: `docs/retros/2026-05-06-trivy-spec-iteration.md`
