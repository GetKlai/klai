# Process Rules

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
