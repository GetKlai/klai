---
id: SPEC-LITELLM-CUSTOM-IMAGE-001
version: "1.0"
status: draft
created: 2026-05-07
updated: 2026-05-07
author: Mark Vletter
priority: high
issue_number: 0
---

## HISTORY

| Version | Date | Author | Change |
|---------|------|--------|--------|
| 1.0 | 2026-05-07 | Mark Vletter | Initial draft. Triggered by 2026-05-07 LiteLLM crashloop incident (SPEC-RAG-MULTILINGUAL-CHAT-001 Phase 4 → hotfix → cleanup). Consolidates the previously-implicit "Phase D" plan from `klai_service_auth.py` + `klai_chat_prompts.py` docstrings + SPEC-SEC-SERVICE-AUTH-001 into a single concrete SPEC. Today's incident is forcing-evidence: the vendored single-file pattern is no longer "deferred best practice" — it's a structural pattern that has now bitten production once and will bite again every time someone changes the relevant deploy without doing a full container recreate. |

---

# SPEC-LITELLM-CUSTOM-IMAGE-001: Custom LiteLLM image, eliminate vendored single-files, unify telemetry

## Context

The `klai-core-litellm-1` container runs the upstream
`ghcr.io/berriai/litellm:v1.83.7-stable` image. Klai's customisations
(KB enrichment hook, JWT auth client, custom token router) live in
files mounted into the container via `docker-compose.yml` bind-mounts:

```
deploy/litellm/klai_knowledge.py     → /app/klai_knowledge.py
deploy/litellm/custom_router.py      → /app/custom_router.py
deploy/litellm/klai_service_auth.py  → /app/klai_service_auth.py
deploy/litellm/klai_chat_prompts.py  → /app/klai_chat_prompts.py
```

The two `klai_*.py` files are **vendored single-file copies** of
canonical libraries that live elsewhere in the monorepo:

| Vendored file | Canonical source | Drift test |
|---|---|---|
| `klai_service_auth.py` | `klai-libs/service-auth/klai_service_auth/client.py` | `test_klai_service_auth_drift.py` |
| `klai_chat_prompts.py` | `klai-libs/chat-prompts/klai_chat_prompts/__init__.py` | `test_klai_chat_prompts_drift.py` |

The vendoring pattern was introduced in SPEC-SEC-SERVICE-AUTH-001
Phase C-1 (2026-05-04) for `klai_service_auth.py`, then re-applied in
SPEC-RAG-MULTILINGUAL-CHAT-001 Phase 4 (2026-05-07) for
`klai_chat_prompts.py`. Both SPECs explicitly documented in their
docstrings that this is a **transitional pattern** — the long-term
plan was always to ship a custom litellm Dockerfile that
`pip install`s the canonical libraries.

### Forcing-evidence: 2026-05-07 incident

The transitional pattern bit production for the first time on
2026-05-07. SPEC-RAG-MULTILINGUAL-CHAT-001 Phase 4 added the
`klai_chat_prompts.py` mount + a corresponding
`from klai_chat_prompts import …` line in `klai_knowledge.py`. The
deploy workflow ran `docker compose restart litellm`, which silently
ignores new bind-mounts. The container kept its previous mount config,
the import failed, and the proxy crashlooped — taking down LibreChat
(path A) for ~15 minutes. Full retro:
`docs/retros/2026-05-07-litellm-restart-vs-recreate.md`.

PR #475 fixed the deploy-workflow side (`restart` → `compose-up.sh`
which uses `up -d --remove-orphans`, picking up new mounts
automatically). That closes the immediate hole, but the underlying
pattern remains: any future bind-mount addition is one wrong workflow
edit away from the same failure mode in another service. The
structural fix is to eliminate the bind-mounts entirely.

### Three problems this SPEC closes

1. **Vendored single-file maintenance burden.** Today: 2 files + 2
   drift tests + 2 docstrings explaining why vendoring exists + 2
   bind-mount entries in docker-compose.yml + 1 lint allowlist entry.
   Drift tests don't *prevent* drift, they just *detect* it after the
   fact. Anyone editing the canonical library MUST also edit the
   vendored copy or CI fails — that's an avoidable cognitive tax on
   every contributor to `klai-libs/service-auth` or
   `klai-libs/chat-prompts`.

2. **Path-A `chat_synthesis_complete` telemetry is blocked.**
   SPEC-RAG-MULTILINGUAL-CHAT-001 REQ-10.4 mandates that
   `klai_knowledge.py` emit the same `chat_synthesis_complete` log
   event as paths B (`partner_chat.py`) and C
   (`synthesis.py`). Today this is deferred (REQ-10.4 currently SHOULD
   not MUST) because the upstream litellm image does not bundle
   `lingua-language-detector`, and a partial emit (no
   `query_language_detected` / `response_language_detected`) gives
   limited operational value. Operators are blind to per-language
   correctness on the most user-visible chat path.

3. **Legacy `X-Internal-Secret` auth path** still exists in every
   receiver as a fall-through. SPEC-SEC-SERVICE-AUTH-001 REQ-5
   committed to removing it once all sender pairs migrate to Zitadel
   JWTs, but currently the LiteLLM hook still ships the legacy header
   as a fallback when the JWT path fails. That fallback is the
   primary reason the legacy receiver code remains. With a custom
   Dockerfile, we can build the JWT-only client into the image and
   remove the legacy fallback in one go. (This sub-scope is owned by
   SPEC-SEC-SERVICE-AUTH-001; this SPEC merely *unblocks* it.)

### Why now

- The vendored pattern bit production once. Anti-pattern documented in
  `.claude/rules/klai/pitfalls/process-rules.md §
  docker-compose-restart-vs-recreate`.
- The deploy-workflow fix (PR #475) is a prerequisite — without it,
  switching `docker-compose.yml` `image:` from upstream tag to custom
  klai-litellm tag would silently fail. With PR #475 merged, this
  SPEC can ship safely.
- All other klai services already have the custom-image + GHCR push
  pattern (klai-connector, klai-mailer, knowledge-ingest,
  klai-knowledge-mcp, retrieval-api). Copy-paste templates exist;
  net-new infrastructure for THIS service is a Dockerfile + workflow
  file, not a new pattern.

---

## Scope

### In scope

1. **Custom litellm Dockerfile** at `deploy/litellm/Dockerfile`.
   Extends `ghcr.io/berriai/litellm:v1.83.7-stable` with
   `pip install klai-service-auth klai-chat-prompts
   lingua-language-detector`. Pin upstream litellm version explicitly
   in the FROM line so version bumps go through PR review.

2. **GHCR push workflow** at
   `.github/workflows/litellm-image-build.yml` (or appended to the
   existing `litellm-hook-deploy.yml` as a new job). Builds + pushes
   `ghcr.io/getklai/klai-litellm:<commit-sha>` and `:latest`. Pattern
   matches `klai-connector.yml` / `klai-mailer.yml`.

3. **Container-import smoke-test** in CI. After the image builds,
   `docker run --rm <image> python -c "import klai_knowledge; from
   klai_chat_prompts import GROUNDED_CHAT_SYSTEM_PROMPT; from
   klai_service_auth import ZitadelTokenClient; from lingua import
   Language; print('OK')"`. This catches any "works in pytest, fails
   in container" gap regardless of cause — the failure mode that
   2026-05-07 hit and that pytest's `sys.path` lookup happens to
   mask.

4. **`docker-compose.yml` image swap.** `image:
   ghcr.io/berriai/litellm:v1.83.7-stable` → `image:
   ghcr.io/getklai/klai-litellm:v1.83.7-<klai-build-id>` (or
   `:latest` per the convention used by other klai services).

5. **Delete vendored single-files + their drift tests + bind-mounts.**
   - Remove `deploy/litellm/klai_service_auth.py`
   - Remove `deploy/litellm/klai_chat_prompts.py`
   - Remove `deploy/litellm/tests/test_klai_service_auth_drift.py`
   - Remove `deploy/litellm/tests/test_klai_chat_prompts_drift.py`
   - Remove the corresponding `volumes:` entries in
     `deploy/docker-compose.yml`
   - Update `scripts/lint-no-duplicate-chat-prompt.sh`
     `GROUNDED_ALLOWED` regex (no need to permit
     `deploy/litellm/klai_chat_prompts.py` anymore).

6. **Path-A `chat_synthesis_complete` emit.** Add an
   `async_post_call_success_hook` emit in `klai_knowledge.py` that
   matches paths B + C: `event=chat_synthesis_complete`,
   `service=litellm`, `query_language_detected`,
   `response_language_detected`, `language_correctness`,
   `response_length_chars`, `org_id`, `request_id`. Implement
   language detection via `lingua` (now installed in the image).
   Update SPEC-RAG-MULTILINGUAL-CHAT-001 REQ-10.4 from SHOULD back
   to MUST. Update AC-OBSERVABILITY scope to cover all three paths.
   Remove the "Path A telemetry caveat" section from
   `docs/runbooks/multilingual-chat-observability.md`.

7. **Migration ordering documentation.** SPEC text MUST cite PR #475
   as a prerequisite. The image-tag swap (item 4) is a
   docker-compose.yml change and depends on the deploy workflow
   handling compose-config diffs correctly. Without PR #475 in main,
   the image swap silently keeps running the old upstream image. The
   prerequisite is already met (PR #475 merged 2026-05-07), but
   future readers of this SPEC need the dependency stated.

### Out of scope

- Removing the legacy `X-Internal-Secret` auth path. That remains in
  SPEC-SEC-SERVICE-AUTH-001 REQ-5 ("Phase D" of that SPEC). This SPEC
  unblocks it (custom image makes the JWT-only client available
  without bind-mount fragility) but does NOT execute the removal.
- Migrating other services (portal-api, knowledge-ingest, etc.) to
  custom images. They already have custom images. This SPEC is
  specifically about closing the gap on the LiteLLM container.
- Building from a non-upstream litellm fork. We continue to consume
  the official `ghcr.io/berriai/litellm` image and only add a thin
  klai-libs install layer on top.
- Replacing `compose-up.sh` workflow patterns. SPEC-INFRA-CONTAINER-HYGIENE-001
  already mandates this pattern; the 2026-05-07 incident closed the
  one outlier (litellm-hook-deploy.yml).

---

## Requirements (EARS)

### REQ-LITELLM-CUSTOM-IMAGE-001-1 — Dockerfile builds successfully on CI

**When** a PR modifies any file under `deploy/litellm/Dockerfile` or
`klai-libs/{service-auth,chat-prompts}/`, **the system MUST**
build the `klai-litellm` image successfully on a clean GitHub Actions
runner, **and** the build MUST run a `docker run --rm <image> python
-c "<smoke-import-statement>"` step that exits 0.

### REQ-LITELLM-CUSTOM-IMAGE-001-2 — Image is pushed to GHCR with both `:latest` and `:<sha>` tags

**When** a commit lands on main that touches the Dockerfile or its
inputs, **the system MUST** push
`ghcr.io/getklai/klai-litellm:<commit-sha>` and update
`ghcr.io/getklai/klai-litellm:latest` to point at the same digest.
Build provenance MUST include the upstream litellm version (`FROM`
line) for forensic traceability.

### REQ-LITELLM-CUSTOM-IMAGE-001-3 — Vendored single-files are removed entirely

**When** this SPEC's PR lands on main, **the system MUST NOT**
contain:
- `deploy/litellm/klai_service_auth.py`
- `deploy/litellm/klai_chat_prompts.py`
- `deploy/litellm/tests/test_klai_service_auth_drift.py`
- `deploy/litellm/tests/test_klai_chat_prompts_drift.py`

The corresponding bind-mount entries in `deploy/docker-compose.yml`
MUST also be removed. The `scripts/lint-no-duplicate-chat-prompt.sh`
GROUNDED_ALLOWED regex MUST be tightened to remove the
`deploy/litellm/klai_chat_prompts\.py` entry.

### REQ-LITELLM-CUSTOM-IMAGE-001-4 — Production import path matches CI test path

**When** the CI smoke-test runs `docker run --rm <image> python -c
"import klai_knowledge; …"`, **it MUST exercise the same import
mechanism the production container uses.** No `sys.path` injection,
no pytest fixtures. Imports resolve via `pip install`-installed
packages inside the image.

This requirement is the structural answer to the 2026-05-07 incident:
unit tests passed because pytest puts `deploy/litellm` on `sys.path`,
but production failed because the runtime container had no such
fallback. After this SPEC, both paths use the same mechanism.

### REQ-LITELLM-CUSTOM-IMAGE-001-5 — Path-A `chat_synthesis_complete` emit lands

**When** the LiteLLM hook completes a LibreChat chat completion (path
A — `data["user"]` was present in the pre-call hook),
**`klai_knowledge.py` MUST** emit a `chat_synthesis_complete`
structured log event matching the schema documented in
`docs/runbooks/multilingual-chat-observability.md`:

```
event:                       "chat_synthesis_complete"
service:                     "litellm"
query_language_detected:     <lingua detection on user's last query>
response_language_detected:  <lingua detection on assembled response>
language_correctness:        <bool | null>
response_length_chars:       <int>
org_id:                      <str | null>  (extracted from team metadata)
request_id:                  <propagated upstream header or generated>
```

The emit MUST occur in `async_post_call_success_hook`. Failures of
the language-detection step (e.g., `lingua` raising on edge cases)
MUST NOT block the chat response — fallback is to emit the event
with `language_correctness: null`.

### REQ-LITELLM-CUSTOM-IMAGE-001-6 — SPEC-RAG-MULTILINGUAL-CHAT-001 REQ-10.4 is upgraded SHOULD → MUST

**When** this SPEC's PR lands, **the implementer MUST** open a
follow-up commit (or include in the same PR) that changes
`.moai/specs/SPEC-RAG-MULTILINGUAL-CHAT-001/spec.md`
REQ-RAG-MULTILINGUAL-CHAT-001-10 sub-clause 4 from SHOULD to MUST,
removing the "ship-level explicitly defers this sub-clause" caveat.
The corresponding AC-OBSERVABILITY scope-narrowing in `acceptance.md`
MUST also be reverted to cover paths A+B+C uniformly.

### REQ-LITELLM-CUSTOM-IMAGE-001-7 — Runbook caveat removal

**When** this SPEC's PR lands, **the file**
`docs/runbooks/multilingual-chat-observability.md` MUST have its
"Path A telemetry caveat (Phase 4 ship → Phase D close)" section
removed, and the table at the top updated to drop the "(planned —
see Path A telemetry caveat below)" qualifier on path A.

### REQ-LITELLM-CUSTOM-IMAGE-001-8 — Deploy workflow uses compose-up.sh

**When** the LiteLLM image is updated and the deploy fires, **the
deploy workflow MUST** use `/opt/klai/scripts/compose-up.sh litellm`
(already true post-PR #475 — this REQ pins the invariant). Direct
`docker compose restart litellm` invocations are FORBIDDEN per the
`docker-compose-restart-vs-recreate` pitfall in
`.claude/rules/klai/pitfalls/process-rules.md`.

---

## Migration phases

This SPEC ships in a single PR (no incremental rollout — the image
swap is atomic). Phases below describe the work order *within* that
PR, not separate ship moments.

### Phase A — Build infrastructure (before swap)

1. Add `deploy/litellm/Dockerfile`.
2. Add `.github/workflows/litellm-image-build.yml` (or append to the
   existing `litellm-hook-deploy.yml`).
3. Verify the workflow builds + pushes successfully on a feature
   branch (no swap yet — image just sits in GHCR unused).
4. Verify the smoke-test step (`docker run --rm <image> python -c
   "…"`) passes.

### Phase B — Image swap + bind-mount removal

1. `deploy/docker-compose.yml`: swap `image:`, remove the two
   vendored bind-mount entries, keep `klai_knowledge.py` and
   `custom_router.py` bind-mounts (those remain bind-mounted because
   they're klai-specific code, not vendored copies of pip-installable
   libraries).
2. Delete the two vendored `.py` files + their drift tests.
3. Update `scripts/lint-no-duplicate-chat-prompt.sh` GROUNDED_ALLOWED.
4. Update `klai_knowledge.py` to remove the docstring block about the
   vendored pattern (now obsolete).

### Phase C — Telemetry emit

1. Add `async_post_call_success_hook` emit logic with
   `lingua`-based language detection.
2. Extend `deploy/litellm/tests/test_klai_knowledge_hook.py` with
   tests for the emit shape (mocking `lingua` to return known
   languages).

### Phase D — Doc updates (closing the loop)

1. Update SPEC-RAG-MULTILINGUAL-CHAT-001 REQ-10.4 SHOULD → MUST.
2. Update AC-OBSERVABILITY scope.
3. Remove "Path A telemetry caveat" section from runbook.
4. Update CHAT-PATH-MAP table in runbook to drop the planned-qualifier.

### Phase E — Validation

1. Local: hook tests, drift tests (now removed), lint script.
2. CI: image build + smoke-test green.
3. Post-merge production: VictoriaLogs query
   `event:chat_synthesis_complete AND service:litellm` shows non-zero
   count after a Playwright DE/FR/PT/ES smoke-test run.

---

## Acceptance criteria

See `.moai/specs/SPEC-LITELLM-CUSTOM-IMAGE-001/acceptance.md` for the full
Given/When/Then breakdown across 5 test areas (image build pipeline,
vendored cleanup, path-A telemetry, deploy workflow invariant, end-to-end
production validation). High-level summary:

- **AC-IMG-BUILD / AC-IMG-PUSH / AC-SMOKE-IMPORT** — image builds + pushes
  to GHCR on every Dockerfile change; `docker run --rm <image> python -c
  "import …"` exits 0 in CI (the structural test that today's incident's
  pytest-only coverage missed).
- **AC-VENDORED-FILES-GONE / AC-DRIFT-TESTS-GONE / AC-COMPOSE-MOUNTS-GONE
  / AC-LINT-TIGHTENED / AC-CANONICAL-IMPORT-WORKS** — post-merge `find`
  shows zero `klai_service_auth.py` / `klai_chat_prompts.py` in
  `deploy/litellm/`; `klai_chat_prompts` resolves via
  `/usr/lib/python*/site-packages/`, NOT `/app/`.
- **AC-EMIT-FIRES-LIBRECHAT / AC-EMIT-FIELD-VALUES-DE /
  AC-EMIT-FALLBACK-ON-LINGUA-ERROR** — production
  `chat_synthesis_complete` events with `service:litellm` count > 0 within
  5s of a chat completion; field values match expected language-detection
  output; lingua errors don't break chat.
- **AC-REQ-10.4-UPGRADED / AC-AC-OBSERVABILITY-RESCOPED /
  AC-RUNBOOK-CAVEAT-REMOVED** — SPEC-RAG-MULTILINGUAL-CHAT-001 REQ-10.4
  reverted SHOULD → MUST; runbook caveat section removed.
- **AC-DEPLOY-USES-COMPOSE-UP / AC-CANARY-CHECK-CLEAN** — deploy
  workflow uses `compose-up.sh`; the canary command from
  `process-rules.md § docker-compose-restart-vs-recreate` returns zero
  non-canonical workflows.
- **AC-NO-REGRESSION-MULTILINGUAL / AC-CONTAINER-HEALTHY-POST-DEPLOY /
  AC-LANGUAGE-CORRECTNESS-RATE-OBSERVABLE** — 6-language Playwright
  smoke (DE/FR/PT/ES/NL/EN) on Voys tenant passes; VictoriaLogs shows
  clean startup; per-language correctness rate queryable in Grafana for
  all three paths.

---

## Risks

### Risk 1: Custom Dockerfile drifts from upstream litellm releases

**Impact:** klai's image stays on an old upstream version, missing
upstream security patches.

**Mitigation:** Pin the upstream tag in the FROM line. Add a
quarterly review item to bump the upstream tag. Existing
`deploy/check-image-pullable.sh` script already exercises the
`vexaai/*` and `ghcr.io/*` tag-pull pattern; extend it to verify our
own `ghcr.io/getklai/klai-litellm:<sha>` tags are pullable from
core-01.

### Risk 2: GHCR push fails silently, deploy uses stale image

**Mitigation:** Image-pullable check at workflow level (after push,
before deploy step). Pattern from `deploy-compose.yml` validate-tags
step.

### Risk 3: lingua-language-detector pulls in heavy ML deps

**Mitigation:** Pre-flight: `pip install lingua-language-detector` in
a clean container, measure image size delta. If > 200 MB, evaluate
alternatives (langdetect, fasttext-langdetect, pycld3). The python
`lingua-language-detector` is documented at ~2-5 MB for the rule-based
path; the model-bundled path is larger. Default install should be
sufficient for our 6 target languages.

### Risk 4: docker-compose.yml image-tag swap is reverted by accident

**Mitigation:** A linter check (added to deploy-compose.yml CI) that
fails if `image:` for litellm service references
`ghcr.io/berriai/litellm` directly instead of
`ghcr.io/getklai/klai-litellm`. Mirror the existing image-tag-pin
discipline from check-image-tags.sh.

---

## Cross-references

- **Triggering incident:** `docs/retros/2026-05-07-litellm-restart-vs-recreate.md`
- **New pitfall entry:** `.claude/rules/klai/pitfalls/process-rules.md § docker-compose-restart-vs-recreate`
- **Prerequisite:** PR #475 (workflow uses `compose-up.sh`) — already merged 2026-05-07
- **Closes deferred work in:**
  - SPEC-RAG-MULTILINGUAL-CHAT-001 REQ-10.4 (path-A telemetry deferred)
  - Implicit "Phase D" plan in `deploy/litellm/klai_service_auth.py` and `deploy/litellm/klai_chat_prompts.py` docstrings
- **Related (out of scope):** SPEC-SEC-SERVICE-AUTH-001 REQ-5 (legacy `X-Internal-Secret` removal) — this SPEC unblocks it but does not execute it.
- **Pattern reference:** see `klai-connector.yml`, `klai-mailer.yml`, `knowledge-ingest.yml` for the canonical custom-image + GHCR push + compose-up.sh deploy pattern.
