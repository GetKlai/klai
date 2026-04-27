## SPEC-SEC-HYGIENE-001 — connector slice progress

Branch: `feature/SPEC-SEC-HYGIENE-001-connector` (worktree at
`C:/Users/markv/stack/02 - Voys/Code/klai-hygiene-connector`).
Branched from `origin/main` at `6b75922f`.

Slice scope: HY-30, HY-31, HY-32 — all in `klai-connector/`. Independent
of the scribe slice (`feature/SPEC-SEC-HYGIENE-001-scribe`); each ships
as its own PR.

Methodology: TDD per `.moai/config/sections/quality.yaml` (`development_mode: tdd`).
Each finding follows RED → GREEN → REFACTOR with the regression test
written first and confirmed failing against the pre-fix code.

---

### HY-30 — `HTTPException` NameError → 500 oracle  ✅
Commit: `10715d18`

- REQ-30.1: imported `HTTPException` in `routes/connectors.py:5`.
- REQ-30.2: 6 regression tests in `tests/test_connector_routes_not_found.py`
  cover GET/PUT/DELETE on a missing UUID + cross-tenant case (org A's
  JWT hitting org B's UUID returns 404, not 403/500). All 6 fail with
  500 against pre-fix code (verified) and pass post-fix.
- REQ-30.3: 2 contract tests in `tests/test_ruff_config.py` pin
  `select=["F", ...]` and ensure `F821` is not on the ignore list. Local
  `uv run ruff check` already flagged the original bug — the gap was a
  CI lint pass, not the pyproject config.
- REQ-30.4: audited every file in `app/routes/` via grep — `connectors.py`
  was the only offender. `deps.py`, `sync.py`, `health.py`, `fingerprint.py`
  all import what they use.

Tests: 8 new, all green. Pre-existing `SyncEngine._image_transport` failures
unchanged.

---

### HY-31 — `/api/v1/compute-fingerprint` rewired (Branch B)  ✅
Commit: `e4ddaa8b`

Branch B chosen (rewire) — feature is in active production use at
`klai-portal/backend/app/services/klai_connector_client.py:91` and
`klai-portal/backend/app/api/connectors.py:47`. Removal would have
shipped a feature break.

Verified the runtime behaviour pre-fix is even worse than the SPEC
text suggested: the original `logger.warning(...)` call inside the
exception handler would itself raise `TypeError` against stdlib Logger
(no kwargs), so the "Crawl failed: ModuleNotFoundError" leak prediction
never executed. Effective state was: silent 500 + portal swallows the
error + canary saved without protection.

- REQ-31.1 (b): rewired to crawl4ai HTTP API at `settings.crawl4ai_api_url`.
  Mirror of `knowledge_ingest.crawl4ai_client.crawl_page` (same
  PruningContentFilter, same chrome-strip excluded_tags, same
  on_page_context_created cookie hook). New helpers `_build_crawl_payload`,
  `_extract_markdown`, `_fetch_page_markdown`. Switched module-level
  logger from `app.core.logging.get_logger` to `structlog.get_logger`
  directly (the codebase helper returns stdlib).
- REQ-31.2: 502 detail collapsed to literal `"Crawl failed"`. Original
  exception only via `logger.exception` → structlog → VictoriaLogs.
- REQ-31.3: 11 regression tests in `tests/test_compute_fingerprint.py`
  cover all four contract paths (200 happy, 422 too-short, 502 generic,
  502 leak detection across 7 strings). Plus an AST-based static guard
  that rejects any future re-import of `app.adapters.webcrawler`.
- REQ-31.4: portal client unchanged — same REST contract, no caller-side
  migration needed.

Tests: 11 new, all green. Ruff clean on `routes/fingerprint.py`.

---

### HY-32 — Per-org Redis sliding-window rate limit  ✅
Commit: `e7efe1db`

- REQ-32.1 (b): app-layer Redis ZSET sliding window keyed on org_id
  (preferred over Caddy zone — keys on org_id rather than `{remote_host}`).
  New module `app/services/rate_limit.py` mirrors
  `klai-portal/backend/app/services/partner_rate_limit.py`.
- REQ-32.2: env-tunable defaults set higher than the SPEC literal based
  on /run research:
  - `CONNECTOR_RL_READ_PER_MIN=120`  (≈ Auth0 free tier; > Heroku 75/min)
  - `CONNECTOR_RL_WRITE_PER_MIN=30`  (3× SPEC; still 1800/hour ceiling)
  Acceptance test sets limits to the SPEC literal (60/10) so it exercises
  the SPEC-described boundaries verbatim — no test rewrite needed if
  defaults change.
- REQ-32.3: fail-open on any Redis exception. `enforce_org_rate_limit`
  catches → logs `connector_rate_limit_redis_unavailable` at WARNING
  with `exc_info=True` → allows the request through. Same pattern as
  portal `signup_email_rl` and `partner_dependencies`.
- REQ-32.4: 7 regression tests in `tests/test_connector_rate_limit.py`
  cover the AC-32 matrix (write limit + reset, read limit + reset,
  fail-open + structlog event, cross-tenant isolation, portal-secret
  bypass). Custom in-memory fake redis (4 ZSET methods) — no
  `fakeredis` dep added.

Wiring: each route in `connectors.py` gets
`dependencies=[Depends(enforce_org_rate_limit("read"|"write"))]`. POST/
PUT/DELETE = write, GET (list + by-id) = read. Portal control-plane
calls (auth middleware sets `request.state.from_portal=True`) skip the
check.

Settings: empty `REDIS_URL` default = feature OFF. No klai-infra/SOPS
pre-flight required — operators enable per-environment by setting
`REDIS_URL` on the connector service's compose `environment` block.
Fail-open semantics make the env-flip non-breaking either way.

Tests: 7 new + 6 not-found-with-deps update for the new dependency
chain. All green; ruff clean on touched files.

---

### Quality summary

- 26 new tests (6 + 2 + 11 + 7) — all green.
- Pre-existing 11 `SyncEngine._image_transport` failures unchanged
  (image-storage SPEC, separate scope).
- Ruff: clean on every touched file. The 4 remaining repo-wide ruff
  errors are all pre-existing in untouched files (notion.py N806,
  enums.py UP042, connector.py model E501).
- Pyright: 11 strict-mode "partially unknown" noise warnings remain in
  `routes/fingerprint.py` from JSON parsing — same shape as
  `knowledge_ingest.crawl4ai_client._extract_result`. The connector
  codebase is not pyright-clean overall; chasing zero-strict in this
  hygiene SPEC is scope creep.
- Three commits, one per finding. Diff stats:
  - HY-30: 3 files, 250+/1−
  - HY-31: 2 files, 419+/60−
  - HY-32: 9 files, 596+/18−

### Follow-ups (out of slice scope)

- klai-infra: when ready to enable rate limiting in any environment,
  set `REDIS_URL` in `klai-infra/core-01/.env.sops` AND add it to the
  connector environment block in `deploy/docker-compose.yml`. Fail-open
  semantics mean partial rollouts don't break.
- CI: investigate why ruff F821 didn't flag the HY-30 bug before merge.
  The pyproject.toml selects "F" (which includes F821) — local
  `ruff check` catches it. The gap is somewhere in the CI workflow
  that runs lint on connector PRs.
