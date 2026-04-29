# Process Rules

## worktree-for-long-running-changes (HIGH)
When you will make working-tree edits that span more than a single tool call
— especially test fixes, refactors, or anything that produces an in-flight
diff — start by creating a dedicated `git worktree add -b <branch> ../<path>`
and work there. Never edit the main repo directory when another session may
switch branches underneath you.

**Why:** `git checkout <other-branch>` aborts with an error if uncommitted
changes conflict, but silently *carries over* any clean-on-disk changes.
If an external tool, IDE auto-format, or parallel session then runs
`git checkout -- <file>` or `git restore`, uncommitted work disappears
without warning. The git reflog records the checkout but NOT the file-level
revert, so the changes look like they were never written. This happened
during the SPEC-KB-019 notion-tests fix: a Write succeeded, tests went green
locally, and then a branch switch in a parallel session restored the file to
its pre-edit state with no recoverable copy anywhere (not in stash, not in
any branch, not in any worktree).

**Prevention:**
1. `git worktree add -b chore/<name> ../<repo>-<name> main` BEFORE the first
   edit.
2. Work inside that worktree path exclusively.
3. Commit frequently — an uncommitted change in a worktree is still
   vulnerable to `git checkout --` or `git restore` from elsewhere.
4. Push the branch as soon as the first meaningful commit lands, so the
   work exists on origin even if the local worktree is wiped.

**When to skip:** single-file, single-tool-call edits that you stage and
commit immediately. For anything that takes more than ~5 tool calls to
complete, use a worktree.

## adapter-framework-bleed (HIGH)
When a service is declared "a pure X adapter framework" but you find
infrastructure concepts leaking into its public contract (S3 clients,
persistence primitives, MIME-validation helpers, content-fingerprint
fields) — stop before deleting them. Audit every consumer of those
concepts first. SPEC-CRAWLER-004 planned to delete `ImageRef` +
`DocumentRef.images` + `DocumentRef.content_fingerprint` from
klai-connector's BaseAdapter as "obviously crawl-only leakage". Only
`content_fingerprint` was actually crawl-only; github and notion
adapters were silently relying on `ImageRef` + `DocumentRef.images` to
drive sync_engine's S3 upload path. Deleting them would have broken
every live github/notion sync.

**Prevention:** Before any SPEC calls for deletion of a shared
datastructure, grep every caller across all services in the repo.
If non-trivially-adjacent callers exist, either broaden the SPEC
scope to move them or narrow the SPEC scope to leave the structure
in place. Never assume "originally added for X, therefore only used
by X". Shared contracts spread.

## data-before-code
Before fixing a bug: check the logs and follow the actual code path.
No guessing. No stacking patches. Trace what happens at runtime — logs,
DB state, API responses — not what you think should happen from memory
or stale files. For production issues, query VictoriaLogs via Grafana
MCP using `request_id:<uuid>` to trace the full chain across services.
If the data isn't visible, add debug logging and reproduce first. One
root cause confirmed by real data = one fix. If the first fix doesn't
work, go back to the data, not to another guess.
Trust your own working system over external GitHub issues — if something
works in 28 files, don't present an obscure issue as a showstopper.

## debug-holistic-view
When debugging, zoom out before zooming in. Don't fixate on the line
that errors — trace the full flow: where does the data come from? What
transforms it? What consumes it downstream? Search the codebase for
related patterns and callers. Search online for the error message or
library behavior. The bug is often not where the error appears.

## verify-changes-landed
After completing work, verify autonomously that changes actually landed:
1. `git diff --stat` — confirm the right files changed
2. Logs or health check — confirm the service runs with new code
3. Browser flow (Playwright MCP) — for UI changes, click through the
   actual user flow before reporting done
Detailed metrics without matching file changes are a hallucination
signal. Never report done based on "looks correct."

## report-confidence
End completion messages with `Confidence: [0-100] — [evidence summary]`.
Only observable evidence counts: test output, curl response, log output,
browser verification. "Code looks correct" and "should work" score zero.
The stop hook enforces this mechanically.

## adversarial-at-high-confidence
At confidence >= 80, ask yourself "what bugs can I find in what I just
did?" Frame as bug-hunting, not confirmation — "is this correct?"
triggers confirmation bias. The stop hook enforces this at >= 80.

## trust-user-feedback
When a user reports something is broken but your tests pass, stop and
reproduce the exact scenario they described with all their parameters.
The user's environment is the ground truth — your test setup may be
missing a key variable.

## minimal-changes
Make only the changes that were explicitly requested. Resist the urge
to "improve" surrounding code, refactor adjacent functions, or update
formatting in files you didn't need to touch. Unasked changes introduce
risk without authorization.

## communication-discipline
Read the user's entire message before taking any action. Summarize
your understanding before starting work — acting on the first sentence
means missing critical context. After asking a question, stop and wait.
Do not continue with tool calls — the answer may change everything.
Never instruct the user to "check in the browser" or "verify in the
UI" — verify autonomously with Playwright, or trust them to check.

## ask-before-retry
After two failed attempts at the same operation, stop and ask the
user for guidance. Summarize what you tried and what happened — a
third blind retry rarely succeeds where the first two failed.

## search-broadly-when-changing
When renaming or changing a default value, search the entire codebase
for all consumers — not just files in your plan. Check all case
variants: kebab-case, snake_case, camelCase, PascalCase, SCREAMING_SNAKE.
Defaults have unbounded blast radius: tests, configs, docs, scripts,
other services. Missing one variant breaks silently.

## follow-loaded-procedures
When a rules file is in your context that documents a procedure (SOPS
workflow, deploy steps, migration sequence), follow it step by step.
Do not improvise shell commands for the same operation. If the rules
say "decrypt → modify → encrypt-in-place → mv", do exactly that — not
a creative alternative with redirects or pipes.

## spec-discipline
Before implementing a SPEC, read the full document in `.moai/specs/`
or `.workflow/specs/`. Write down each constraint and how to verify it:
image tags, resource limits, excluded services. Then during work:
- If your architecture diverges from the SPEC — STOP. State the
  mismatch and ask before continuing. Never assume "close enough."
- If logs show a SPEC constraint violation (wrong service, wrong
  memory, forbidden process) — STOP. Report the violation before
  debugging downstream symptoms.
- If any constraint is unclear — ask before implementing.

## read-before-delegate
Before giving a subagent a "rewrite this file" task, Read the file
yourself first. If the user edited it, extract their text and pass it
verbatim in the prompt as content to preserve. Subagents have no
context about prior user edits — they will overwrite silently.

## extract-repeated-ui-patterns
When the same UI pattern is copy-pasted into a third file, extract a shared
component immediately — not after the fourth or fifth instance. Extracting
after the fact requires hunting down all existing instances and risks silent
divergence between copies. The threshold is three: two repetitions is a
coincidence, three is a pattern that warrants a component.

**Prevention:** At the start of the second copy, note the pattern. At the
third, stop and extract before continuing.

## pixel-perfect-alignment (HIGH)
For sub-pixel CSS alignment, Playwright measurements are unreliable:
headless Chromium runs at 1x CSS pixels while the user has a 2x HiDPI
display. A 1px offset invisible in a screenshot is clearly visible on
screen. `getBoundingClientRect()` measures bounding boxes, not glyph
positions. Theoretical corrections on top of measurements compound the error.

**Rule:**
1. Calculate the target offset in px first — do not start with a Tailwind class
2. Convert to Tailwind last: `mt-px`=1px, `mt-0.5`=2px, `mt-1`=4px, `mt-2`=8px
3. For sub-pixel work, ask the user to test directly in DevTools:
   "Select the element, add `style='margin-top:Xpx'`, try 1/2/3px — which works?"
   Their browser is the ground truth, not Playwright
4. Do not commit visual alignment until the user explicitly confirms it is correct

Never iterate through Tailwind spacing classes by feel. One measurement, one value.

## no-sycophancy
Never agree with the user just to be agreeable. If a proposed approach
has flaws, say so directly — even if the user seems committed to it.
If you don't know something, say "I don't know" instead of guessing
confidently. If the user's assumption is wrong, correct it before
acting on it. Prefer an uncomfortable truth over a comfortable lie.

Specific anti-patterns to avoid:
- "Great question!" or "That's a great idea!" before answering
- Claiming something works when you haven't verified it
- Softening bad news with excessive caveats or optimism
- Agreeing with contradictory statements across messages
- Generating plausible-sounding but unverified explanations

When you disagree: state the disagreement, give your reasoning, then
ask how to proceed. The user hired an expert, not a yes-man.

## worktree-agent-isolation
When a subagent runs inside a git worktree (`.claude/worktrees/<id>/`),
its file writes land in that worktree, not the main working tree. After the
agent completes, manually verify that changes exist in the main directory,
copy them if needed, and prune with `git worktree prune`. Skipping this
means reviewing and committing an empty diff.

## spec-work-in-a-worktree (HIGH)
Before making the first edit for any multi-file SPEC implementation, create a
dedicated git worktree branched from `main`:

```bash
git worktree add ../klai-<spec-short-name> -b feature/SPEC-<SPEC-ID> main
```

Then `cd` there and do all work in that worktree — implementation, tests,
docs, runbooks, everything. Commit inside the worktree. Open the PR from
that branch.

**Why:** When work spans 10+ files across multiple services (connector +
portal + frontend + docs), doing it on whatever feature branch happens to
be checked out is a recipe for the work getting swept into an unrelated
commit. SPEC-KB-MS-DOCS-001 suffered exactly this: 17 files of MS-365
connector work landed in commit `726d81a2` titled "fix(knowledge-ingest):
seed BFS start_url inside path_prefix subtree" because the implementation
assistant never created a dedicated worktree. Recovering clean history
after the fact requires rewriting pushed commits, which is rarely worth
the risk — the mess ships as-is.

**Prevention:**
- Every new SPEC implementation starts with `git worktree add` as literal
  step 0. No exceptions, no "I'll clean up later".
- If you catch yourself editing on the wrong branch, STOP — stash, create
  the worktree, replay the edits there.
- Rule-of-thumb trigger: if the SPEC touches more than 3 files, worktree.
  Below that, a normal feature branch is fine.

See `.claude/rules/moai/workflow/worktree-integration.md` for the decision
tree and `worktree add` flags.

## validator-env-parity (HIGH)
When a pydantic `@model_validator` is added that REJECTS an empty /
whitespace-only env var at app startup, verify the env var already exists
in production BEFORE landing the code change. Local tests pass because the
conftest sets a default; prod doesn't have a conftest, only SOPS. Shipping
the validator without the env var causes the service to refuse to start
and returns HTTP 502 until reverted.

**Why this happened:** SPEC-SEC-WEBHOOK-001 REQ-3 added
`_require_moneybird_webhook_token` to `klai-portal/backend/app/core/config.py`.
Tests passed (conftest sets the var), CI green, PR merged → auto-deploy to
core-01 → portal-api startup raised `ValidationError: Missing required:
MONEYBIRD_WEBHOOK_TOKEN` because the var was never in
`klai-infra/core-01/.env.sops`. Prod 502 for ~4 minutes until the merge was
reverted. The Moneybird finding (Cornelis #3) was the CAUSE: the token had
never been configured, so webhooks ran fail-open. The validator correctly
closed that bypass but required the env var to ship in the same deploy
window.

**Prevention:**

1. Before committing any `_require_<X>_secret` validator, run:
   ```bash
   grep -c "^ *<X>_SECRET\|^ *<X>_TOKEN" klai-infra/core-01/.env.sops
   grep -c "<X>_SECRET\|<X>_TOKEN" deploy/docker-compose.yml
   ```
   If either returns `0`, add the env var to SOPS first (and to the compose
   environment block if applicable), commit to klai-infra, verify decrypt
   works, THEN land the validator.

2. Deploy order is **env var first, validator second** — never the other
   way around. Even a same-day gap is acceptable; a same-deploy gap is
   catastrophic because validator-fails-at-startup triggers Docker restart
   loop and 502 cascade.

3. For audit-finding fixes that make a previously-optional config
   mandatory, list "env var pre-flight in klai-infra/core-01/.env.sops"
   as an explicit checkbox in the SPEC's Success Criteria AND in the PR
   body — not only in the forcing-function prose.

4. Conftest-sets-a-default is the classic trap that hides this regression.
   When writing the fail-closed test (`test_settings_startup_fails_without_X`),
   add a comment on the pydantic validator linking to this pitfall so
   reviewers stop and think about prod env parity.

See `klai-infra/core-01/.env.sops` for the canonical prod env inventory.

## env-file-migration-reverse-check (HIGH)
When replacing `env_file: .env` on a service with an explicit
`environment:` block, the obvious audit is forward: "what env vars does
the service's code read, and is each one declared in the new block?"
That audit is necessary but NOT sufficient. It misses the case where
a pydantic-settings field has an in-code default AND `/opt/klai/.env`
overrides it with a different value. Pre-migration the override was
inherited silently via `env_file: .env`; post-migration the field falls
back to the code default and behaviour changes.

SPEC-SEC-ENVFILE-SCOPE-001 shipped with three such regressions that
survived the forward audit:

- `VEXA_MEETING_API_URL` on portal-api: prod set `http://api-gateway:8000`,
  code default was `http://vexa-meeting-api:8080`. Would have routed
  meeting-bot traffic past the api-gateway layer.
- `GRAPHITI_LLM_MODEL` on retrieval-api: prod set `klai-pipeline`,
  code default was `klai-fast`. Different quality/cost on graph
  extraction.
- `VEXA_ADMIN_TOKEN` on portal-api: prod set a real token, code default
  was `""`. No current runtime reader, but future callers would have
  silently gotten an empty token.

**Prevention:** For every service migrated off `env_file: .env`, run
the reverse check explicitly:

```bash
# For each pydantic field with a default, compare /opt/klai/.env to the container env
for var in $FIELD_NAMES; do
  VAL_ENV=$(grep -E "^${var}=" /opt/klai/.env | cut -d= -f2-)
  VAL_CTR=$(docker exec klai-core-<svc>-1 printenv $var 2>/dev/null)
  [ -n "$VAL_ENV" ] && [ "$VAL_ENV" != "$VAL_CTR" ] && \
    echo "DIVERGENCE: $var — .env='$VAL_ENV' vs container='$VAL_CTR'"
done
```

Run both BEFORE the migration (to build the override inventory) and
AFTER the deploy (to confirm zero divergence). Any DIVERGENCE line is
a behaviour regression. Fix by declaring the var in the explicit block
with `${VAR:-<code-default>}` interpolation so the compose file is also
self-documenting about the expected prod value if SOPS drift occurs.

The same-shape generalisation: **trust the container env, not the
config source.** Any "silent fallback to a code default" in a migration
off a blanket-inherit pattern is a latent bug.

## scribe-deploy-no-alembic (HIGH)
The `scribe-api.yml` GitHub Action does `docker compose up -d` only —
it does NOT run `alembic upgrade head`. The Dockerfile CMD is
`uvicorn`, no migrate step in the entrypoint either. New migrations
land in the image but are not applied to the DB on deploy.

**What it looks like in production**: app starts, any code path that
references the new column raises `asyncpg.exceptions.UndefinedColumnError`.
If wrapped in try/except (e.g. lifespan startup hooks), it logs a warning
and the rest of the app keeps working — you only notice when the new
feature silently does nothing. If NOT wrapped, the request fails with
a 500.

**SPEC-SEC-HYGIENE-001 scribe-slice (2026-04-27)** got bitten by this:
migration `0007_c5f9e3a4` (adds `error_reason`) shipped in the image but
not applied. Reaper-on-startup logged `scribe_startup_reaper_failed` with
the `UndefinedColumnError`. The lifespan try/except caught it, app stayed
up, but the new feature was dormant until manual `docker exec
klai-core-scribe-api-1 alembic upgrade head` + container restart.

**Prevention**:
1. Any scribe SPEC that adds a migration MUST include in its acceptance
   criteria: "after CI deploy completes, run `docker exec
   klai-core-scribe-api-1 alembic upgrade head` and restart the
   container" — and put that in the PR body so the merger doesn't forget.
2. Better long-term fix: add a step to `scribe-api.yml` after `docker
   compose up -d`:
   ```yaml
   - name: Apply alembic migrations
     uses: appleboy/ssh-action@v1
     with:
       script: |
         docker exec klai-core-scribe-api-1 alembic upgrade head
   ```
   Or move it into the Dockerfile CMD (`alembic upgrade head && exec uvicorn ...`).
3. **General rule for all klai services with their own deploy workflow**:
   grep the `.github/workflows/<service>.yml` for `alembic` BEFORE landing
   any migration. If absent, use option 1 (manual + PR-body reminder) as
   a stopgap and file a follow-up SPEC for option 2.

**Audit (2026-04-27)** — verified by greping `Dockerfile` ENTRYPOINT/CMD
across services:

| Service | Auto-migrates on container start? |
|---|---|
| portal-api | YES — `entrypoint.sh` runs `alembic upgrade head` then exec's uvicorn |
| scribe-api | NO — `CMD uvicorn …` only |
| klai-connector | NO — `CMD uvicorn …` only |
| klai-mailer | NO — `CMD uvicorn …` only |
| klai-knowledge-mcp | NO — `CMD python main.py` only |
| klai-knowledge-ingest | NO — `CMD uvicorn …` only |
| klai-retrieval-api | NO — `CMD uvicorn …` only |

Every service except portal-api needs the manual-migrate step or an
entrypoint port. The portal-api `entrypoint.sh` (introduced by
SPEC-CHAT-TEMPLATES-CLEANUP-001) is the canonical pattern to copy.

## ruff-format-and-ruff-check-are-different (MED)
`uv run ruff check` and `uv run ruff format --check` enforce different
things. Lint (`check`) catches code-correctness issues (unused imports,
undefined names). Format (`format --check`) catches whitespace, line
wrapping, quote consistency. CI's portal-api `quality` job runs BOTH;
local `ruff check` clean does NOT guarantee CI pass.

**Prevention:** Before pushing, run BOTH commands:

```bash
cd klai-portal/backend
uv run ruff check . && uv run ruff format --check .
```

Or run the quality job's exact sequence: see
`.github/workflows/portal-api.yml` lines 43-47. SPEC-SEC-CORS-001 round
2 push hit this — `ruff check` was clean locally but `ruff format --check`
flagged 4 files in CI, requiring a follow-up commit. Now mechanical.

## gh-cleanup-cross-worktree (LOW)
`gh pr merge --delete-branch` runs a local-side cleanup that includes
`git checkout main && git branch -D <feature>`. If `main` is checked out
in another git worktree (common in klai with multiple parallel SPECs),
this fails with `fatal: 'main' is already used by worktree at '<path>'`
AFTER the remote merge has succeeded. The PR is merged, the local-side
cleanup is incomplete.

**Prevention:** Trust the GitHub-side merge result; finish local cleanup
manually:

```bash
gh pr view <number> --json state,mergeCommit  # confirm MERGED
git push origin --delete <feature-branch>      # remote branch
git worktree remove <path>                     # local worktree
```

Do NOT panic and re-attempt the merge. The remote merge is idempotent
once committed; trying again will say "already merged".

## sops-roundtrip-line-count-check (HIGH)
A SOPS edit done via the documented `decrypt → modify → encrypt` workflow
can silently DROP entries from the encrypted dotenv file. Specifically:

- `sops --decrypt --input-type dotenv --output-type dotenv` strips comments
  and blank lines that have no `KEY=VALUE` shape.
- Some KEY=VALUE lines with edge-case formatting (multi-line values, trailing
  whitespace inside encrypted content, age-version transitions) decrypt to
  a different number of lines than the source.
- After `--encrypt`, the resulting file has fewer entries than the original.

The deploy-side sync workflow on klai-infra catches *some* of this via its
"keys-removed" guard, but it only fires AFTER the file is pushed and CI
runs — by which time the local SOPS file already has the regression and
unrelated commits would compound the loss.

Two real incidents in the audit-response sprint:

1. The **first MONEYBIRD_WEBHOOK_TOKEN add** (klai-infra `6d73cb98`) —
   author appended one line, but decrypt-encrypt roundtrip dropped
   `KUMA_TOKEN_RESEARCH_API` and `RESEARCH_API_ZITADEL_AUDIENCE`.
   GitHub sync workflow refused to deploy with `keys would be REMOVED`
   error. Force-push of a fresh roundtrip fixed it.
2. **#170 ENVFILE-SCOPE migration** — three vars dropped on a SOPS edit
   that was supposed to be pure additive.

**Prevention:**

1. **Always do a roundtrip line-count check on the server** as part of
   the SOPS edit workflow. Modify the standard sequence:

   ```bash
   ssh core-01 "
     cd /tmp/klai-sops &&
     SOPS_AGE_KEY_FILE=~/.config/sops/age/keys.txt sops --decrypt --input-type dotenv --output-type dotenv core-01/.env.sops > core-01/.new.env
     OLD=\$(wc -l < core-01/.new.env)
     # ... your sed/append modification here ...
     EXPECTED_DELTA=1   # +1 if adding a single var, 0 if rotating
     SOPS_AGE_KEY_FILE=~/.config/sops/age/keys.txt sops --encrypt --input-type dotenv --output-type dotenv core-01/.new.env > core-01/.env.sops
     ROUNDTRIP=\$(SOPS_AGE_KEY_FILE=~/.config/sops/age/keys.txt sops --decrypt --input-type dotenv --output-type dotenv core-01/.env.sops | wc -l)
     # Compare against /opt/klai/.env (the live, authoritative file) PLUS expected delta:
     LIVE=\$(wc -l < /opt/klai/.env)
     EXPECTED=\$((LIVE + EXPECTED_DELTA))
     if [ \"\$ROUNDTRIP\" -ne \"\$EXPECTED\" ]; then
       echo \"REFUSING — roundtrip=\$ROUNDTRIP expected=\$EXPECTED (live=\$LIVE delta=\$EXPECTED_DELTA)\"
       exit 1
     fi
   "
   ```

2. When a roundtrip diverges from expectation, **rebuild the SOPS file
   from `/opt/klai/.env`** (the live authoritative source) plus your
   additions, instead of trying to patch the broken decrypt output.
   This is what fixed both incidents above.

3. Treat the klai-infra GitHub sync workflow's `keys-would-be-REMOVED`
   error as a HARD STOP, never as a warning to bypass. Force-pushing
   with `--allow-removal` was considered and rejected for incident #1
   precisely because the operator could not enumerate which 161-vs-162
   line was the regression — a known-good rebuild is always cheaper.

See `.claude/rules/klai/infra/sops-env.md` for the full SOPS workflow.

## astgrep-gitignore-shadowed-rules (HIGH)
ast-grep silently respects `.gitignore` when discovering rule files in
`ruleDirs`. The repo `.gitignore` carries `*-secret.*`, `*_secret.*`,
`secret-*.*` and a handful of similar secret-file-hygiene patterns. A
rule file named `no-string-compare-on-secret.yml` (matching `*-secret.*`)
is silently dropped: `effectiveRuleCount` stays unchanged in
`sg scan --inspect summary`, no warning hits stderr, and no parse error
is reported. The same rule loads fine when invoked via
`sg scan --rule path/to/file.yml`, which makes the bug very confusing
to diagnose.

**Symptom.** Your new rule passes a manual `sg scan --rule rules/foo.yml`
test, but the per-service workflow doesn't fire it. `effectiveRuleCount`
in `--inspect summary` reflects the existing rule count only.
SPEC-SEC-INTERNAL-001 (2026-04-29) hit this with rule files named
`no-string-{compare,neq}-on-secret.yml` and renamed them to
`no-secret-{eq,neq}-compare.yml` to escape the gitignore filter.

**Prevention.**
- Before relying on a new rule under `rules/`, run
  `git check-ignore -v rules/<file>.yml`. If that command prints any
  matching pattern, rename the file.
- Prefer prefixes like `no-secret-*-compare.yml` that don't end in
  `secret.<ext>` / `_secret.<ext>` / `secret-*.<ext>`.
- Verify rule loading with
  `uv tool run --from ast-grep-cli sg scan -c sgconfig.yml --inspect entity .`
  and grep for your rule's `id:` in the output.

## uv-pip-install-skips-uv-sources (HIGH)
`uv pip install --system -r pyproject.toml` (uv's pip-compatibility mode)
does NOT read `[tool.uv.sources]`. Path-deps declared as
`klai-log-utils = { path = "../../klai-libs/log-utils" }` get resolved
as PyPI lookups and fail with
`error: Failed to parse entry: 'klai-log-utils'` during the Docker
build. This is a silent gotcha because `uv sync` (which IS uv-native)
DOES honour `[tool.uv.sources]`, so the local dev experience works
fine and only Docker breaks.

**Symptom.** `docker build` fails on the install step with the parse
error above. SPEC-SEC-INTERNAL-001 (2026-04-29) hit this when scribe-api
was the only service still on the old `pip install` Dockerfile pattern;
adding the shared `klai-log-utils` path-dep silently broke its build.

**Prevention.**
- Switch the Dockerfile to a repo-root build context plus
  `uv sync --frozen --no-dev --no-install-project` and `COPY` lines
  for every `klai-libs/*` path-dep the service consumes. Mirror the
  pattern already used by knowledge-mcp / connector / portal-api.
- The workflow's `docker/build-push-action` step needs `context: .`
  and an explicit `file: <service>/Dockerfile` once the context is
  broadened.
- After rewriting, smoke-test the Dockerfile locally
  (`docker build -f <service>/Dockerfile .`) BEFORE pushing — the
  CI feedback loop is 3-5 min per attempt.

## parallel-spec-on-overlapping-log-sites (MED)
When two SPECs land on the same call sites in the same file, the rebase
or merge produces large, repetitive conflicts. SPEC-SEC-INTERNAL-001
REQ-4 was a 22-site sweep on `klai-portal/backend/app/api/auth.py` that
rewrote `logger.exception("...", exc.response.status_code, exc.response.text)`
to `... sanitize_response_body(exc)`. SPEC-SEC-AUTH-COVERAGE-001 (#195)
landed concurrently and replaced the SAME 22 `logger.exception` calls
with structured `_slog.exception(...)` + `_emit_auth_event(...)`
events that don't log the body at all. Result: 20 conflict blocks on
merge, all of the shape "my sanitize wrapper vs main's structured event".

**Resolution rule.** Take the more-thorough version on each conflict —
in this case main's structured events, because they already achieve
REQ-4's goal (no body in the log) AND add observability fields the
sanitizer does not. The other SPEC's contribution survives in the
non-conflict zones (a single non-log substring-check site at line 399
plus the import).

**Prevention.**
- Before opening a wide log-site sweep, grep `git log --all --oneline`
  for adjacent SPECs touching the same file, AND check `gh pr list
  --search "auth.py"` for in-flight branches.
- If two SPECs MUST sweep the same file in the same week, coordinate
  scope: one PR carries the structural refactor, the other adapts on
  top instead of replaying the same edits.
- Prefer rebase + per-commit conflict resolution for the secondary
  branch when the primary is already merged; or use a merge commit if
  the secondary has multiple commits worth preserving (as
  SPEC-SEC-INTERNAL-001 did to keep its 7 batch-commits readable).
