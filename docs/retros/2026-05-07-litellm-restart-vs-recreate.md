# 2026-05-07 — `docker compose restart` vs `up -d` LiteLLM crashloop retro

**Pitfalls (now live in):**
- `.claude/rules/klai/pitfalls/process-rules.md` § `docker-compose-restart-vs-recreate`
- `.claude/rules/klai/infra/deploy.md` (cross-reference)

**Severity:** MEDIUM (production outage on path A — LibreChat — for ~15 min; paths B/C unaffected; auto-recovered by hotfix; no data loss)

**PRs / commits involved:**
- [#472](https://github.com/GetKlai/klai/pull/472) — Phase 4 ship that introduced the new mount (caused crashloop)
- [#474](https://github.com/GetKlai/klai/pull/474) — emergency hotfix (inlined the constant to remove the new-mount dependency)
- [#475](https://github.com/GetKlai/klai/pull/475) — structural cleanup (fixed the deploy workflow + un-inlined the constant)

## What happened

Phase 4 of SPEC-RAG-MULTILINGUAL-CHAT-001 added a vendored single-file copy
of `klai-libs/chat-prompts` at `deploy/litellm/klai_chat_prompts.py`,
following the exact same pattern used for `klai_service_auth.py` in
SPEC-SEC-SERVICE-AUTH-001 Phase C-1:

1. Add the file to `deploy/litellm/`
2. Add a bind-mount entry in `deploy/docker-compose.yml`
3. Add an `import` in `klai_knowledge.py`

Locally everything worked (pytest puts `deploy/litellm` on `sys.path`).
The drift test passed. CI was green. PR #472 merged at 08:44 UTC.

About 5 minutes later the LiteLLM container was crashlooping with
`ImportError: Could not import klai_knowledge_hook from klai_knowledge`.
All LibreChat (path A) chat completions hung in "Stop generating" state —
the LiteLLM proxy was unavailable, but LibreChat's frontend never showed
an error because its agent backend kept the SSE stream open waiting for
tokens that never came.

## Root cause

`.github/workflows/litellm-hook-deploy.yml` executed
`docker compose restart litellm`. The `restart` subcommand restarts the
running container with its **existing** configuration — it does NOT
re-read `docker-compose.yml`. New volume mounts, new env-vars, new image
tags, all silently ignored. This is documented Compose behaviour, not a
bug, but the surface area is invisible from the workflow file alone.

So:
- New `klai_knowledge.py` (with `from klai_chat_prompts import …`)
  landed in the container via the existing bind-mount of `klai_knowledge.py`. ✅
- New `klai_chat_prompts.py` bind-mount was **not** applied — the
  container kept its previous mount config. ❌
- Container started, Python tried to import `klai_chat_prompts`, file
  not at `/app/klai_chat_prompts.py` → ImportError → exit → crashloop.

The deploy workflow itself reported `Container Started` because
`docker compose restart` exits non-zero only when the daemon refuses the
restart, not when the application inside the container fails to boot.

## How we discovered it

1. End-to-end test attempt via Playwright sent a German query to
   LibreChat. POST returned 200, SSE stream opened, but no tokens.
2. VictoriaLogs query `service:litellm AND _time:5m` showed
   `ImportError: Could not import klai_knowledge_hook from klai_knowledge`
   followed by `ERROR: Application startup failed. Exiting.` repeating.
3. Deploy workflow log showed `docker compose restart litellm` —
   immediately suspicious because every other klai service-deploy uses
   `compose-up.sh` instead. `litellm-hook-deploy.yml` was the lone outlier.

## Hotfix (PR #474)

Inlined `GROUNDED_CHAT_SYSTEM_PROMPT` directly in `klai_knowledge.py`,
removing the cross-file import. The hot path went back to having zero
new filesystem dependencies, so `restart` was sufficient again.
Container recreated cleanly at 08:59:49 UTC. Total path-A downtime ~15
min.

## Structural cleanup (PR #475)

Three paired changes:

1. `litellm-hook-deploy.yml` switched from
   `docker compose restart litellm` → `/opt/klai/scripts/compose-up.sh
   litellm`. This is the canonical wrapper from
   SPEC-INFRA-CONTAINER-HYGIENE-001 REQ-3 that every other klai
   service-deploy already used. Compose-up.sh internally does
   `docker compose up -d --remove-orphans <svc>` which detects
   compose-file diffs and recreates only when needed.
2. `klai_knowledge.py` un-inlined back to
   `from klai_chat_prompts import GROUNDED_CHAT_SYSTEM_PROMPT`. Three
   copies of the constant collapsed back to two: canonical
   (`klai-libs/chat-prompts/klai_chat_prompts/__init__.py`) + vendored
   (`deploy/litellm/klai_chat_prompts.py`). Drift between them stays
   enforced by `test_klai_chat_prompts_drift.py`.
3. Lint script `GROUNDED_ALLOWED` regex no longer permits
   `deploy/litellm/klai_knowledge.py` — any future re-introduction of
   the inline constant will fail CI.

The cleanup PR's own deploy run (`Deploy LiteLLM hooks` workflow firing
on its own change) was the validation: it self-validated that the
`compose-up.sh` switch works.

## Lessons

### Lesson 1: `restart` is not equivalent to `up -d` for compose changes

**Pattern:** `docker compose restart <svc>` keeps the existing container
config — it ignores any drift between the running container and the
current `docker-compose.yml` / `.env`. Use `docker compose up -d <svc>`
(or the canonical `/opt/klai/scripts/compose-up.sh` wrapper) so Compose
recreates the container when its definition has changed.

**Where it bites:**
- New volume mounts (today's bug)
- New env-vars (mentioned by the existing comment block in
  `deploy-compose.yml` as the reason Grafana uses `up -d`)
- Image tag changes (would silently keep running the old image)
- Compose-level network/dns changes

**Detection rule:** every klai service-deploy workflow MUST use
`/opt/klai/scripts/compose-up.sh <svc>` and never inline `docker compose
restart <svc>`. The new pitfall entry adds:
```
grep -L 'compose-up.sh' .github/workflows/*.yml | \
  xargs grep -l 'docker compose'
```
as the canary check (any service-deploy that does compose ops without
the wrapper is suspect).

### Lesson 2: vendored single-files are zero-cost in tests, real-cost in deploys

**Pattern:** vendoring a file via Compose bind-mount is invisible to
unit tests (pytest's sys.path lookup just finds the file) but adds a
real deploy-pipeline dependency: every deploy workflow that touches
the affected service MUST recreate the container. If the workflow does
`restart` instead, the new mount silently does not land.

**Mitigation today:** structural fix in PR #475 (workflow uses
`compose-up.sh`).

**Mitigation Phase D (separate SPEC):** replace BOTH vendored
single-files (`klai_service_auth.py`, `klai_chat_prompts.py`) with a
custom litellm Dockerfile that `pip install`s `klai-service-auth` and
`klai-chat-prompts`. Once those dependencies live in the image instead
of as bind-mounts, the deploy workflow only needs to repull the image
tag — no compose-config diff, no recreate-vs-restart concern.

### Lesson 3: deploy workflow consistency matters

**Symptom:** `litellm-hook-deploy.yml` was the lone service-deploy
workflow using `restart`. Six other workflows (caddy, klai-connector,
klai-mailer, knowledge-ingest, klai-knowledge-mcp, docs-app) already
used `compose-up.sh`. The inconsistency was invisible to per-service
review (each workflow looks fine in isolation) but blew up the moment
a Compose-config change actually mattered.

**Mitigation:** SPEC-INFRA-CONTAINER-HYGIENE-001 already mandates
`compose-up.sh` for klasse-A (compose-managed) services. Today's bug
revealed that one workflow had been missed during the rollout. Going
forward, any net-new service-deploy workflow added must reference
SPEC-INFRA-CONTAINER-HYGIENE-001 REQ-3 in its body so the convention
is unmissable.

## Cost

- Production path-A downtime: ~15 min (08:44 → 08:59 UTC)
- Diagnostics via VictoriaLogs MCP: ~2 min once we knew to look there
- Hotfix PR #474 author + admin-merge: ~10 min
- Structural cleanup PR #475 (fix workflow + un-inline): ~25 min
- Total session impact: ~1 hour incl. retro doc

## What worked

- VictoriaLogs MCP was the fast path to root cause. `service:litellm
  AND _time:5m` gave the ImportError stack trace in one query.
- Worktree-based hotfix kept the main repo stable and let the structural
  cleanup PR be a clean follow-up rather than a single sprawling patch.
- The existing `compose-up.sh` wrapper meant the cleanup was a one-line
  workflow change, not a green-field rewrite. SPEC-INFRA-CONTAINER-HYGIENE-001
  paid off.
- The Phase D follow-up plan was already documented in the
  `klai_service_auth.py` docstring, so the structural mitigation
  direction was clear before the incident.

## What didn't work

- Local CI didn't catch the `restart`-vs-`up -d` gap because the import
  works in pytest. There's no test that exercises the actual production
  deploy path. Adding such a test (e.g., a CI job that does
  `docker compose -f deploy/docker-compose.yml run litellm python -c
  "import klai_knowledge"`) would have caught it. Filed as Phase D
  scope.
- The 4-place rationale comment ("Phase D plan: replace with pip
  install") in the `klai_chat_prompts.py` docstring + SPEC-text was not
  enough to prevent the bug — that comment told FUTURE readers what to
  do, but nothing prevented PRESENT-me from shipping the bind-mount
  pattern that the comment was warning against. Documentation is not a
  control.

## Follow-ups

- (now landing) `.claude/rules/klai/pitfalls/process-rules.md` —
  CRIT-level entry for `docker-compose-restart-vs-recreate`.
- (Phase D, separate SPEC, deferred) Custom litellm Dockerfile that
  `pip install`s `klai-service-auth` + `klai-chat-prompts` +
  `lingua-language-detector`. Closes both vendored-single-file
  patterns and unblocks SPEC-RAG-MULTILINGUAL-CHAT-001 REQ-10.4
  (path-A `chat_synthesis_complete` telemetry).
- (Phase D, related) Remove the legacy `X-Internal-Secret` auth path
  per SPEC-SEC-SERVICE-AUTH-001 REQ-5.
