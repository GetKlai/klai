# Process Rules

## postgres-no-return-type-overload (HIGH)
PostgreSQL does NOT support function overloading by return type alone.
Two zero-argument functions with the same name and different return
types cannot coexist in the same schema. `CREATE OR REPLACE FUNCTION`
on an existing function with a different return type fails with:

```
ERROR:  cannot change return type of existing function
HINT:  Use DROP FUNCTION _rls_current_org_id() first.
```

Reference: SPEC-TI-002 (PR #375, 2026-05-06). The post-deploy SQL
declared `_rls_current_org_id() RETURNS text` next to portal-api's
existing `_rls_current_org_id() RETURNS integer`. The SPEC author
believed Postgres allowed return-type overloading; it does not. The
migration's ENABLE+FORCE RLS step succeeded but the policy creation
aborted, leaving connector.connectors and connector.sync_runs with
default-deny RLS and no policies — a 100% read/write block on every
connector operation.

Recovered by hot-renaming to `_rls_current_org_text()` in prod via
post-deploy SQL, then back-filling the source tree
(fix/SPEC-TI-002-rls-function-name-collision).

**Prevention:**

1. **Schema-qualify per-service RLS helpers.** Each service that needs a
   different-typed `_rls_current_org_id` should put it in its own
   schema (e.g. `connector._rls_current_org_id() RETURNS text`,
   `knowledge._rls_current_org_id() RETURNS text`,
   `public._rls_current_org_id() RETURNS integer`). Schema-qualified
   functions don't collide. SPEC-TI-003 already does this correctly
   with `knowledge._rls_current_org_id()`.

2. **Or use a clearly-different name.** `_rls_current_org_text()` /
   `_rls_current_org_int()` / `_rls_current_<service>_org()` make the
   type explicit at the call site and remove ambiguity in policy
   definitions.

3. **Never rely on "return type overloading".** It is not a Postgres
   feature regardless of how the docs read at first glance. The actual
   overloading dimension is parameter list (zero-arg vs one-arg vs
   two-arg, OR same arity but different parameter types). Return type
   alone is never enough.

4. **Smoke-test the post-deploy SQL on a non-prod DB before merging
   the SPEC.** Apply against a snapshot or stage DB. Any error here is
   a deployment-blocker — production policies must exist BEFORE the
   alembic migration ENABLE+FORCEs RLS, or the table becomes default-
   deny with no recourse from the application layer.

## rls-policy-shape-must-match-lifespan-assert (HIGH)
The portal-api lifespan substring-matches the literal text `"IS NULL"` in
the `portal_users.tenant_isolation` USING clause and raises if absent.
RLS policy DDL must (a) keep that substring AND (b) match the table's
auth category — Cat-A AUTH-SEED tables (`portal_users`,
`portal_connectors`) are queried BEFORE tenant context is set, so they
MUST use the inline NULLIF pattern; calling the `_rls_current_org_id()`
helper raises ERRCODE 42501 and 500s every authenticated request.
Cat-D strict tenant tables MUST use the helper (fail-loud is correct).

Reference: SPEC-TI-005 (PR #377, 2026-05-06).

```sql
-- Cat-A (portal_users, portal_connectors) — inline NULLIF, no helper call:
USING (
    org_id = NULLIF(current_setting('app.current_org_id', true), '')::integer
    OR NULLIF(current_setting('app.current_org_id', true), '') IS NULL
)
WITH CHECK (org_id = NULLIF(current_setting('app.current_org_id', true), '')::integer)
```

**Prevention:**
- Cat-A tables: inline NULLIF only. Cat-D tables: helper. Never swap.
- `curl /api/me → 401` does NOT validate Cat-A reads (401 short-circuits
  before the DB query). Hit an authenticated endpoint, or run
  `SET ROLE portal_api; RESET app.current_org_id; SELECT COUNT(*) FROM portal_users;`
  and confirm no 42501.
- Any USING-shape change MUST update `assert_portal_users_rls_ready()`
  in the same PR, or extend the rls-policy-smoke-test CI job to import
  `app.main` and invoke the lifespan assertion.

## asyncpg-pool-guc-not-shared (HIGH)
Postgres GUCs (`current_setting('app.foo', true)`) are **connection-local**,
not pool-local. Setting the GUC on connection A does NOT propagate to
connection B that a later `pool.acquire()` returns. Pinning one
connection in an outer `async with tenant_scoped_connection(...)` and
letting the body call `pool.acquire()` on its own gives the body a
DIFFERENT connection without the GUC — every query against an
RLS-protected table raises ERRCODE 42501.

Reference: SPEC-TI-003 (PR #376, 2026-05-06).

```python
# WRONG — the SPEC author wrote this literally:
async with tenant_scoped_connection(org_id) as _conn:
    del _conn  # connection held open to keep GUC set; pg_store uses pool
    await run_crawl_job(...)  # → pg_store grabs a DIFFERENT pool conn → 42501
```

Fix: pass the `conn` from the helper through every function that issues
SQL, or have the leaf function open its own `tenant_scoped_connection`.

**Prevention:**
- `tenant_scoped_connection` helpers MUST yield a `Connection` callers
  PASS through — never pin-and-pray.
- Code review for any RLS helper: grep `pool.acquire()` / `get_pool()`
  in the same service. Every site must accept a `conn` parameter, be
  inside the helper, or never touch RLS-protected tables.
- contextvars and per-request middleware do NOT help — they carry
  Python state, not Postgres connection state.
- Pre-merge: run a smoke-test against a clean Postgres with the
  FORCE-RLS post-deploy SQL applied. Any wiring gap surfaces as 42501.

## scale-the-answer-to-the-problem (HIGH)
When a user asks "what is industry standard?" do not autopilot to the
most architecturally-elegant answer in the search results. Anchor on
**team scale + actual problem** first. The same question has different
right answers depending on whether the user is a small team that wants
one developer's MCP to query logs (industry standard: SSH tunnel +
`launchd` auto-start, or self-hosted WireGuard) versus a SaaS
observability platform with 50+ engineers and audit-compliance
requirements (industry standard: identity-aware proxy, vmauth-OIDC,
Pomerium, etc.). Both frames are real industry standards — for
*different problems*.

The 2026-05-05 incident: user asked for the "mooiste self-hosted
oplossing" for a `mcp-victorialogs` tunnel that kept dying after Claude
Code session end. The 5-minute fix was a `launchd` plist that
auto-restarts `scripts/victorialogs-tunnel.sh`. Instead, the assistant
proposed and partially landed a SPEC-OBS-002 that involved deploying
vmauth on core-01, provisioning a Zitadel app with auth-code+PKCE,
adding a Zitadel Action to inject the `vm_access` claim, rewriting
.mcp.json, building a `klai-login` script, and updating two docs files
plus the deploy-compose workflow. ~9 commits, 4 hours, dual worktrees,
multiple force-pushes to main, and ended with the user blocked on a
Zitadel admin UI click that he can't easily make because Klai's auth
goes through its own login flow.

The user's earlier signals were ignored:
- "ik ben gewend om dat met iets van MCP te doen of dat je gewoon via
  SSH bij de logs kan" — said a simple-as-possible solution.
- "Het lijkt me handig om de spec te updaten" — said this is heavier
  than necessary.
- "Hoe KOM je erbij dat dit industry standard is?" — challenged the
  premise of the entire architecture.

**Prevention (mechanical, not vibes-based):**

1. Before proposing a SPEC for an infra change, count the people
   affected today vs. in 12 months. If today is 1–5 people and
   in-12-months is still 1–5 people, the answer is likely a config
   tweak or a small script, not a SPEC. SPECs are for things 5+ people
   will live with for 2+ years.
2. When the user asks "what is industry standard," ask back: "for
   what scale and what compliance pressure?" before answering. The
   question is incomplete; do not silently fill in the most ambitious
   interpretation.
3. If the user describes a problem that has an obvious 5-minute fix
   *also* present in the search results, lead with that fix. Mention
   the larger architectural answer as "if you ever scale to 20+
   engineers, here's where you'd go" — not as the recommendation.
4. The pitfall here is not technical: vmauth + Zitadel + auth-code+PKCE
   genuinely works. The pitfall is **proportionality** — applying a
   pattern that fits a Fortune-500 SOC to a small team that just wants
   their MCP to keep working across reboots.

## retrieve-caller-service-header-mismatch (CRIT)
When a SPEC adds a new MANDATORY request header (or any other contract
change) on a receiver, the SAME PR MUST update every active caller, even
the ones in other repositories or directories the SPEC author does not
normally edit. SPEC-SEC-IDENTITY-ASSERT-001 Phase D landed on 2026-04-28
and made `X-Caller-Service` required on `retrieval-api /retrieve`. The
SPEC PR updated the receiver and its tests; it did NOT touch the four
in-repo callers:

| Caller | File | Symptom |
|---|---|---|
| LiteLLM hook | `deploy/litellm/klai_knowledge.py` | Every chat ran with no KB context for 7 days |
| Partner API | `klai-portal/backend/app/services/partner_chat.py` | Partner /chat/completions returned no KB chunks |
| Gap re-scorer | `klai-portal/backend/app/services/gap_rescorer.py` | Background job silently returned 400 on every call |
| Focus narrow retrieval | `klai-focus/research-api/app/services/retrieval_client.py` | Notebook narrow returned [] for 7 days |

**Why nobody noticed:** every caller wrapped the call in a fail-open
`except Exception → log.warning → return empty/no context`. The chat
still produced a coherent answer (just from general knowledge, not the
KB). No alerts on retrieval-failure rate. Discovered only when a user
asked "is the KB even being queried?" and we tailed the logs.

**Prevention (mechanical, several layers):**

1. **Allowlist tests.** Every consumer of
   `klai_identity_assert.KNOWN_CALLER_SERVICES` MUST have a unit test
   that locks in `X-Caller-Service: <its-name>` on the outbound
   `/retrieve` call. The test mocks the httpx client and asserts the
   header is set. Without it, the next refactor that drops the header
   passes CI silently. We added these tests for all 4 callers in the
   2026-05-05 hotfix — keep them.

2. **Receiver-side contract test.** `klai-retrieval-api` should ship a
   smoke test that POSTs to `/retrieve` from a real httpx client using
   the EXACT header set every caller sends. A unit test inside the
   receiver covers the receiver only — it does not validate that any
   caller still complies.

3. **Cross-repo audit on contract changes.** When a SPEC adds a new
   header / required field on an internal endpoint, grep the entire
   monorepo for callers BEFORE merging:
   ```bash
   grep -rn '/retrieve\|RETRIEVE_URL\|knowledge_retrieve_url' \
       --include='*.py' --include='*.ts' .
   ```
   For every match outside the SPEC's own service, either patch it in
   the same PR or open a tracking issue and ship the receiver-side
   change behind a per-caller header allowlist for the soak window.

4. **Fail-loud on retrieval failure.** The hook now bumps `warning →
   error` on any /retrieve failure AND injects a user-visible
   "[Klai Kennisbank — TIJDELIJK NIET BEREIKBAAR]" notice into the
   system prompt so the user sees an explicit warning. Same class as
   `fail-open-auth` in this file: silent-degrade on a feature the user
   thinks they have is worse than a loud error.

5. **Grafana alert on failure rate.** Pending: alert on
   `service:litellm AND message:"retrieval"` ERROR-rate > 0 for 5 min.
   This regression was visible in logs from minute one — only the lack
   of an alert kept it hidden for a week.

## verify-image-pullable-before-pin (HIGH)
When pinning an external image tag in any compose file (`vexaai/*`,
`ghcr.io/*`, etc.), verify the tag is actually pullable BEFORE
committing. PR #269 (2026-05-03) bumped
`vexaai/transcription-service` to `:0.10.6` based on the v0.10.6
release notes — but that specific image was never published to
Docker Hub (upstream lists 9 public images; transcription-service is
locally-built only). The bug landed in main; only the fact that
gpu-01 has no CI sync prevented a runtime regression.

**Why this slips through:** release notes are written for a global
audience. They list "all images" from the maintainer's perspective,
which can mean "all images in our private CI" not "all images on
Docker Hub". Reading carefully and trusting the prose is not enough.

**Prevention (mechanical):** `deploy/check-image-pullable.sh` runs
in pre-commit and the deploy-compose CI workflow. For every
`vexaai/*` (and other external) ref in compose files, it does
`docker manifest inspect <ref>`. Failures are accepted ONLY if the
tag matches a locally-built convention
(`<semver>-local-YYMMDD-HHMM` or legacy `<semver>-YYMMDD-HHMM`).
Anything else is rejected at commit time.

**Prevention (human):** before adding a NEW external image tag to
a compose file, run `docker manifest inspect <ref>` locally. If it
404s and the image is locally built, name the tag with the
`-local-` infix so the script's whitelist accepts it. Do not invent
tag names; mirror what your build pipeline emits.

## docker-compose-restart-vs-recreate (CRIT)

`docker compose restart <svc>` keeps the existing container config —
it ignores any drift between the running container and the current
`docker-compose.yml` / `.env`. New volume mounts, new env-vars, new
image tags: all silently skipped. Use `docker compose up -d <svc>`
(or the canonical `/opt/klai/scripts/compose-up.sh <svc>` wrapper from
SPEC-INFRA-CONTAINER-HYGIENE-001 REQ-3) so Compose recreates the
container when its definition has changed.

**Reference incident:** SPEC-RAG-MULTILINGUAL-CHAT-001 Phase 4
(2026-05-07). PR #472 added a new `klai_chat_prompts.py` bind-mount
for the LiteLLM container. The `litellm-hook-deploy.yml` workflow
ran `docker compose restart litellm`, which silently ignored the new
mount. The container kept its previous mount config, the
`from klai_chat_prompts import …` line failed at startup with
`ImportError`, and the proxy entered a crashloop. All path-A traffic
(LibreChat) hung in "Stop generating" state for ~15 min until a
hotfix inlined the constant. Structural fix landed in PR #475:
workflow switched to `compose-up.sh`. Full retro:
`docs/retros/2026-05-07-litellm-restart-vs-recreate.md`.

**Where it bites:**
- New volume mounts (the 2026-05-07 incident)
- New env-vars (the comment in `deploy-compose.yml` already explains
  this for grafana — Grafana env-var changes need `up -d`, not
  `restart`)
- Image tag changes (would silently keep running the old image)
- Compose-level network / DNS / depends_on changes

**Prevention (mechanical):** every klai service-deploy workflow MUST
use `/opt/klai/scripts/compose-up.sh <svc>` for the recreate step,
not `docker compose restart`. Canary check that any reviewer / CI can
run:

```bash
# Find service-deploys that do compose ops without the canonical wrapper
for f in .github/workflows/*.yml; do
  grep -l 'docker compose' "$f" | grep -L 'compose-up.sh' || true
done
```

This MUST return zero results (other than `deploy-compose.yml` itself,
which is the workflow that INSTALLS `compose-up.sh`, and read-only
diagnostic uses such as `docker compose config` / `docker compose
exec`).

**Prevention (human):** before merging a PR that adds a new bind-mount
or env-var to `deploy/docker-compose.yml`, sanity-check the affected
service's deploy workflow uses `compose-up.sh` (or `up -d`). If it
uses `restart`, fix that FIRST in a separate PR, THEN ship the
mount/env-var change. The two changes can land together but only if
the PR's workflow change is itself triggered by the same commit (i.e.
the workflow file is in `paths:` of its own trigger).

**See also:** SPEC-INFRA-CONTAINER-HYGIENE-001 REQ-3 mandates
`compose-up.sh` for klasse-A (compose-managed) services. Phase D of
SPEC-RAG-MULTILINGUAL-CHAT-001 (TBD) will eliminate the underlying
cause for this service by replacing both vendored single-files with
a custom litellm Dockerfile that `pip install`s the deps — once that
ships, there are no bind-mounts to forget.

## bind-mount-without-sync-workflow (HIGH)

Sibling-class to `docker-compose-restart-vs-recreate`. When a relative
bind-mount `./<svc>/<file>:/etc/...` is added to
`deploy/docker-compose.yml`, the host source resolves to
`/opt/klai/<svc>/<file>` — but **no workflow syncs the file from the
repo to that path** unless one is explicitly added. Image rebuilds
recreate the container, but the bind-mount happily reads whatever
was scp'd manually long ago. Drift can persist for months.

**Reference incidents:**
- SPEC-INFRA-CADDY-CONFIG-DEPLOY-001 (2026-05-07, `a2a090a5`):
  `deploy/caddy/Caddyfile` had no sync workflow. Fixed via SPEC.
- SPEC-INFRA-CONFIG-SYNC-001 (2026-05-07, `c49f0914`): post-Caddy
  audit found the same gap for `alloy/config.alloy`,
  `searxng/settings.yml`, and `vexa/profiles.yaml`. The first
  post-merge run actually detected drift on searxng and runtime-api
  and recreated both — proving the drift was real, not theoretical.

**Prevention (canonical pattern, codified in `infra/deploy.md`):**
The 3-step checklist when adding a new relative bind-mount to
`deploy/docker-compose.yml`:

1. Add `'deploy/<svc>/<file>'` to `deploy-compose.yml`'s `paths:`
   trigger
2. Add the same path to the `git sparse-checkout set` invocation
3. Add a `sync_and_recreate <compose-service> deploy/<svc>/<file>
   /opt/klai/<svc>/<file>` call in the workflow script

Use the helper for single-file mounts. For directory mounts, use a
directory-rsync block in the style of grafana provisioning sync.

**See also:** `infra/deploy.md` § "Bind-mount config sync — required
pattern" for the full Class A/A-dir/B/C inventory of current
bind-mounts and the helper's behavioural contract.

## bind-mount-content-vs-python-module-cache (HIGH)

Sibling-class to `bind-mount-without-sync-workflow` and
`docker-compose-restart-vs-recreate`. When a service imports its
business logic from bind-mounted Python files (litellm is the only
current example: `klai_knowledge.py`, `klai_chat_prompts.py`,
`klai_retrieval_telemetry.py`, `klai_service_auth.py`,
`custom_router.py` all live in `deploy/litellm/` and mount onto
`/app/`), a deploy that ONLY changes file content — no compose
definition diff — silently runs the old code in production.

The chain:

1. `litellm-hook-deploy.yml` rsyncs the new `.py` files to
   `/opt/klai/litellm/` on core-01. The bind-mount serves them live
   on the container's filesystem immediately.
2. `compose-up.sh litellm` (the canonical wrapper) runs
   `docker compose up -d --remove-orphans litellm`. Compose only
   recreates when the compose DEFINITION diffs (volume list, env-vars,
   image tag). File content is invisible to compose. Result: no-op.
3. The container keeps running the same uvicorn process from the
   previous boot. Python's `sys.modules` already cached
   `klai_knowledge` (and friends) from that boot — re-imports return
   the cached module object regardless of what's now on disk.
4. CI workflow turns green, repo + host filesystem agree, but the
   running container ignores the change.

**Reference incidents:** PR #497 (no-KB GENERAL prompt) deployed
green; live container kept the old `__all__` and old prompt body.
Required manual `docker compose up -d --force-recreate litellm` on
core-01. PR #501 (telemetry mount + anti-hallucination prompt)
landed the same problem twice in two days.

**Prevention (mechanical, codified in
`deploy/scripts/compose-up.sh` + the litellm workflow):**

`compose-up.sh` accepts `--force-recreate` as a flag and passes it
through to `docker compose up -d`. Use it from any service-deploy
workflow whose service imports bind-mounted Python (or any other
language whose runtime caches modules — Node `require.cache`, JVM
class loaders, etc.):

```yaml
- name: Recreate LiteLLM container
  run: ssh core-01 "/opt/klai/scripts/compose-up.sh --force-recreate litellm"
```

For services whose code lives entirely in the image (no bind-mount
of source files), `--force-recreate` is unnecessary and the bare
`compose-up.sh <service>` form is correct — image-tag changes already
trigger a recreate.

**Audit (2026-05-07):** the only klai service today that imports
bind-mounted Python at module load is litellm. If a future service
adopts the same pattern, its deploy workflow MUST use
`--force-recreate` from day one. Adding the flag retroactively after
a "container runs old code" incident is the standard ramp pattern,
but the cleaner default is to set it at workflow-create time.

**Why not `docker restart <ctr>`:** equivalent effect (drops
process, drops module cache) and lighter, BUT `restart` ignores
compose definition diffs landed in the same deploy window
(`docker-compose-restart-vs-recreate` pitfall). `--force-recreate`
catches both bind-mount content AND compose definition changes in
one pass — strictly safer.

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

## multi-layer-gate-audit-all-sides (HIGH)
When a SPEC introduces a new gating layer on top of an existing one
(e.g. tenant-level toggle PLUS per-user/group entitlement, where access
requires BOTH), the implementation must touch THREE sides at once:
**Unlock side** (the new flag write), **Assign side** (every endpoint
that creates the per-user record AND every plan-ceiling / allowlist
check that decides which products are assignable), and **Read side**
(every consumer that surfaces effective entitlements — `get_effective_*`,
`/api/me`, internal JWT enrichment, sidebar feeds). Missing any one
side leaves a cosmetic dead-end or a leaky gate.

SPEC-PORTAL-PROFILES-001 Phase 2 shipped with only Unlock + part of
Read wired correctly. The tenant flag (`portal_orgs.enabled_addons`)
on `/admin/settings` persisted, and `require_product` enforced the
two-layer AND. But `list_available_products` and `assign_product` /
`assign_group_product` still read `get_plan_products(org.plan)` only,
so the admin UI dropdown never offered the new addons and a direct
POST got 403. In the reverse direction, `get_effective_products` did
not filter granted entitlements against `enabled_addons`, so the
sidebar still advertised an addon for one click after the toggle was
disabled. Fixed in PR #291 (commit `db9c2e9002f2`, 2026-05-04) by
introducing `_assignable_products(org) = plan_products | enabled_addons`
on the assign side and a dormancy filter on the read side.

**Why this slips through:** each side passes its own unit tests in
isolation. `require_product` tests stayed green because they exercised
the read path through a pre-built fixture. The unlock-side endpoint
test only checked the toggle write. Assign-side tests covered the
old plan-ceiling. None of them composed the full chain, and the
SPEC's own description ("checks two things: A AND B") did not
translate into a per-consumer audit.

**Prevention (mechanical):** Before merging any SPEC implementation
that adds a new gating layer, run the audit explicitly:

1. Grep every reference to the OLD single-layer gate that is still
   the source of truth for any decision:
   ```bash
   grep -rn "get_plan_products\|<old_gate_function>" \
     klai-portal/backend/app klai-connector/app
   ```
   For each hit, decide: does it correctly compose with the new
   layer, or is it intentionally left as-is with a `# noqa:
   single-layer — reason …` comment? No silent leftovers.

2. Grep every consumer of the read function that surfaces entitlements
   to clients:
   ```bash
   grep -rn "get_effective_products\|/api/me\|/internal/" \
     klai-portal/backend/app
   ```
   Each must either apply the new layer's filter or carry a comment
   explaining why it is exempt.

3. Add at minimum one full-chain integration test: tenant flag ON +
   per-user assignment → read endpoint sees product. Then tenant flag
   OFF → read endpoint no longer sees it. This is the only test that
   catches cross-layer gaps; per-layer unit tests cannot.

4. In the SPEC's Success Criteria, list the three sides explicitly as
   separate checkboxes ("Unlock UI wired", "Assign endpoints accept
   new layer", "Read consumers filter by new layer"). A single
   "implements two-layer gate" box hides the audit.

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

## worktree-teardown-after-merge (HIGH)
After `gh pr merge --delete-branch` from inside a worktree, the LOCAL-side
cleanup step (`git checkout main && git branch -D <feature>`) silently
fails because:

1. `git checkout main` errors with "main is already used by worktree at..."
   if any other worktree (including a stale `klai-kb-sources` mirror) has
   `main` checked out.
2. Even when main IS available locally, `git branch -D <feature>` errors
   with "cannot delete branch <X> used by worktree at <path>" — the
   worktree IS the thing using it.

`gh` reports the failure but the merge ITSELF succeeded on GitHub. The
worktree directory stays on disk, the local branch stays in
`git branch`, and over many PRs you accumulate dead worktrees.

**The 2026-05-04 incident:** the canonical klai repo had 39 worktrees
on disk (40 including main repo), 30 stale local branches with `gone`
upstream, and 10 stashes — almost all from this exact failure mode
across many sessions. The `klai-kb-sources` worktree had quietly been
holding `main` for weeks, forcing every `git switch main` attempt into
detached-HEAD as a workaround, which spawned more workarounds.

**Prevention (mechanical, SPEC-INFRA-AI-WORKFLOW-001):**

- `.claude/hooks/klai/pr-merge-teardown.sh` runs PostToolUse on
  `gh pr merge`. If PWD is a worktree (not canonical repo), it prints
  the exact teardown command: `git worktree remove --force <path> &&
  git branch -D <branch>`. Does NOT auto-execute — surfaces the next
  step so the next assistant turn (or human) sees and runs it.
- `.claude/hooks/klai/worktree-no-main.sh` runs PreToolUse on
  `git worktree add`. Refuses any form that checks out `main` in a
  non-canonical location. Suggests `-b feature/<task> origin/main`
  instead. This stops the kb-sources class of incident.
- `.claude/hooks/klai/session-start-hygiene.sh` runs SessionStart and
  warns if worktree count, gone-branch count, or non-rescue-stash
  count exceed thresholds. Does NOT auto-clean.

**Recovery for an existing collision (manual):**

1. `git worktree list` to see who's holding main.
2. If a non-canonical worktree (e.g. `klai-kb-sources`) has main and
   you don't need it: `git worktree remove --force <path>`.
3. Back in canonical repo: `git switch main` should now work.
4. `git branch -vv | grep ': gone\]' | awk '{print $1}' | xargs -r
   git branch -D` to prune stale local branches.

**Windows-specific:** locked agent worktrees (`.claude/worktrees/agent-*`)
may need `git worktree unlock <path>` before `git worktree remove`. The
`bezmaw3bz` background command on 2026-05-04 hung on locked worktrees;
the fix is to unlock first or use `--force` synchronously rather than
running 30+ removes in a single bash chain.

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
  extraction. (Resolved May 2026: dropped the role-based klai-pipeline
  alias; both code and prod now use klai-fast which points at the
  same Mistral Small 4 model.)
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

**Audit (2026-04-27, updated 2026-04-29)** — verified by greping `Dockerfile` ENTRYPOINT/CMD
across services:

| Service | Auto-migrates on container start? |
|---|---|
| portal-api | YES — `entrypoint.sh` runs `alembic upgrade head` then exec's uvicorn |
| klai-connector | YES — `entrypoint.sh` runs `alembic upgrade head` then exec's uvicorn (added 2026-04-30 after migration 006_add_org_id_to_sync_runs shipped in image but never ran on prod) |
| scribe-api | YES — `entrypoint.sh` added by SPEC-SEC-AUDIT-2026-04 C5 (PR fix/scribe-c5-alembic-auto-migrate) |
| klai-mailer | NO — `CMD uvicorn …` only |
| klai-knowledge-mcp | NO — `CMD python main.py` only |
| klai-knowledge-ingest | NO — `CMD uvicorn …` only |
| klai-retrieval-api | NO — `CMD uvicorn …` only |

The remaining 4 services without auto-migration are tracked in
SPEC-DEPLOY-AUTO-MIGRATE-001 as follow-up work. The portal-api, klai-connector
and scribe-api `entrypoint.sh` pattern (introduced by SPEC-CHAT-TEMPLATES-CLEANUP-001
and extended by SPEC-SEC-AUDIT-2026-04 C5) is the canonical template to copy.
klai-connector's `entrypoint.sh` adds the twin requirement on its `alembic.ini`:
`prepend_sys_path = .` (so `from app.models.connector import Base` resolves) —
without that line `alembic upgrade head` exits with
`ModuleNotFoundError: No module named 'app'` and the container will
crash-loop on every restart. Spotted live on 2026-04-30 when the
`Sync now` click failed with `column sync_runs.org_id does not exist`
on the very first new-build connector, requiring
`docker exec klai-core-klai-connector-1 sh -c 'PYTHONPATH=. .venv/bin/alembic upgrade head'`
as a hand-applied fix before this entrypoint pattern landed.

## alembic-cannot-drop-non-portal_api-tables (HIGH)

`portal_api` is the role that runs `alembic upgrade head` from the
portal-api `entrypoint.sh`. Tables created with RLS (or any table whose
ownership is `klai` superuser instead of `portal_api`) cannot be **DROPPED,
have RLS ENABLED on them, or have CREATE POLICY applied** by `op.execute(...)`
in a normal alembic migration — Postgres raises
`InsufficientPrivilegeError: must be owner of table <name>`. The
entrypoint then crash-loops and the deploy lands in 502.

The full list of owner-required DDL is broader than DROP TABLE:
- `DROP TABLE` (original incident, SPEC-AUTH-009)
- `ALTER TABLE ... ENABLE ROW LEVEL SECURITY` (SPEC-SEC-PORTAL-RLS-001 #364, 2026-05-05)
- `ALTER TABLE ... FORCE ROW LEVEL SECURITY`
- `CREATE POLICY` / `DROP POLICY` (when target is owned by klai)
- `ALTER TABLE ... OWNER TO ...`

All of these need the post-deploy SQL pattern below.

This bit SPEC-AUTH-009 (PR #259, 2026-05-04). Migration `ed5b78b296f5`
ended with `op.drop_table("portal_org_allowed_domains")`. The table was
owned by `klai` (RLS pattern from SPEC-AUTH-006), not `portal_api`. On
deploy:

1. `alembic upgrade head` ran the ADD COLUMN statements fine.
2. The DROP TABLE failed with `InsufficientPrivilegeError`.
3. portal-api crash-looped on every `alembic upgrade` retry.
4. `voys.getklai.com/api/auth/oidc/start` → 502.

**Recovery (manual, on prod):**
```bash
# As klai superuser:
DROP TABLE IF EXISTS <table> CASCADE;
# Re-run any pending migration steps (ADD COLUMN with backfill, etc.) manually:
ALTER TABLE ... ;
UPDATE ... ;
# Stamp alembic so the entrypoint doesn't re-attempt the failing migration:
UPDATE alembic_version SET version_num = '<failed-revision>';
docker restart klai-core-portal-api-1
```

**Prevention:** Whenever a migration drops a table whose `pg_class.relowner`
is not `portal_api`, do NOT use `op.drop_table(...)`. Instead:

1. Replace the `op.drop_table(...)` call with a comment explaining the delegation.
2. Add a sibling `alembic/versions/post_deploy_<revision>.sql` containing
   `DROP TABLE IF EXISTS <name> CASCADE;` (idempotent).
3. The post-deploy SQL is run by an operator (or `apply_post_deploy_sql.sh`)
   as `klai` superuser AFTER `alembic upgrade head` completes successfully.

Mirrors the RLS pattern (`post_deploy_f0a1b2c3d4e5.sql` for SPEC-WIDGET-002,
`post_deploy_rls_*.sql` for the RLS rollouts).

**Detection during PR review:** for any migration with `op.drop_table(...)`
or `op.execute("ALTER TABLE ... OWNER TO ...")`, check `pg_class.relowner`
on the live DB (or recall the table's history). If it was created by an
RLS-enabled migration, it almost certainly is owned by `klai`.

```bash
# Quick owner-check on prod:
ssh core-01 "docker exec klai-core-postgres-1 sh -c 'psql -U \$POSTGRES_USER -d \$POSTGRES_DB -c \"SELECT tablename, tableowner FROM pg_tables WHERE tablename = ARG;\"'"
```

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

## container-cleanup-without-preflight (HIGH)
When you face a "wees-uitziende" container — no
`com.docker.compose.project` label, no Caddy upstream, untagged image —
do NOT treat the absence of those signals as a verdict to delete. Klai
has TWO legitimate classes of prod containers:

- **Klasse A — compose-managed:** `com.docker.compose.project=klai-core` label
- **Klasse B — provisioning-managed:** `klai.managed_by=portal-api-provisioning`
  + `klai.tenant_slug=<slug>` + `klai.kind=<type>` (gezet door
  `_start_librechat_container` per SPEC-INFRA-CONTAINER-HYGIENE-001 REQ-2a)

A container without ANY of these labels AND without a `klai.adhoc=*`
opt-in is a candidate wees, but verify before deleting.

The librechat-voys incident (2026-05-02) is the canonical case. The
production Voys-tenant chat container was klasse B (provisioning-managed
by portal-api) but at the time the labels did not yet exist as a
convention. The cleanup-agent (me) checked only for klasse-A label,
saw none, also saw no current Caddy upstream, and concluded "wees".
Wrong on both counts: the container was legitimate; Caddy upstream
absence had a different cause (timing of provisioning vs. caddy
reload). Recovery succeeded only because `/opt/klai/librechat/voys/`
(env-file + librechat.yaml) survived; the original image SHA
(untagged, never registered) was permanently lost.

**Why this is a HIGH-class trap:** the signals that look like "orphan"
are exactly the signals you'd expect from a legitimate
production-relevant container managed via a non-compose pathway.
"No compose label" correlates with rommel for klasse-A containers,
NOT for klasse-B (provisioning-managed) containers. Tenant-specific
names (`librechat-*`, `-voys`, `-getklai`, `-<klant>`) flip the prior:
those are almost never test fixtures.

**Tenant-deprovisioning is NOT just `docker rm`.** A klasse-B
container belongs to a tenant whose Mongo user, Meilisearch index,
Caddy upstream, and Redis cache need cleanup too. Use the portal-api
deprovision flow (`provisioning/orchestrator.py::deprovision_tenant`)
— never `docker rm` a `librechat-<slug>` directly.

**Prevention (mechanical, not narrative):**

1. Hook `.claude/hooks/klai/container-hygiene-preflight.sh` blocks
   `docker rm`/`rmi`/`volume rm`/`system prune`/`compose down --volumes`
   on any target matching tenant-naam patterns or appearing in
   `klai-infra/deploy/docker-compose*.yml` history. Registered as
   PreToolUse in `.claude/settings.json` alongside
   `portal-api-preflight.sh`. SPEC-INFRA-CONTAINER-HYGIENE-001 REQ-1.

2. Hook hard-blocks `docker volume prune`, `docker image prune -af`,
   `docker system prune -a`, `docker compose down --volumes`. Use
   targeted `volume rm` after manual review or the systemd
   `docker-cleanup.timer` (SPEC REQ-6) for safe daily prune.

3. Weekly `klai-orphan-audit` (SPEC REQ-5) emits structlog events to
   VictoriaLogs (`service:klai-orphan-audit`); cleanup-agents query
   the stream BEFORE deciding via VictoriaLogs MCP. Caddy upstream
   without container, container with tenant-naam without Caddy
   upstream, untagged images >30d — all flagged for human review,
   never auto-removed.

4. Ad-hoc debug runs MUST use
   `docker run --rm --label klai.adhoc=YYYY-MM-DD-reason --label klai.owner=<email>`
   so they self-clean and appear in a separate "ad-hoc" audit
   section, not in the "wees" section.

**Why mechanical not narrative.** An AI is the primary code- and
deploy-actor in this codebase. A markdown rule that "agents should
read first" is a gap that re-opens with every context truncation,
prompt variation, or new agent. The hook is the only enforcement that
survives all those failure modes. The narrative rule
`.claude/rules/klai/infra/container-hygiene.md` exists for human
review and override-path documentation, not as the primary guard.

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

## global-test-state-collision (MED)
Two SPECs each merge a test file that globally configures structlog
via `structlog.configure(...)` + `sl.reset_defaults()` in a `try /
finally`. Each branch is green in isolation. After the second merge,
the third merger discovers the two test files now coexist and
collide: one of them imports a production helper from a module that
runs `setup_logging()` at module-load time, which globally swaps the
processor pipeline, which makes the OTHER test's
`structlog.configure`-based capture see no events.

SPEC-SEC-HYGIENE-001 portal-slice (HY-28) hit this exactly:
`tests/test_docs_gating.py` imported `from app.main import
_should_expose_docs`. SPEC-SEC-CORS-001 (#180, already on main)
shipped `tests/test_cors_allowlist.py` with a structlog-capture
fixture. On their separate branches each had only one of the two
files, so the conflict only surfaced after the main->portal-v02
merge. The 1334-test suite regressed from "all green" to "2 failed"
in `test_cors_allowlist.py`, and bisecting (`pytest tests/test_X.py
tests/test_cors_allowlist.py` for each candidate) identified
`test_docs_gating.py` as the breaker.

**Why it slips through review.** Both PRs run their own CI, both
green. Neither test files reference each other directly. The
production helper that ties them together (`app.main`) is not on
either PR's diff.

**Resolution rule.** Move helpers consumed by tests OUT of any
module that runs `setup_logging()` (or any other module-load
side-effect that mutates global state) at import time. For
SPEC-SEC-HYGIENE-001 this meant relocating `should_expose_docs` from
`app/main.py` to `app/core/config.py`, where the helper lives next to
its data (`Settings`) AND the import path is side-effect-free for
tests. The `tests/test_startup_sso_key_guard.py` workaround
(replicate the helper inline with a "drift mitigated by validator"
comment) is acceptable for a one-line helper but introduces real
drift risk for anything more complex — prefer the
relocate-and-import-once approach when the helper has more than one
decision branch or might grow.

**Prevention.**
- For any new test file that uses `structlog.configure()` for
  capture, add an `# @MX:NOTE: do not import from app.main; this
  test relies on global structlog state` line on the import block,
  so reviewers on adjacent PRs see the trap when adding imports.
- For any production helper that tests need to import: place it in a
  module that does NOT call `setup_logging()` (or other global
  config) at module-load time. Common safe homes:
  `app/core/config.py`, `app/utils/*.py`. Common unsafe home:
  `app/main.py`.
- If two SPECs are in flight that each modify global test state,
  coordinate during /plan: identify the shared global, agree on the
  relocation, ship the relocation FIRST as a no-behavior-change PR.

## uvlock-conflict-resolution-via-uv-lock (LOW)
A 3-way merge conflict in `uv.lock` is almost never worth resolving
by hand. The lock file's structure (TOML with hash-pinned
dependencies, ordered alphabetically) means even a small upstream
delta produces dozens of conflict markers across hundreds of lines,
and a hand-merge can subtly diverge from what the resolver would
have produced — leaving a passing CI today and a "but our prod
image differs from local" surprise next week.

**Resolution rule.**

```bash
git checkout --theirs klai-portal/backend/uv.lock
cd klai-portal/backend
uv lock
git add uv.lock
```

This takes upstream's lockfile (assumed to be the more-recently-
audited resolution) and asks `uv` to reconcile any pyproject.toml
additions on top of it. The output is byte-equal to what `uv lock`
would produce on a clean checkout.

**Verification.** After the merge commit, `uv sync --group dev`
reports the EXPECTED diff against the previous environment (e.g.
"+ zxcvbn==4.5.0" for SPEC-SEC-HYGIENE-001 REQ-22). Any unexpected
package change (e.g. a major version bump) is a signal that the
upstream lockfile drifted further than the merge metadata
suggested — investigate before accepting.

**Prevention.** Same as for any merge conflict: rebase often when
you know main is moving, and use `git fetch && git log --oneline
HEAD..origin/main -- klai-portal/backend/pyproject.toml` to see
upstream pyproject changes before they collide with yours.

## fail-open-auth (HIGH)
When a service treats an EMPTY env var (whitespace-only or unset) as "no auth required", a misconfigured deploy silently disables auth instead of refusing to start. Empty-secret bypasses are catastrophic — webhook 200s on attacker traffic, internal endpoints accept any caller.

Reference cases: SPEC-SEC-WEBHOOK-001 REQ-3 (Moneybird empty-token bypass before fix), SPEC-SEC-IDENTITY-ASSERT-001 (knowledge-mcp `KNOWLEDGE_INGEST_SECRET` empty fail-open).

**Prevention:** Every auth-related secret in pydantic settings MUST have a `@model_validator(mode="after")` that rejects empty/whitespace values. The validator must run at startup, not at first request, so misconfigured deploys fail-fast in CI/staging.

## empty-secret-fail-open (HIGH)
Closely related to fail-open-auth but specifically about OUTBOUND calls: when `httpx.post(..., headers={"Authorization": f"Bearer {self._secret}"})` runs with `self._secret == ""`, the receiver sees `Bearer ` (literal trailing space) and may accept it as auth, or worse, log it as legitimate.

Reference: SPEC-SEC-INTERNAL-001 connector empty-secret bypass; klai-portal/backend/app/services/klai_connector_client.py.

**Prevention:** Outbound HTTP clients MUST refuse to construct the request if the credential is falsy. Raise at construction, never silently send.

## empty-encryption-key-mid-lifespan-crash (HIGH)
When a service uses a base64-encoded AES / Fernet / KEK env var that is consumed via `cipher = AESGCM(base64.b64decode(settings.encryption_key))` (or equivalent) during FastAPI lifespan, an empty / missing / invalid env var crashes mid-lifespan with a cryptic `ValueError: AES-256 requires a 32-byte key, got 0 bytes`. The container restart-loops, ops sees a stack trace deep inside cryptography internals, and the actual misconfiguration is invisible.

Reference: 2026-05-04 incident on klai-connector. `CONNECTOR_ENCRYPTION_KEY` was declared in `deploy/docker-compose.yml` as `${CONNECTOR_ENCRYPTION_KEY}` but was never added to `klai-infra/core-01/.env.sops`. Compose interpolation passed empty string through, pydantic-settings happily stored `""`, and `AESGCMCipher(base64.b64decode(""))` crashed at `cipher = AESGCM(b"")`.

**Prevention:**

1. **Pydantic validator on every encryption-key field**, mirroring the `_require_*_secret` patterns in `klai-mailer/app/config.py` and `klai-connector/app/core/config.py`:
   ```python
   @field_validator("encryption_key", mode="after")
   @classmethod
   def _require_valid_encryption_key(cls, v: str) -> str:
       if not v or not v.strip():
           raise ValueError("CONNECTOR_ENCRYPTION_KEY must be non-empty base64 …")
       try:
           decoded = base64.b64decode(v, validate=True)
       except Exception as exc:
           raise ValueError(f"… valid base64. {exc!s}") from exc
       if len(decoded) != EXPECTED_KEY_LEN:  # 32 for AES-256, 32 for Fernet
           raise ValueError(f"… exactly {EXPECTED_KEY_LEN} bytes, got {len(decoded)}")
       return v
   ```
   Validator runs at module-load (Settings init) — fails BEFORE the FastAPI lifespan, so the error surface is "ValidationError on Settings load" with an actionable message, not a mid-lifespan crash.

2. **Test the validator** with: empty string, whitespace-only, invalid base64, decoded-too-short, decoded-too-long, missing-env. See `klai-connector/tests/test_encryption_key_validator.py` for a worked example.

3. **VALIDATOR-ENV-PARITY applies**: before landing the validator, verify the env var exists in `klai-infra/core-01/.env.sops`. Without env-parity, the validator-bearing release crash-loops on every deploy. See validator-env-parity (HIGH) above.

4. **Audit checklist** when adding a new encryption / KEK field to a service config:
   - Field declared in `app/core/config.py` Settings? ✓
   - Field interpolated in `deploy/docker-compose.yml` environment block? ✓
   - Env var present in `klai-infra/core-01/.env.sops`? ✓
   - `@field_validator(mode="after")` rejects empty + invalid format? ✓
   - Test file covers all reject paths? ✓
   - Test for valid 32-byte (or whatever-length) round-trip? ✓

   All six required. Missing any = future restart-loop incident.

## non-constant-time-secret-compare (HIGH)
`==` and `!=` short-circuit on the first non-matching byte. Comparing user-supplied tokens / signatures / secrets with these operators leaks length and content via timing. The leak is exploitable across the LAN; on a same-host attacker (think compromised sidecar), a few thousand probes recovers the secret byte-by-byte.

Reference: mailer `_validate_incoming_secret` was using `!=` until SPEC-SEC-INTERNAL-001 cross-service fix.

**Prevention:** ALL secret/token/signature equality comparisons MUST use `hmac.compare_digest`. Add a semgrep rule to catch `==`/`!=` against any variable named like `*secret*`, `*token*`, `*signature*`. Reviewers MUST flag any auth-comparison without `compare_digest`.

## format-string-template-injection (CRIT)
`str.format(**user_dict)` walks attribute chains: `{x.__class__.__base__.__subclasses__}` is a valid format token, leading to introspection-based RCE. NEVER pass user-controlled data through `.format()`, `.format_map()`, or f-string `__class_getitem__` paths.

Reference: SPEC-SEC-MAILER-INJECTION-001 — mailer rendered email subjects/bodies via `template.format(**variables)` where keys came from inbound webhook JSON.

**Prevention:** Use `string.Template.safe_substitute` (allows only $-prefixed identifiers, no attribute traversal) or `jinja2` with `autoescape=True` and a sandbox. Add the format-string-injection check to the security-review skill checklist.

## allowlist-must-enumerate-all-host-classes (HIGH)
A security-allowlist is only as good as the enumeration that built it. When a hardening SPEC introduces a new check on hostnames, identifiers, or any user-facing string, the implementer MUST list **every** legitimate class the field can hold — not just the obvious user-facing class — before the check ships. Missing one class breaks production at deploy time.

Reference: SPEC-SEC-HYGIENE-001 REQ-20 (callback URL subdomain allowlist) shipped on 2026-04-29 with three host classes enumerated (`localhost`, bare apex, tenant-slug) and one missed: the FRONTEND_URL host (`my.getklai.com`). Zitadel always redirects through the FRONTEND_URL host first per SPEC-AUTH-008, so every multi-tenant TOTP login started returning 502 within minutes of deploy. Fixed by REQ-20.4 (system-host bypass derived from `settings.frontend_url`) and `tests/test_callback_url_allowlist.py`. The original PR landed with **zero** dedicated tests on the validator, which is why CI did not catch the regression.

**Prevention checklist for any new allowlist / blocklist / hostname-validator PR:**

1. Before merging, list every hostname / identifier the validator will see in production. Grep the codebase for the field across all services. Enumerate at minimum:
   - localhost / 127.0.0.1 (dev)
   - the bare apex domain
   - any FRONTEND_URL / login domain / admin domain
   - all currently-active user-tenant subdomains
   - any third-party-callback domains (Stripe, Vexa, Moneybird, etc.)
2. Each enumerated class MUST appear either explicitly in the allowlist OR with a documented bypass (a comment on the bypass line + a test asserting the bypass).
3. The validator MUST have a dedicated test file in `klai-portal/backend/tests/` (or the equivalent service) covering at least one accept-case per enumerated class AND at least two reject-cases (random unknown, lookalike-substring). No "we'll add tests later" merges on auth surfaces — the v0.7.1 hotfix exists because of this exact decision.
4. Configurable values (`settings.domain`, `settings.frontend_url`, etc.) MUST be derived from settings — never hardcoded strings — so dev / staging / prod work without code changes.
5. PR description MUST include the rollback command. For validator-style hardening: `git revert <sha> && gh run watch && verify on core-01`. So when the regression hits prod, recovery is one command, not a panic.

## redis-url-password-must-be-parsed-manually (HIGH)
`redis_asyncio.from_url(url)` (and `redis.Redis.from_url`) delegate to `urllib.parse.urlparse`. urlparse rejects URLs whose userinfo password contains characters it treats as URL-reserved — most commonly `:`, `/`, `+`, `@`, `#`, `?`, `%` — by raising `ValueError("Port could not be cast to integer value as '<garbled>'")` on the first property access. Operators routinely paste a generated password into SOPS without percent-encoding it, the service starts cleanly because `from_url` is called lazily, and every subsequent Redis operation crashes with an opaque error.

Reference: SPEC-SEC-MAILER-INJECTION-001 v0.3.1 (2026-04-29). klai-mailer's `app/nonce.py::get_redis()` called `redis_asyncio.from_url(settings.redis_url)`. Production password contained `:`, `/`, `+`. Every Zitadel webhook to `/notify` returned 500. The mailer's `setup_logging()` swallowed uvicorn access logs, so neither the request nor the traceback appeared in `docker logs` — diagnosis required tailing the container live AND triggering a fresh request to capture the ASGI traceback. Outage duration ~4 hours (12:36 → 17:30 UTC). Fixed by `parse_redis_url` (structural splits, not urlparse) + `Redis(host=..., password=..., ...)` kwargs.

**Prevention for any code constructing a Redis client (or any URL-userinfo-bearing client):**

1. Do not call `Redis.from_url(settings.foo_url)` if there is any chance the password came through SOPS / env var. Either:
   - Construct via individual env vars (`REDIS_HOST`, `REDIS_PASSWORD`, `REDIS_PORT`) — the 12-factor recommended pattern, no escaping ambiguity.
   - Use a structural URL parser (e.g. `app/redis_url.py::parse_redis_url`) that takes the password as opaque bytes between the first `:` after the scheme and the last `@` before the host.
2. If `from_url` is unavoidable (e.g. third-party library doesn't accept kwargs), document in SOPS that the password MUST be percent-encoded AND add a startup-time `urlparse(settings.redis_url).port` access in the lifespan handler — that fails-fast at deploy with a clear error instead of crashing on the first request.
3. Include a regression test that loads the URL with `:` in the password (`redis://:p:hPKBf@host:6379/0`) and asserts the client constructs without error. The test must NOT use a hand-crafted "valid" password — pretend the operator pasted directly from a 1Password generator.
4. Verify the same pattern across every klai service that uses Redis (portal-api, retrieval-api, knowledge-ingest, scribe, connector, mailer). If any of them uses `from_url(settings.redis_url)` and the URL hasn't been audited for password encoding, that's a latent outage waiting on a password rotation.
5. Audit logging-config: the mailer outage was 4× longer than necessary because `setup_logging()` suppressed uvicorn access logs. For any service that uses structlog + `ProcessorFormatter`, verify uvicorn access logs still surface in stdout — otherwise an unhandled exception is invisible to `docker logs`.

## alembic-stamped-past-skipped-migration (HIGH)
`alembic_version` is a single source of truth for "what's the current head" — it is NOT a log of every migration that ran. If a migration's `op.create_table` (or any other DDL) silently fails to execute on a given deploy but `alembic_version` still advances past it, the schema and alembic's view of the schema permanently diverge. Every subsequent `alembic upgrade head` is a no-op (head already equals current), so the missing tables / columns / indexes never get created. The only symptom is a 500 on the first endpoint that touches the missing object — sometimes weeks after the original deploy.

Reference: 2026-04-30 — `/api/admin/domains` and `/api/admin/join-requests` returned 500 on prod. Root cause: `portal_org_allowed_domains` and `portal_join_requests` (created by SPEC-AUTH-006 migrations `23c5c8b48669` and `b2c3d4e5f6g7`) did not exist in the production DB even though `SELECT version_num FROM alembic_version` returned `v2m3e4r5g6h7` (head). All other migrations on the chain were applied — verified by probing five sentinel columns/tables (`portal_templates`, `vexa_meetings.recording_deleted_at`, `portal_connectors.content_type`, `portal_users.kb_narrow`, `portal_users.active_template_ids`) — only those two specific tables were missing. Likely trigger: the `93bf090e refactor(alembic): real random hex ids + resolve a1b2c3d4e5f6 duplicate` rename on 2026-04-22 happened while a deploy that had already advanced `alembic_version` past that chain segment was in flight, so the renamed migrations were never re-walked. Logs from the original deploy window were rolled (Docker `max-size: 50m, max-file: 3`) so the exact moment is not recoverable. The fix was to apply the alembic-generated DDL directly via `psql` (alembic_version stayed at head; the schema caught up).

**Prevention:**

1. **Post-deploy schema drift check on any portal-api PR that adds a migration.** Add a `scripts/verify_schema_at_head.py` (or extend `scripts/apply_post_deploy_sql.sh`) that reads every `op.create_table(...)` from migration files newer than `alembic_version - 5` and asserts each table exists in `information_schema.tables`. Run it as the last step of `portal-api.yml` CI deploy. Failing the deploy on schema-vs-head divergence means future regressions surface within minutes instead of weeks.
2. **Never `alembic stamp` on prod without immediately following with `alembic upgrade head` in the same shell session.** A bare `stamp` command silently advances `alembic_version` past migrations that have not run. If you ever need to mark a migration as "already applied" on prod (e.g. recovering from a renamed-revision incident), document the reason in the deploy log and verify the schema state before AND after.
3. **For an alembic-rename refactor (commit shape: changing `revision = "..."` of an existing migration file):** before merging, `docker exec klai-core-portal-api-1 alembic current` on prod and confirm the result is NOT inside the chain segment being renamed. If it is, the rename either has to wait or be paired with a manual `alembic stamp <new-id>` on the spot — never assume the rename is invisible to prod.
4. **Diagnostic playbook when an endpoint mysteriously 500s after a recent deploy:**
   ```bash
   ssh core-01 "docker exec klai-core-portal-api-1 alembic current && docker exec klai-core-portal-api-1 alembic heads"
   # Pick a sentinel table from each recent migration and probe:
   ssh core-01 "docker exec klai-core-postgres-1 sh -c 'psql -U \$POSTGRES_USER -d \$POSTGRES_DB -c \"SELECT to_regclass('"'"'public.<expected_table>'"'"');\"'"
   ```
   If `alembic current` is at head AND `to_regclass` is NULL for an expected table, you have schema drift. The fix is `alembic upgrade --sql <prev>:<head>` (offline, generates the DDL alembic WOULD have run), strip the `UPDATE alembic_version SET ...` lines, apply via `psql`. Do NOT `alembic stamp` backwards then `upgrade head` — that re-runs ALL migrations after the stamp point, most of which will fail because their tables already exist.
5. **Logging.** When a handler queries an ORM model whose backing table doesn't exist, `asyncpg.exceptions.UndefinedTableError` is raised inside SQLAlchemy and FastAPI catches it as a generic 500. The trace IS in VictoriaLogs (`service:portal-api AND level:error AND message:"UndefinedTable"`) — use that query first when triaging "500 on a single endpoint, all other endpoints fine" before assuming it's an auth / dependency-injection / RLS issue. Diagnosis-without-logs falls back to the `to_regclass` probe above.

## grafana-uid-40-char-limit (HIGH)
Grafana enforces a hard 40-character limit on alert-rule and dashboard UIDs. A
file-provisioned alert rule with `uid: <49+ chars>` causes the entire
provisioning step to fail with `Failed to provision alerting: ... cannot
create rule with UID '<x>': UID is longer than 40 symbols`, which in turn
crashes Grafana into a restart loop. While Grafana is down: ALL alerts stop
firing, the alerter-on-alerter heartbeat stops pushing to Uptime Kuma, and
the dead-man's switch fires within 5 minutes (SPEC-OBS-001 R22 alerter-down
detection).

Reference: 2026-05-04 — SPEC-INFRA-CONTAINER-HYGIENE-001 stage 6 first
deploy (PR #296, 12:11 CEST) used UIDs `spec-infra-container-hygiene-001-tenant-no-route` (49 chars) and `spec-infra-container-hygiene-001-caddy-upstream-missing` (55 chars). Grafana entered restart loop the moment deploy-compose.yml synced the files. Recovery required revert (#297, 12:13 CEST) PLUS a manual `rm` of the synced files on core-01 because deploy-compose.yml uses `rsync -ac` WITHOUT `--delete`, so revert-removed files remain on disk and re-trigger the failure on every grafana recreate. Total downtime ~3 minutes (12:11 → 12:14 CEST), within Kuma's 5-minute heartbeat window — no alerter-down email was sent.

**The class.** Any provisioned Grafana resource with a UID has a 40-char limit. This applies to:
- Alert rule UIDs (`groups[].rules[].uid`)
- Dashboard top-level UIDs (`{"uid": "..."}`)
- Folder UIDs (when explicitly set)
- Notification policy UIDs

**Why the limit isn't documented mechanically.** Grafana's docs describe UIDs as "any string up to 40 characters" but the enforcement is a fail-loud refusal at provisioning, not a config-time validation. CI checks that don't probe the actual provisioning path will pass; the failure surfaces only on the first sync to a real Grafana.

**Prevention (mechanical, the right kind of guard):**

1. CI script `scripts/audit-alert-uid-length.sh` runs in the `Alerting provisioning checks` workflow on every PR that touches `deploy/grafana/provisioning/{alerting,dashboards}/**`. Iterates every YAML `uid:` and JSON top-level `"uid"`, fails CI if any is > 40 chars. Tested against 9 cases (5 accept incl. existing UIDs, 4 reject incl. path-traversal). After this guard landed, the class is structurally impossible to ship.

2. UID convention for klai-provisioned alerts: `spec-<3-letter>-<3-digit>-<verb>` (e.g. `spec-hyg-001-tenant-no-route`). The 3-letter abbreviation must be unique across active SPECs. Existing prefixes: `obs-`, `spec-sec-024-`, `spec-infra-005-`, `spec-hyg-001-`. Never use the full SPEC ID — they're 30+ chars before the verb.

3. `scripts/reset-grafana-orphan-alert.sh` accepts UIDs matching `^(obs-[0-9]+|spec-[a-z][a-z-]*-[0-9]+)-` so multi-word abbreviations work for the cleanup-by-uid path. (Shorter abbrevs are still preferred — the 40-char limit doesn't change.)

4. The `rsync without --delete` quirk is a SEPARATE class: `deploy-compose.yml` syncs `deploy/grafana/provisioning/` to `/opt/klai/grafana/provisioning/` with `rsync -ac` (no `--delete`). Revert-removed files persist on disk. For any future revert that removes provisioning files, the operator MUST `ssh core-01 "rm /opt/klai/grafana/provisioning/<deleted-path>"` after the revert merges. Long-term fix is to add `--delete` to the rsync (separate SPEC — `--delete` has its own blast-radius considerations because server-only files would also disappear).

## playwright-mcp-config-cycle (HIGH)
The `@playwright/mcp` configuration in `.mcp.json` has been "fixed" at least
five times since April 2026 (commits `940d079e`, `0ceab6b6`, `b26d5e01`,
`0a423697`, `ce6a8ab5`) — each fix re-introduced the failure mode that the
previous fix was trying to eliminate. The user has explicitly named this an
"every-time-you-fix-it-it-breaks-the-other-thing" cycle. Before changing
ANYTHING about this config, you MUST internalise this entry, because the
failure modes look like ordinary bugs each time you encounter them.

**Use case (settled, do not re-derive):**
AI-driven coding sessions where the assistant validates its own changes
end-to-end via Playwright MCP. The user logs in ONCE; the AI takes over
from there. Multiple coding sessions can run in parallel, each needing a
visible browser with the same login already loaded. The AI does not log
out, does not change passwords, does not mutate auth state. Read-only
login state is therefore sufficient.

**The constraint on the platform.** `@playwright/mcp` and Chromium together
allow two profile modes:

- Persistent `--user-data-dir`: login persists, BUT "A persistent profile
  can only be used by one browser instance at a time, so concurrent MCP
  clients sharing the same workspace will conflict." Symptom: second
  Claude Code session fails with `Browser is already in use`.
- `--isolated`: parallel sessions work, ephemeral profile per process. By
  itself the profile starts empty; combine with `--storage-state <file>`
  to preload cookies/localStorage at startup.

`--storage-state` is read-only at startup and never written back. Microsoft
explicitly declined named-session-with-auto-save in
[#1530](https://github.com/microsoft/playwright-mcp/issues/1530)
(closed Not Planned, May 2026). For this repo's use case (AI doesn't mutate
auth) read-only is fine — refresh the file once when Google's session
cookies expire (~3 weeks).

**The canonical answer for this use case:**
`--isolated --storage-state ~/.claude/mcp-storageState.json`. Seed the
storage-state file via `browser_run_code_unsafe` with one Playwright
snippet that calls `page.context().storageState({ path })` — verified
working on 2026-05-07. Do NOT use `npx playwright codegen
--save-storage=...`: that path is flaky on macOS (the file only writes
on a specific Inspector-side close event; closing the browser the
wrong way means no file appears). PR #354 shipped codegen as the seed
and lost a week of debugging before the 2026-05-07 retry confirmed
the failure mode. Note: there is **no** separate `browser_storage_state`
MCP tool in `@playwright/mcp@latest` — earlier drafts of this entry
referenced one that does not exist. The functionality lives in the
generic `browser_run_code_unsafe` tool, which executes Playwright code
server-side; calling `page.context().storageState({ path: '...' })`
writes the cookies + localStorage directly. No external tooling, no
Inspector window, no Ctrl+C dance.

**Anti-patterns — do NOT propose any of these without re-reading this entry:**

1. `--config <some.json>` with `userDataDir` set inside the JSON. **Silently
   broken on `@playwright/mcp@0.0.70`** ([issue #1446](https://github.com/microsoft/playwright-mcp/issues/1446)):
   the JSON `userDataDir` is ignored and the browser launches with an
   in-memory profile. CLI flags work; JSON does not. If you must use a JSON
   config for some reason, verify on the running version that `userDataDir`
   is honoured before relying on it.
2. Persistent `--user-data-dir` for the primary playwright server.
   Single-instance only — second concurrent session fails with the lock
   error. Was tried in commits `b26d5e01` (apr 2) and `ce6a8ab5` (may 5).
3. `--isolated` ALONE (no `--storage-state`) for the primary server. Each
   session starts logged-out — defeats the whole point.
4. `--headless` on a server the AI uses to validate its own changes. The
   AI cannot SEE a headless browser; nothing to verify. Headed only.
5. `--executable-path <Brave/Chrome>` for the primary browser. On Windows,
   Brave/Chrome cannot run a second instance with a different profile —
   the second instance becomes a background process with no visible window.
   Use Playwright's bundled Chromium (omit `--executable-path` entirely).
6. Homegrown "slot pool" launchers that copy login files between profile
   directories at start/exit. Tempting because it sounds clever, but:
   - Not an industry standard pattern; no widely-validated implementation.
   - Race conditions on simultaneous shutdowns (last-write-wins on cookies).
   - SQLite cookie file copies during/after browser shutdown can corrupt.
   - Only justified if the AI mutates auth state across sessions, which
     this use case does not require.
   Was attempted on 2026-05-05; reverted in favour of `--storage-state`.
7. "Just remove all profile config and let Playwright pick its default" —
   default IS persistent `--user-data-dir`, so this re-triggers
   anti-pattern (2) from the other direction.
8. `npx playwright codegen --save-storage=...` as the seed step on macOS.
   The codegen process only writes the file on a specific Inspector-side
   close event; close the browser the wrong way and the file never
   appears. PR #354 (apr 2026) shipped this as the documented seed step
   and lost a full week of "but it should work" debugging before the
   2026-05-07 retry confirmed the failure mode. Use `browser_run_code_unsafe`
   with `page.context().storageState({ path })` instead (see canonical
   answer above).

**The current working setup (do not change without strong cause):**

- Primary `playwright` server in `.mcp.json` invokes
  `.claude/scripts/playwright-launcher.mjs`, which spawns
  `npx @playwright/mcp@latest --browser chrome --isolated
  --storage-state ~/.claude/mcp-storageState.json`. Headed (no `--headless`).
  Multi-session safe (each launch gets its own ephemeral profile).
  All sessions preload the same login state. **Note**: on
  `@playwright/mcp >= 0.0.74` the valid `--browser` values are
  `chrome|firefox|webkit|msedge`. `chromium` was dropped — passing it
  silently breaks the launcher. Use `chrome` (system Google Chrome
  install; combined with `--isolated`, gets its own ephemeral profile
  per session and does not collide with the user's regular Chrome).
- Secondary `playwright-isolated` server in `.mcp.json` runs `--browser
  chrome --isolated` with NO storage-state. Headed. For one-off CSS or
  unauthenticated checks where login state would just be noise.
- Login seed/refresh (run when `~/.claude/mcp-storageState.json` is
  missing, or when Google cookies have expired and sessions start
  logged-out):
  1. With the launcher in place but no storage-state file (or after
     deleting it), restart Claude Code so the MCP server picks up the
     new config. Open a new Playwright MCP session — the browser starts
     logged-out.
  2. Have the AI `browser_navigate` to a login URL (e.g.
     `https://voys.getklai.com`).
  3. Log in by hand (Google SSO + 2FA), wait until you're on the
     post-login workspace.
  4. Have the AI call `browser_run_code_unsafe` with this snippet (it
     writes the current cookies + localStorage to disk):
     ```js
     async (page) => {
       await page.context().storageState({
         path: '/Users/<you>/.claude/mcp-storageState.json'
       });
       return { url: page.url(), cookieCount: (await page.context().cookies()).length };
     }
     ```
     The tool returns immediately with the new cookie count. Verify the
     file mtime/size with `ls -la ~/.claude/mcp-storageState.json`.
  5. Restart Claude Code so the launcher picks the new file up via
     `--storage-state` at startup.
  6. From now on all MCP sessions, including parallel Claude Code
     instances, start authenticated.

**Prevention — symptom → correct response:**

| Symptom | Correct response | Wrong response |
|---|---|---|
| Sessions start logged-out | Verify `~/.claude/mcp-storageState.json` exists and is recent. If missing/stale, re-seed by running `browser_run_code_unsafe` with `page.context().storageState({ path })` after a fresh login (see "Login seed/refresh" above). | Add `--user-data-dir` back (anti-pattern 2); fall back to `playwright codegen --save-storage` (anti-pattern 8); search for a non-existent `browser_storage_state` tool. |
| `Browser is already in use` on a second session | Confirm `.mcp.json` uses `--isolated` not `--user-data-dir`. Each session must get its own ephemeral profile. | Add a slot-pool launcher (anti-pattern 6) |
| AI cannot see what the browser is doing | Confirm no `--headless` flag in the primary server. | Tell the user to "just check the browser themselves" — defeats the point |
| User says "iedere keer hetzelfde probleem" | Stop. Re-read this entry. Do not propose a config change before identifying which anti-pattern you are about to commit. | Propose another config edit |
| `browser_run_code_unsafe` tool not visible in current session | Tool-schema-cache is from an older `@playwright/mcp` pin (pre `0.0.74` exposed `browser_run_code` instead). Restart Claude Code so it reloads the schema list from `@latest`. | Try `browser_evaluate` to read cookies (HttpOnly cookies are invisible) or write a homegrown seed script (anti-pattern 6 territory). |
| `--browser chromium` rejected on launch | `@playwright/mcp >= 0.0.74` dropped `chromium` as a valid value. Use `--browser chrome`. | Pin back to `@0.0.70` (loses `browser_run_code_unsafe` — anti-pattern 8 territory). |
| Storage state file is hand-edited / non-standard | Delete it, re-seed via `browser_run_code_unsafe` after a fresh login. | Hand-edit JSON in storageState |

If a future @playwright/mcp version introduces auto-write-back of storage
state on session close, the manual refresh step can be removed. Until
then, refreshing the storage-state file every few weeks is the cost of
this design — accept it.

## stale-decommission-attracts-defensive-fixes (HIGH)
Wanneer een service is gedecommissioned maar de source-directory in de
git-tree blijft staan met een README "FROZEN — do not resurrect", trekt
die directory ALSNOG defensieve code-fixes aan op alsof hij levend is.
De volgende sweep-style PR die door alle callers van een interface gaat
behandelt de dode directory als een normale caller en patcht hem
zorgvuldig — zonder eerst te checken of de service draait.

SPEC-PORTAL-UNIFY-KB-001 (April 2026) verwijderde `research-api` uit
`docker-compose.yml` en zette `klai-focus/README.md` op FROZEN. De
directory bleef "voor historische referentie" in de tree. Tien dagen
later, toen SPEC-SEC-IDENTITY-ASSERT-001 Phase D `X-Caller-Service`
mandatory maakte op `retrieval-api /retrieve`, brak elke caller
silent. PR #311 (2026-05-05 hotfix) fixte 4 callers — waaronder
`klai-focus/research-api/app/services/retrieval_client.py`. De fix
was in de PR uitgevoerd ZONDER te checken dat `klai-core-research-api-1`
op core-01 niet draaide. Plus: `research-api` werd toegevoegd aan
`klai_identity_assert.KNOWN_CALLER_SERVICES` als allowlist-entry voor
een caller die niet bestaat. Dood materiaal verspilt review-tijd.

Discovered tijdens SPEC-DECOMM-FOCUS-001 (mei 2026) audit:
`docker ps` op core-01 → geen container; Caddy `/research/` 0 hits in
7 dagen; retrieval-api `_search_notebook` 0 logs in 24u. De directory
was effectief dood maar trok tóch defensieve fixes aan.

**Prevention (mechanisch, in volgorde):**

1. **Een decommission is pas af als de directory weg is.** "FROZEN"
   README + behouden tree is een halfslachtige staat die ALTIJD
   onderhoud blijft trekken. Een SPEC-PORTAL-UNIFY-KB-001-style
   decommission MOET in dezelfde of vervolg-PR `git rm -r <dir>/`
   doen — niet "later, als we tijd hebben".

2. **Pre-flight check bij sweep-PRs.** Voor elke PR die "alle X
   bijwerkt" (alle callers van een endpoint, alle services die een
   secret rouleren, alle FastAPI services met een middleware-fix):
   ```bash
   ssh core-01 "docker ps --format '{{.Names}}'" | sort > /tmp/live.txt
   git ls-tree -d HEAD '*/Dockerfile' | awk '{print $4}' | xargs -n1 dirname > /tmp/in-tree.txt
   comm -23 /tmp/in-tree.txt /tmp/live.txt
   ```
   Output = directories met een Dockerfile maar geen draaiende
   container. Skip die in je sweep, of merge er een delete-commit
   voor.

3. **Audit op decommission-residu na elke SPEC die "Phase X
   verwijdert service Y".** Check binnen 7 dagen na de SPEC:
   - Ghc.io image build workflow nog actief? (zou uit moeten staan)
   - Productie SOPS env vars nog aanwezig? (zou weg moeten zijn)
   - Allowlists nog include? (zou geschrapt moeten zijn)
   - Documentatie SERVERS.md / architecture.md nog "up"? (zou
     historisch moeten zijn)

   Als één van deze nog "live" is, is de decommission incompleet en
   trekt het volgende sweep-PR weer onnodig werk. Zie
   SPEC-DECOMM-FOCUS-001 voor het opruim-template.

4. **Comment-marker ipv FROZEN README.** Als een directory ECHT moet
   blijven (bv. wettelijk archief), zet een SessionStart-hook die bij
   elke `Edit` in de directory een waarschuwing toont. README's worden
   genegeerd door agents die op grep-resultaten werken.

## sync-env-removal-needs-explicit-confirmation (HIGH)
Sync-workflows die SOPS-decryption naar `/opt/klai/.env` doen MOETEN
een expliciete escape hatch hebben voor key-REMOVAL. Zonder die hatch
forceert elke decommission die SOPS-vars schrapt tot een handmatige
`sudo sed` op `/opt/klai/.env` — wat de SOPS-as-source-of-truth
invariant breekt en drift tussen SOPS en server creëert.

Achtergrond: `klai-infra/.github/workflows/sync-env.yml` had tot
mei 2026 de regel "abort if any keys would be REMOVED". Dat is een
verstandige default (voorkomt accidentele truncation), maar zonder
override-mechanisme dwingt het de operator om SOPS en server
verschillend te houden of het probleem op de server te omzeilen. Bij
SPEC-DECOMM-FOCUS-001 (mei 2026) leverde dit een drift-window: SOPS
had 176 lines (na verwijdering van `KUMA_TOKEN_RESEARCH_API` +
`RESEARCH_API_ZITADEL_AUDIENCE`), `/opt/klai/.env` had nog 178. De
auto-sync weigerde te draaien tot de operator handmatig sed'de.

**Prevention (mechanisch, opgelost in klai-infra#6):**

`workflow_dispatch.inputs.allow_removal` toegevoegd aan sync-env.yml
met een getypte string confirmation:

```yaml
workflow_dispatch:
  inputs:
    allow_removal:
      description: 'Acknowledge intentional key removal (typed confirmation required: "I-CONFIRM-REMOVAL")'
      required: false
      type: string
      default: ''
```

In de check:

```bash
if [ -n "$REMOVED" ]; then
  if [ "${{ github.event_name }}" = "workflow_dispatch" ] \
     && [ "${{ inputs.allow_removal }}" = "I-CONFIRM-REMOVAL" ]; then
    echo "::warning::Keys REMOVED with explicit confirmation: $REMOVED"
  else
    echo "::error::Keys would be REMOVED: $REMOVED — refusing automatic deploy."
    exit 1
  fi
fi
```

**Waarom een typed string en geen boolean:** een boolean input zit
voor-aangevinkt of standaard "false" in de "Run workflow" UI van
GitHub. Een operator die snel doorklikt vinkt 'm per ongeluk aan.
Een getypte string `I-CONFIRM-REMOVAL` dwingt expliciete
intentioneel typen — geen accidental click-through mogelijk.

**Gebruik bij decommission:**
```bash
gh workflow run sync-env.yml -f allow_removal=I-CONFIRM-REMOVAL
```

**Pattern voor andere sync workflows:** elke workflow die keys/files/
configs verwijdert van een productie-systeem heeft een variant van
deze guard nodig — refuse-by-default + getypte-string-override-input.
Niet alleen voor SOPS env: ook voor migrations die DROP TABLE doen,
voor terraform destroy, voor docker prune in CI. De pattern is
generic: destructieve ops moeten een getypte intent-bewijs hebben.

## alembic-multi-pr-head-split (CRIT)

When two PRs each add an Alembic migration with the same `down_revision`,
both PR builds pass green (each migration in isolation is valid) but
production crashes on `alembic upgrade head` with:

```
Multiple head revisions are present for given argument 'head'
```

The merge-first PR lands cleanly. The second-merging PR turns the chain
into two heads. `alembic upgrade head` refuses to proceed because the
target is ambiguous; the entrypoint loops on `FAILED` and the container
restartloops.

Klai hit this once on 2026-05-06:

1. PR #440 (SPEC-INGEST-RECONCILE-001) added `0005_crawl_jobs_fetch_outcomes`
   chained on `603787256fb8`.
2. PR #441 (SPEC-INGEST-LOGIN-WALL-DETECT-002) added `0005_crawled_pages_simhash`
   chained on the same `603787256fb8`.

#440 merged 7 minutes before #441. Production deployed `:latest` after
#441 and the knowledge-ingest container restartlooped for ~5 minutes.
The recovery used both available remediations in parallel: hotfix #442
rebased the second migration onto the first (`down_revision`:
`603787256fb8` → `a8c5e1d2f3b4`) AND hotfix #443 shipped a no-op merge
migration declaring both heads as parents. Either alone would have
unblocked the entrypoint; doing both is safe (one becomes redundant).

The schema columns themselves were independent (`crawl_jobs.fetch_outcomes`
vs `crawled_pages.content_simhash` — different tables) so no data
corruption happened; only the alembic graph needed linearisation.

**Prevention (process):**

1. **Before merging an alembic migration, rebase if main has moved.** A
   PR built on yesterday's `main` whose chain still references the
   yesterday-head is NOT mergeable today if another migration landed on
   the same parent. Treat alembic as a serial resource: the moment a
   migration lands on main, ALL other open PRs with an alembic
   migration are stale.

2. **Run `alembic heads` in CI on every PR build.** The check is offline
   (no DB needed) and catches the head split at PR-build time, not at
   container-start time. Add to the existing build-push workflow:
   ```yaml
   - name: Verify single alembic head
     run: |
       cd klai-knowledge-ingest
       heads=$(alembic heads | wc -l)
       if [ "$heads" -ne 1 ]; then
         echo "::error::Multiple alembic heads detected (got $heads, expected 1)"
         alembic heads
         exit 1
       fi
   ```
   This is the only protection that doesn't depend on developer
   discipline. Same pattern applies to all 4 services with alembic
   migrations: knowledge-ingest, connector, portal-api, scribe.

3. **When the head split DOES land in production**, two equally valid
   recoveries:
   - **Rebase** the second-merging migration onto the first head
     (preferred when the migration files are still recent and rename
     is cheap). What #442 did.
   - **Merge migration** that declares both heads as parents with a
     no-op body. What #443 did. Useful when the rebase would require
     coordinating across teams or downgrade safety matters.

   Both are safe and additive. Doing both at once is also safe (one
   becomes redundant); pick one.

4. **The compose-up restart loop is the loud signal.** If a
   `:latest`-image deploy puts a container in `Restarting` state with
   `failed: alembic upgrade head` in logs, the head-split is the most
   common cause for any service with > 1 alembic migration in flight.
   Check `alembic heads` first before debugging any other symptom.

Reference: PR #441 → crashloop → hotfix #442 (rebase) + #443 (merge
migration). Operator timeline: 5 minutes from deploy to crashloop, 13
minutes from crashloop to recovered deploy.

## docker-cp-not-a-deploy-mechanism (HIGH)

`docker cp` from a developer laptop into a running production container
is fine for **debugging-iteration speed** — apply a candidate fix,
restart, observe logs, refine. It is **NOT a deploy mechanism**, and
treating it as one introduces a class of silent regression that this
codebase already paid for during the SPEC-MCP-AUTH-001 rollout
(2026-05-07).

### What goes wrong

A `docker cp` writes to the container's writable layer, not to a Docker
volume or to the source repo. The container restart re-reads the file
from that writable layer, so an interactive `docker restart` preserves
the patch. But `docker compose up`, `docker compose down/up`, image
rebuild, and most orchestrator-driven container recreates **discard the
writable layer** and rebuild the container from the image. The patched
file vanishes.

If you are the developer iterating, you notice immediately and re-apply.
If the container is recreated by an unrelated path — Coolify health
check, scheduled image refresh, another deploy that triggers a
compose-up — the fix evaporates without anyone editing it back. Live
behavior silently regresses to pre-patch.

### What we observed

During 2026-05-07 OAuth-rollout debugging, three hot-patches disappeared
between iterations:
1. `klai-portal/backend/app/middleware/session.py` lost the `/oauth/`
   CSRF exempt entry that had been `docker cp`'d earlier in the day.
2. `klai-libs/identity-assert/.../mcp_token_client.py` reverted its
   Authorization-header fix.
3. `alembic/versions/post_deploy_9f4e2c8a1b7d.sql` was overwritten back
   to the direct-cast RLS pattern.

A drift-check (`shasum -a 256 <local> ; ssh core-01 docker exec <ctr>
sha256sum <path>`) caught all three. Without that check, the OAuth
flow would have started failing the moment the container next
recreated — minutes-to-hours after Mark stopped looking.

### Rule

- **Permanent fix**: branch + commit + PR + CI rebuild + image redeploy.
  No docker cp. The Docker image is the only durable artifact.
- **Iteration during debugging**: docker cp is fine, BUT every iteration
  also runs the drift-check before declaring "live confirmed". Without
  the check, "I tested it on prod" means nothing past the next compose-up.
- **End of debugging session**: assert worktree-≡-container parity for
  every file you patched. Any drift = unfinished work. Either finish
  applying the patch (re-cp + restart) or rebuild the image from the
  branch.

### Drift-check snippet

```bash
files=(...)  # your hot-patched paths
for f in "${files[@]}"; do
  l=$(shasum -a 256 "$f" | awk '{print $1}')
  r=$(ssh core-01 "docker exec <container> sha256sum /repo/$f" | awk '{print $1}')
  [ "$l" = "$r" ] && echo "OK $f" || echo "DRIFT $f"
done
```

Run it after every `docker restart` during a debugging session, and
before signing off. A single DRIFT line is the difference between
"committed and live" and "live until something restarts the container".

Reference: SPEC-MCP-AUTH-001 ops timeline 2026-05-07.
