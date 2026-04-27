---
id: SPEC-KB-YOUTUBE-REMOVE-001
version: 1.0.0
status: in-progress
created: 2026-04-27
updated: 2026-04-27
author: Mark Vletter
priority: medium
supersedes: SPEC-KB-SOURCES-001 (YouTube portion only)
---

# SPEC-KB-YOUTUBE-REMOVE-001: Remove YouTube ingest path from portal-api

## HISTORY

### v1.0.0 (2026-04-27) — initial

YouTube ingest is dead. SPEC-KB-SOURCES-001 v1.4.0 disabled the UI tile,
v1.5.0 removed the tile entirely. The backend route + extractor + 51
tests stayed live as "single-PR restore" — but YouTube has not unblocked
core-01's datacenter IP and is not going to. The optional residential
proxy was never configured in production, so every real call returns
HTTP 502 ``Could not reach YouTube — try again``.

This SPEC removes the dead backend code and the ``yt-dlp`` dependency.
That also removes the noisy Trivy false-positive findings against
``yt_dlp/extractor/*.py`` as a side effect (PR #183 worked around the
symptom; this SPEC fixes the cause).

---

## Goal

Delete every line of YouTube-ingest code from portal-api. Free the
``yt-dlp`` dependency. Leave a 410 Gone-style response on the route
URL for any caller that hard-coded it, with a one-line message that
points at the URL ingest path or Scribe transcription. No silent 404 —
existing callers must learn the route is gone, not appear to succeed.

---

## Success Criteria

- ``klai-portal/backend/app/services/source_extractors/youtube.py``
  is deleted.
- ``klai-portal/backend/tests/test_source_extractors_youtube.py`` is
  deleted.
- ``yt-dlp`` is removed from ``pyproject.toml`` dependencies.
- ``settings.youtube_proxy_url`` is removed from ``app/core/config.py``;
  the corresponding ``YOUTUBE_PROXY_URL`` env entry is removed from
  ``deploy/docker-compose.yml`` (klai-infra owns SOPS, but the compose
  env block lives in this repo).
- ``POST /api/app/knowledge-bases/{kb_slug}/sources/youtube`` returns
  HTTP 410 ``{"detail": "youtube_ingest_removed"}`` with a structlog
  warning event ``youtube_ingest_called_after_removal`` so we can spot
  any forgotten caller in production. The handler does NOT pull
  ``yt-dlp`` at import time.
- ``YouTubeSourceRequest`` Pydantic model and the ``extract_youtube``
  import are gone from ``app/api/app_knowledge_sources.py``.
- ``app/services/source_extractors/__init__.py`` no longer re-exports
  YouTube symbols.
- Trivy ``scan`` job on portal-api still passes after the removal —
  with ``yt-dlp`` gone the secret-scanner false positives disappear,
  but PR #183's ``scanners: 'vuln'`` config stays in place as
  documented policy (we already decided not to re-enable secret
  scanning on built images).
- Frontend already does not surface YouTube (SPEC-KB-SOURCES-001 v1.5.0)
  — no frontend change in this SPEC.

---

## Environment

- **Service:** klai-portal/backend (Python 3.13, FastAPI)
- **Files removed:**
  - [klai-portal/backend/app/services/source_extractors/youtube.py](../../../klai-portal/backend/app/services/source_extractors/youtube.py)
  - [klai-portal/backend/tests/test_source_extractors_youtube.py](../../../klai-portal/backend/tests/test_source_extractors_youtube.py)
- **Files modified:**
  - [klai-portal/backend/app/api/app_knowledge_sources.py](../../../klai-portal/backend/app/api/app_knowledge_sources.py)
    — drop ``YouTubeSourceRequest``, replace ``add_youtube_source`` with
    a 410 stub.
  - [klai-portal/backend/app/services/source_extractors/__init__.py](../../../klai-portal/backend/app/services/source_extractors/__init__.py)
    — drop YouTube exports.
  - [klai-portal/backend/app/core/config.py](../../../klai-portal/backend/app/core/config.py)
    — drop ``youtube_proxy_url`` setting.
  - [klai-portal/backend/pyproject.toml](../../../klai-portal/backend/pyproject.toml)
    — drop ``yt-dlp`` dep.
  - [klai-portal/backend/tests/test_app_knowledge_sources.py](../../../klai-portal/backend/tests/test_app_knowledge_sources.py)
    — drop YouTube-route tests, add a 410 regression test.
  - [deploy/docker-compose.yml](../../../deploy/docker-compose.yml) —
    drop ``YOUTUBE_PROXY_URL`` from the portal-api environment block.

## Out of Scope

- Frontend cleanup — already done in SPEC-KB-SOURCES-001 v1.5.0.
- Removing ``YOUTUBE_PROXY_URL`` from the prod ``.env.sops`` (klai-infra
  repo). The variable becomes orphaned env after this SPEC; that's a
  follow-up SOPS-cleanup PR in klai-infra and does not block portal-api.
- Reverting PR #183's ``scanners: 'vuln'`` config — the policy of "no
  built-image secret scanning" stands regardless. yt-dlp removal makes
  it unnecessary for THIS lib, but the rationale documented in
  ``.claude/rules/klai/infra/deploy.md`` covers any future similar lib.

---

## Threat Model

This is a removal, not a new feature. Two operational risks worth
calling out:

### Risk 1: An external caller still has the route URL hard-coded

Possible callers: a developer's curl script, a Postman collection, an
old internal browser tab. After deploy, those calls return HTTP 410.
The structlog event ``youtube_ingest_called_after_removal`` makes
forgotten callers visible in VictoriaLogs without breaking them
silently.

### Risk 2: Tests still import the deleted module

Caught by ``ruff check`` + ``pytest`` collection — both fail loudly
when an import resolves to nothing. Pre-merge CI gates this.

---

## Requirements

### REQ-1: Delete the extractor module and its tests

- **REQ-1.1:** ``app/services/source_extractors/youtube.py`` SHALL be
  deleted in full. No file remnant, no commented-out body.
- **REQ-1.2:** ``tests/test_source_extractors_youtube.py`` SHALL be
  deleted in full.
- **REQ-1.3:** ``app/services/source_extractors/__init__.py`` SHALL no
  longer import or re-export ``extract_youtube`` (or any other
  YouTube-specific symbol).

### REQ-2: Drop the dependency and its config knob

- **REQ-2.1:** ``yt-dlp`` SHALL be removed from
  ``klai-portal/backend/pyproject.toml`` dependencies. ``uv.lock``
  SHALL be regenerated to drop it transitively.
- **REQ-2.2:** ``settings.youtube_proxy_url`` SHALL be removed from
  ``app/core/config.py``. Any code path referencing it must be removed
  in the same change (CI's ``ruff`` ``F821`` catches stragglers).
- **REQ-2.3:** ``YOUTUBE_PROXY_URL: ${YOUTUBE_PROXY_URL}`` SHALL be
  removed from the portal-api ``environment:`` block in
  ``deploy/docker-compose.yml``. The SOPS entry in klai-infra is
  out-of-scope (orphan env after this PR; cleaned up separately).

### REQ-3: 410 Gone on the old route

- **REQ-3.1:** ``POST /api/app/knowledge-bases/{kb_slug}/sources/youtube``
  SHALL still resolve, return HTTP 410, body
  ``{"detail": "youtube_ingest_removed"}``. Auth + RLS context loading
  MUST NOT change — this is a thin handler that emits a log event and
  returns the error.
- **REQ-3.2:** The handler SHALL emit ONE structlog event
  ``event="youtube_ingest_called_after_removal"`` with fields
  ``org_id``, ``kb_slug``, ``user_agent`` (from request headers), so
  forgotten callers surface in VictoriaLogs without breaking them
  silently.
- **REQ-3.3:** The handler SHALL NOT import ``yt_dlp`` directly or
  transitively. Verified by ``test_app_knowledge_sources.py``: a
  collect-only test that asserts ``yt_dlp`` is not in
  ``sys.modules`` after importing ``app.api.app_knowledge_sources``.

### REQ-4: Pydantic model cleanup

- **REQ-4.1:** ``YouTubeSourceRequest`` Pydantic class SHALL be deleted
  from ``app/api/app_knowledge_sources.py``. The 410 handler does not
  need a body model — it accepts any body and returns the same 410.

### REQ-5: Tests reflect the new state

- **REQ-5.1:** A new test SHALL verify the 410 contract: an
  authenticated user POSTing to the YouTube route gets HTTP 410 with
  the documented body, and the structlog event is emitted.
- **REQ-5.2:** The ``test_source_extractors_ssrf`` and
  ``test_rls_callsite_audit`` test suites SHALL still pass — they are
  the only adjacent suites that referenced the YouTube extractor.

---

## Non-Functional Requirements

- **Backward compatibility:** External hard-coded callers of the route
  (none expected — the route was internal) get HTTP 410 with a stable
  ``detail`` value. No silent 404, no data loss because no data is
  ingested.
- **Image size:** removing ``yt-dlp`` (~30 MB) is a small but real
  shrink to the portal-api Docker image — measurable improvement to
  cold-start times on container restart.
- **Trivy:** the 30 false-positive GitHub Security alerts in
  ``yt_dlp/extractor/*.py`` will close automatically once the next
  scan does not find the files. PR #183's ``scanners: 'vuln'`` config
  stays in place as policy.

---

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| A handler imports ``yt_dlp`` lazily and that import becomes a runtime error after removal | REQ-3.3's ``sys.modules`` test covers the only known import site. ``ruff`` ``F821`` catches any other dangling reference. |
| ``settings.youtube_proxy_url`` is referenced in a code path I missed | ``ruff F821`` + ``pyright`` + the existing test suite catch this. The same removal pattern was applied for SPEC-SEC-IDENTITY-ASSERT-001 cleanup with no surprises. |
| An external caller hard-coded the route | HTTP 410 with stable ``detail`` + structlog event. Anyone using the route discovers it within one request. |
| The compose env entry stays orphaned in SOPS | Acknowledged out-of-scope. klai-infra cleanup PR scheduled separately; the variable just sits idle. |

---

## Cross-references

- Original SPEC: [SPEC-KB-SOURCES-001](../SPEC-KB-SOURCES-001/spec.md)
  v1.5.0 — frontend already removed YouTube tile.
- Trivy work-around: [PR #183](https://github.com/GetKlai/klai/pull/183)
  — ``scanners: 'vuln'`` config in portal-api workflow. Stays in place
  after this SPEC.
- Deploy policy: [.claude/rules/klai/infra/deploy.md](../../../.claude/rules/klai/infra/deploy.md)
  — Trivy section documents why we don't re-enable secret scanning.
