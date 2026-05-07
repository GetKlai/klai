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

# Acceptance — SPEC-LITELLM-CUSTOM-IMAGE-001

This document defines the acceptance criteria for SPEC-LITELLM-CUSTOM-IMAGE-001
("Custom LiteLLM image, eliminate vendored single-files, unify telemetry").
Each AC maps to one or more EARS requirements in `spec.md`.

## Test Area 1 — Image build pipeline (REQ-1, REQ-2, REQ-4)

### AC-IMG-BUILD — Dockerfile builds successfully on a clean CI runner

**Given** a feature branch contains `deploy/litellm/Dockerfile` with
`FROM ghcr.io/berriai/litellm:v1.83.7-stable` and a
`RUN pip install klai-service-auth klai-chat-prompts lingua-language-detector`
layer
**When** the GitHub Actions workflow `litellm-image-build.yml` runs on
that branch
**Then** the workflow's build step exits 0
**And** the resulting image is tagged
`ghcr.io/getklai/klai-litellm:<commit-sha>` in the local docker daemon

### AC-IMG-PUSH — Image is pushed to GHCR with both `:latest` and `:<sha>`

**Given** a commit lands on `main` that touches `deploy/litellm/Dockerfile`
or any of its inputs
**When** the build workflow completes successfully
**Then** `docker manifest inspect ghcr.io/getklai/klai-litellm:<sha>` exits 0
**And** `docker manifest inspect ghcr.io/getklai/klai-litellm:latest` resolves
to the same digest
**And** the image manifest contains a label
`klai.upstream-litellm-version=v1.83.7-stable` (or equivalent) so the upstream
version is forensically traceable

### AC-IMG-PULLABLE-FROM-CORE01 — Production host can pull the new image

**Given** a fresh image `ghcr.io/getklai/klai-litellm:<sha>` was pushed to GHCR
**When** the deploy workflow runs `compose-up.sh litellm` on core-01
**Then** `docker pull ghcr.io/getklai/klai-litellm:<sha>` exits 0
**And** the container that gets recreated by `compose-up.sh` is built from
that image (verified via `docker inspect klai-core-litellm-1 |
jq -r '.[0].Image'` showing the matching digest)

### AC-SMOKE-IMPORT — Production-shape smoke-test passes in CI

**Given** the image `ghcr.io/getklai/klai-litellm:<sha>` has been built
**When** the CI workflow runs:
```bash
docker run --rm \
  -e KNOWLEDGE_RETRIEVE_URL=http://stub \
  -e PORTAL_API_URL=http://stub \
  ghcr.io/getklai/klai-litellm:<sha> \
  python -c "
import klai_knowledge
from klai_chat_prompts import GROUNDED_CHAT_SYSTEM_PROMPT
from klai_service_auth import ZitadelTokenClient
from lingua import Language
print('OK')
"
```
**Then** the container exits 0
**And** stdout contains the literal string `OK`

This AC is the structural answer to the 2026-05-07 incident: the
import-resolution path is identical between CI test and production runtime.
No `sys.path` injection, no pytest fixtures.

## Test Area 2 — Vendored cleanup (REQ-3)

### AC-VENDORED-FILES-GONE — Vendored single-files no longer exist on main

**Given** this SPEC's PR has merged to main
**When** `find deploy/litellm -name "klai_service_auth.py" -o -name "klai_chat_prompts.py"`
is executed at the repo root
**Then** the output is empty (zero matches)

### AC-DRIFT-TESTS-GONE — Drift tests are removed

**Given** this SPEC's PR has merged
**When** `find deploy/litellm/tests -name "test_klai_*_drift.py"` is executed
**Then** the output is empty
**And** `pytest deploy/litellm/tests/` discovery does not error on missing
fixtures

### AC-COMPOSE-MOUNTS-GONE — docker-compose.yml drops the two vendored mounts

**Given** this SPEC's PR has merged
**When** the litellm service block in `deploy/docker-compose.yml` is inspected
**Then** there are NO `volumes:` entries containing `klai_service_auth.py` or
`klai_chat_prompts.py`
**And** the `klai_knowledge.py` and `custom_router.py` bind-mounts remain
(those are klai-specific code, not vendored copies)

### AC-LINT-TIGHTENED — GROUNDED_ALLOWED no longer permits the vendored path

**Given** this SPEC's PR has merged
**When** `scripts/lint-no-duplicate-chat-prompt.sh` is read
**Then** the `GROUNDED_ALLOWED` regex does NOT contain
`deploy/litellm/klai_chat_prompts\.py`
**And** the script exits 0 against the post-merge tree

### AC-CANONICAL-IMPORT-WORKS — `klai_knowledge.py` imports from pip-installed package

**Given** the post-merge image is running in production
**When** the container starts
**Then** the `from klai_chat_prompts import GROUNDED_CHAT_SYSTEM_PROMPT`
statement in `klai_knowledge.py` resolves successfully
**And** the resolution chain is via `pip install`-installed
`klai_chat_prompts` package (verified by `docker exec klai-core-litellm-1
python -c "import klai_chat_prompts; print(klai_chat_prompts.__file__)"`
returning a path under `/usr/lib/python*/site-packages/`, NOT under `/app/`)

## Test Area 3 — Path-A `chat_synthesis_complete` telemetry (REQ-5, REQ-6, REQ-7)

### AC-EMIT-FIRES-LIBRECHAT — Path A emits the event after each chat completion

**Given** the new image is running in production
**And** a Voys-tenant LibreChat user submits a chat completion
**When** the LiteLLM hook completes the request via
`async_post_call_success_hook`
**Then** within 5 seconds, VictoriaLogs query
`event:chat_synthesis_complete AND service:litellm AND _time:1m`
returns at least 1 entry
**And** that entry contains all required fields:
`query_language_detected`, `response_language_detected`,
`language_correctness`, `response_length_chars`, `org_id`, `request_id`

### AC-EMIT-FIELD-VALUES-DE — Detected language fields are correct for a German query

**Given** the new image is running
**When** a user submits "Wie kann ich einen Anruf weiterleiten?" through
LibreChat and gets a response
**Then** the resulting `chat_synthesis_complete` event has:
- `query_language_detected: "de"`
- `response_language_detected: "de"`
- `language_correctness: true`

### AC-EMIT-FALLBACK-ON-LINGUA-ERROR — Detection failure does not break chat

**Given** an edge-case query that causes `lingua` to raise (e.g. extremely
short, mixed-script, or empty input)
**When** the hook tries to detect language
**Then** the chat response itself is NOT affected — the user still gets
their answer
**And** the `chat_synthesis_complete` event is still emitted with
`language_correctness: null` (and `query_language_detected`/
`response_language_detected` set to `null` if detection failed at that step)

### AC-REQ-10.4-UPGRADED — SPEC-RAG-MULTILINGUAL-CHAT-001 REQ-10.4 reverted to MUST

**Given** this SPEC's PR has merged
**When** `.moai/specs/SPEC-RAG-MULTILINGUAL-CHAT-001/spec.md`
REQ-RAG-MULTILINGUAL-CHAT-001-10 sub-clause 4 is read
**Then** the wording is `MUST emit` (not `SHOULD emit`)
**And** the deferral caveat block ("Phase 4 ship-level explicitly defers
this sub-clause: …") is removed

### AC-AC-OBSERVABILITY-RESCOPED — Acceptance doc covers paths A+B+C uniformly

**Given** this SPEC's PR has merged
**When** `.moai/specs/SPEC-RAG-MULTILINGUAL-CHAT-001/acceptance.md`
AC-OBSERVABILITY is read
**Then** it does NOT contain the "Path A scope" deferral note
**And** the criterion applies uniformly to events from `service:litellm`,
`service:portal-api`, and `service:retrieval-api`

### AC-RUNBOOK-CAVEAT-REMOVED — Runbook drops the path-A deferral section

**Given** this SPEC's PR has merged
**When** `docs/runbooks/multilingual-chat-observability.md` is read
**Then** the section "Path A telemetry caveat (Phase 4 ship → Phase D close)"
is fully removed (no leftover heading, no leftover paragraphs)
**And** the chat-paths table at the top no longer contains the qualifier
"(planned — see Path A telemetry caveat below)" on path A's `service:` cell

## Test Area 4 — Deploy workflow invariant (REQ-8)

### AC-DEPLOY-USES-COMPOSE-UP — Deploy workflow uses the canonical wrapper

**Given** the post-merge state of the repo
**When** `.github/workflows/litellm-hook-deploy.yml` (or
`litellm-image-build.yml` if separated) is inspected
**Then** the recreate step calls `/opt/klai/scripts/compose-up.sh litellm`
**And** there is NO `docker compose restart litellm` invocation anywhere in
any service-deploy workflow

### AC-CANARY-CHECK-CLEAN — Process-rules canary returns zero non-canonical workflows

**Given** the post-merge state of the repo
**When** the following canary command from
`.claude/rules/klai/pitfalls/process-rules.md §
docker-compose-restart-vs-recreate` is executed:
```bash
for f in .github/workflows/*.yml; do
  grep -l 'docker compose' "$f" | grep -L 'compose-up.sh' || true
done
```
**Then** the output contains ONLY:
- `.github/workflows/deploy-compose.yml` (the workflow that INSTALLS
  compose-up.sh — using it would be circular)
- Any workflow whose `docker compose` invocations are read-only diagnostic
  (`docker compose config`, `docker compose exec` for ad-hoc queries)

No production-deploy workflow may appear here.

## Test Area 5 — End-to-end production validation

### AC-NO-REGRESSION-MULTILINGUAL — All 6 languages still work in production

**Given** the post-merge state with the new image deployed on core-01
**When** a Voys-tenant Playwright smoke runs the following 6 queries through
LibreChat (path A):

| Lang | Query |
|---|---|
| DE | "Wie kann ich einen Anruf weiterleiten in Voys?" |
| FR | "Comment puis-je transférer un appel actif vers un collègue?" |
| PT | "Como faço para encaminhar uma chamada ativa?" |
| ES | "¿Cómo puedo transferir una llamada activa a un compañero?" |
| NL | "Hoe stel ik een belplan in voor buiten openingstijden?" |
| EN | "How do I configure a dial plan for outside opening hours?" |

**Then** each response satisfies:
- Title (auto-generated by LibreChat from response) is in the target language
- TL;DR (or local equivalent — Samenvatting / Zusammenfassung / Résumé /
  Resumen / Resumo) is in the target language
- Response body content is in the target language
- 📎 source link points to the canonical NL Notion page (cross-lingual
  citation works)

This is the same matrix verified post-Phase 4 + post-hotfix on 2026-05-07;
re-running it post-Phase D ensures no regression from the image swap.

### AC-CONTAINER-HEALTHY-POST-DEPLOY — VictoriaLogs shows clean startup

**Given** `compose-up.sh litellm` has just recreated the container with the
new image
**When** VictoriaLogs query
`service:litellm AND _time:5m AND _msg:*startup*` is executed
**Then** at least one log entry contains
`INFO:     Application startup complete.`
**And** zero log entries contain
`ERROR:    Application startup failed`
**And** zero log entries contain
`ImportError: Could not import klai_knowledge_hook from klai_knowledge`

### AC-LANGUAGE-CORRECTNESS-RATE-OBSERVABLE — Operators can query the metric in Grafana

**Given** the new image has been running in production for at least 24h
**When** an operator opens Grafana → Explore → VictoriaLogs and runs:
```
event:chat_synthesis_complete
| stats by (service, query_language_detected, language_correctness) count() AS n
```
**Then** the result contains rows with `service:litellm` (path A) for each
of the 6 target languages
**And** the per-language `language_correctness=true` rate is computable
without missing data
**And** the runbook query `Per-tenant break-down (portal-api only)` still
works for path B (regression check that this SPEC didn't break path B
observability)

## Out of scope (explicitly NOT verified by this SPEC)

- Removal of legacy `X-Internal-Secret` auth path — owned by
  SPEC-SEC-SERVICE-AUTH-001 REQ-5. This SPEC unblocks it (custom image
  makes the JWT-only client available without bind-mount fragility) but
  does NOT execute the removal. A follow-up PR against SEC-SERVICE-AUTH-001
  will address it.
- Migration of paths B and C to use the same custom image. They run inside
  different containers (portal-api, retrieval-api) which already have their
  own custom images and `pip install klai-chat-prompts` natively.
- Replacing the upstream litellm version. We continue to consume
  `ghcr.io/berriai/litellm:v1.83.7-stable` and add a thin pip-install layer.
  Upstream version bumps remain a separate decision.

## Notes for the implementer

- The pattern reference for the GHCR push workflow is
  `.github/workflows/klai-connector.yml` — copy-paste it and substitute
  service name + image-tag prefix.
- The `compose-up.sh` wrapper already does `docker compose pull <svc>` as
  step 1, so the deploy will repull the new image automatically once the
  GHCR push completes. No extra coordination needed between the build job
  and the deploy job — chain them via `needs:` in the same workflow file.
- `lingua-language-detector` PyPI package: install size is ~5 MB for the
  rule-based detector. If the bundled-models path is needed for accuracy on
  short queries, the install grows to ~80 MB. Per Risk 3 in spec.md,
  benchmark before committing to bundled-models.
- The path-A emit reuses the same field schema documented in the runbook —
  do NOT invent new field names. Operators have queries that depend on
  exact field names (`query_language_detected`, not `query_lang` or
  `detected_query_language`).
