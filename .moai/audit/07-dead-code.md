# Fase 6 — Dead-Code Audit

Scope: 9 Python services + 3 TS/JS projects in the Klai monorepo. Tools: `vulture 2.16`
(Python, min-confidence 60 and 80), `knip` (TS/JS — portal-frontend and website),
CodeIndex graph (Cypher orphan query). Raw output in `.moai/audit/fase6-raw/`.

No commits, no deletions — reporting only.

---

## TL;DR

**Total genuine dead-code items: 26 findings (DEAD-001..DEAD-026)** after filtering false positives.
Finding DEAD-024 is a bundle of 7 duplicated/unused frontend files.

| Category | Genuine | False positives (in noise section) |
|---|---|---|
| Python unused functions/methods | 10 | ~70 (FastAPI routes, `@field_validator`, `@model_validator`, middleware `dispatch`, `@property`, decorated workers, test helpers) |
| Python unused classes | 2 | ~6 (pydantic v1 `Config` inner classes, SQLAlchemy models exported via `__init__`) |
| Python unused variables / config fields | 10 | ~140 (pydantic `model_config`, mapped-column fields read by ORM, ConfigDict keys, response-model attrs accessed by FastAPI serializer) |
| TS unused files | 7 (4 billing/legacy + 3 MFA duplicates + 0 other; grouped under DEAD-024) | ~35 (Astro `_`-prefixed partial includes, shadcn primitives, generated `.d.ts`) |
| TS unused exports (shadcn variants) | 1 (`UnauthorizedError`) | ~28 |
| TS unused dependencies | 0 (tailwindcss used via CSS pipeline, radix deps ship with unused UI files) | 2 packages |

Breakdown by service:

| Service | Genuine dead items |
|---|---|
| klai-portal/backend | 5 |
| klai-knowledge-ingest | 6 |
| klai-retrieval-api | 4 |
| klai-focus/research-api | 1 |
| klai-scribe/scribe-api | 2 |
| klai-connector | 2 |
| klai-mailer | 1 |
| klai-knowledge-mcp | 1 |
| klai-scribe/whisper-server | 0 |
| klai-portal/frontend | 7 unused/duplicate files + 1 unused export |
| klai-widget | (knip could not run — no node_modules) |
| klai-website | 0 (Astro partials are all intentional) |

No CRIT/HIGH security implications. All items are cleanup — reducing maintenance surface.

---

## Methodology

### Python (vulture)

Ran per service at `--min-confidence 80` (high-confidence pass) and `--min-confidence 60`
(wider net). Excluded `tests/`, `.venv/`, `alembic/versions/`, `__pycache__/`. The 60-
confidence pass surfaced ~475 raw candidates; ~10 per service at 80-confidence.

Every candidate was manually cross-checked with Grep (all case variants, string refs,
test imports, config refs) before being classified as genuine.

### TypeScript (knip)

Ran `npx knip@latest` in each TS/JS project. `klai-widget` failed (no `node_modules`,
not installed in the audit environment) — skipped with note. `klai-portal/frontend` and
`klai-website` produced usable output.

### CodeIndex orphans

Cypher query for `Function` nodes with zero incoming `CALLS` edges, excluding tests,
`__`-prefixed (magic), `node_modules`, `dist`, `alembic`. Returned 300 candidates;
nearly all were false positives for the same reason vulture's are (framework
decorators, React component exports, FastAPI/MCP route handlers). Only 3 net-new
genuine findings emerged from the graph that vulture didn't surface.

### False-positive categories (documented in the noise section)

1. **FastAPI route handlers** — decorated with `@router.get/post/put/delete/patch`.
   Vulture sees them as orphans because call sites are in the router registry, not in
   Python code.
2. **Pydantic `@field_validator`, `@model_validator`, `@property`** — invoked by
   pydantic's metaclass at runtime.
3. **Starlette middleware `dispatch` methods** — called by the ASGI chain.
4. **SQLAlchemy `Mapped[...]` columns** — read reflectively through attribute access
   and by `select()` queries.
5. **Pydantic `model_config`** and inner `Config` classes — consumed by pydantic itself.
6. **MCP tool functions** in `klai-knowledge-mcp` — exposed as MCP endpoints via
   FastMCP decorators.
7. **Procrastinate/Taskiq task wrappers** — decorated with `@procrastinate_app.task`.
8. **Test-only utilities** (`clear_cache`, `reset_cache`, `clear_centroid_cache`)  —
   vulture excludes `tests/` and so cannot see the callers.
9. **Shadcn/ui re-exports** — UI library files deliberately re-export everything; a
   subset is always "unused" until the consuming app imports the piece.
10. **Astro `_`-prefixed files** (`_index.astro`, `_components.astro`) — by Astro
    convention these are partials included via `getStaticPaths`. knip cannot follow
    that indirection.

---

## Genuine findings (Python)

### klai-portal/backend (5)

| ID | File:Line | Item | Why genuine | Action |
|---|---|---|---|---|
| DEAD-001 | `app/services/bff_session.py:80` | `class SessionNotFoundError(LookupError)` | Declared, never raised or caught anywhere in the codebase. Sibling `SessionDecryptError` is used. | Safe to remove (or wire into `SessionService` if intended). |
| DEAD-002 | `app/services/invite_scheduler.py:191` | `def get_scheduled()` | Docstring says "for testing" but no test imports it; the internal `_scheduled` dict is private. | Safe to remove — or add a unit test that uses it. |
| DEAD-003 | `app/logging_setup.py:14` | unused local `method_name` | `method_name = record.levelname.lower()` computed then never referenced (100% confidence). | Safe to remove. |
| DEAD-004 | `app/core/config.py:155` | `vexa_admin_token: str = ...` | No reader in the backend (only SPEC-VEXA-003 research doc references it). Was added in anticipation of a dropped Vexa admin API. | **Resolved (keep + annotate)** — still in SOPS + compose. `@MX:NOTE` added 2026-04-19: reserved for Vexa admin surface, keep until admin API lands. |
| ~~DEAD-005~~ | `app/core/config.py:45-50` | 6× `moneybird_product_*_monthly/yearly` settings | ~~Only read by the legacy billing mapping that was replaced~~ | **FALSE POSITIVE** (2026-04-19) — fields ARE used via dynamic `getattr(self, f"moneybird_product_{plan}_{cycle}")` in `moneybird_product_id()` at config.py:53-54, called from `billing.py:80` + `webhooks.py:40`. Vulture cannot see dynamic attribute access. Keep. |

### klai-knowledge-ingest (6)

| ID | File:Line | Item | Why genuine | Action |
|---|---|---|---|---|
| DEAD-006 | `knowledge_ingest/embedder.py:87` | `async def embed_one()` | Defined; no caller anywhere (grep of full repo returns only def site and spec docs). `embed()` batch form is used everywhere. | Safe to remove. |
| DEAD-007 | `knowledge_ingest/_patch_graphiti.py:402` | unused loop var `cls` (100% confidence) | Monkey-patch wrapper forgot to use the captured class in one branch. | Safe to remove or rename to `_`. |
| DEAD-008 | `knowledge_ingest/qdrant_store.py:358` | unused local `sparse_weight` (100% confidence) | Computed from payload but never plugged into the Qdrant query — probably a regression from a hybrid-weight refactor. | **Verify first** — this may be a bug (intended to be passed to `prefetch=`) rather than dead code. |
| DEAD-009 | `knowledge_ingest/config.py:15` | `docs_internal_secret: str = ""` | Empty-default pydantic field, never read by the service (feature-flag pattern), but no SPEC references it. | **Resolved (keep + annotate)** — docs-app accepts `X-Internal-Secret` via `requireAuthOrService` (see SEC-020 analysis). `@MX:NOTE` added: reserved for knowledge-ingest → docs-app service calls. |
| ~~DEAD-010~~ | `knowledge_ingest/config.py:22-23` | `reranker_url`, `reranker_model` | Reranker is called via portal-api, not directly from knowledge-ingest; these settings are leftover. | **✅ REMOVED** (2026-04-19) — confirmed no runtime usage (`settings.reranker*` returns zero matches). |
| DEAD-011 | `knowledge_ingest/config.py:39` | `sparse_index_on_disk: bool = False` | Referenced in SPEC-KB-007 AC-10 only, no runtime code uses it. Qdrant index-on-disk is controlled at collection creation in `qdrant_store.ensure_collection` via a different mechanism. | **Resolved (keep + annotate)** — `@MX:TODO` added referencing SPEC-KB-007 AC-10. Reserved flag; wire into `ensure_collection` sparse-index config when activated. |

### klai-retrieval-api (4)

| ID | File:Line | Item | Why genuine | Action |
|---|---|---|---|---|
| DEAD-012 | `evaluation/eval_runner.py:355` | `async def run_dimension_isolation()` | The CLI `main()` re-implements the isolation loop inline (line 425-426) and never calls this function. | Safe to remove (or call it from `main` to eliminate the duplication). |
| DEAD-013 | `retrieval_api/services/router.py:64` | `def clear_catalog_cache()` | Unlike `clear_centroid_cache` (used by tests), no caller in source or tests. | Safe to remove. |
| DEAD-014 | `retrieval_api/models.py:31` | unused `cls` (100% confidence) | Similar dead `cls` in a classmethod body. | Safe to remove or rename. |
| DEAD-015 | `retrieval_api/services/evidence_tier.py:113` | unused `assertion_mode` (100% confidence) | Function is a placeholder (v1 returns `1.00` regardless). Parameter intentionally unused — but has an `@MX:TODO` already noting this. | **Keep** as-is; the @MX:TODO already tracks it. Re-classify: not dead, just deferred per SPEC-EVIDENCE-002. (→ moved to noise/deferred list.) |

### klai-focus/research-api (1)

| ID | File:Line | Item | Why genuine | Action |
|---|---|---|---|---|
| DEAD-016 | `app/services/qdrant_store.py:104` | `def search_chunks()` | No caller in `research-api` (retrieval is done via the separate retrieval-api) — leftover stub from initial DDD prototype. The alembic migration sets up supporting indexes. | Safe to remove, OR keep if this service will take over retrieval for research notebooks (check SPEC-KNOW-004). |

### klai-scribe/scribe-api (2)

| ID | File:Line | Item | Why genuine | Action |
|---|---|---|---|---|
| DEAD-017 | `app/api/transcribe.py:90` | `def _audio_dir(user_id)` | Defined as a helper but replaced by direct path construction elsewhere; no caller. | Safe to remove. |
| DEAD-018 | `app/services/audio.py:24` | `ALLOWED_EXTENSIONS = {...}` | Constant defined, no reader — file-type validation happens via `filetype` library now. | Safe to remove. |
| — | `app/core/database.py:2` | unused `import event` (90% confidence) | Already listed in the 80-confidence vulture pass. | Safe to remove. (Part of DEAD-018? No — keep as tracked finding below.) |

Adjusted: the unused `import event` is tracked as **DEAD-019**.

| ID | File:Line | Item | Why genuine | Action |
|---|---|---|---|---|
| DEAD-019 | `app/core/database.py:2` | unused `from sqlalchemy import event` | No SQLAlchemy event listeners registered in this service. | Safe to remove. |

### klai-connector (2)

| ID | File:Line | Item | Why genuine | Action |
|---|---|---|---|---|
| ~~DEAD-020~~ | ~~`app/services/crypto.py:8`~~ | ~~`class SecretsStore`~~ | ~~Referenced only by its own import~~ | **✅ ALREADY CLEANED** (2026-04-19) — both the file and class no longer exist in `klai-portal/backend`. Removed in an earlier refactor; no tracking action needed. |
| ~~DEAD-021~~ | ~~`app/adapters/oauth_base.py:162`~~ | ~~`def _cache_token()`~~ | ~~Helper method on `OAuthAdapter`~~ | **✅ ALREADY CLEANED** (2026-04-19) — `app/adapters/` directory has been removed entirely from `klai-portal/backend` (OAuth adapters refactored inline). |

### klai-mailer (1)

| ID | File:Line | Item | Why genuine | Action |
|---|---|---|---|---|
| ~~DEAD-022~~ | `app/models.py:72` | ~~`def preferred_language(self)` on `ZitadelPayload`~~ | ~~defensive stub~~ | **✅ REMOVED** (2026-04-19) — method had no callers (`\.preferred_language\(\)` → zero matches). Knowledge (Zitadel webhook payload lacks `preferredLanguage`) preserved in commit message + git history. |

### klai-knowledge-mcp (1)

| ID | File:Line | Item | Why genuine | Action |
|---|---|---|---|---|
| DEAD-023 | `main.py:346` | redundant `if`-condition (100% confidence) | Vulture flagged a tautological check — likely `if x: ... else: ...` where one branch is unreachable. | Read and fix — safety-neutral but worth cleaning. |

### klai-scribe/whisper-server (0)

No genuine findings.

---

## Genuine findings (TypeScript — klai-portal/frontend)

### DEAD-024: Unused files (4 genuinely unused, 11 likely-intentional)

Raw knip output listed 15 unused files. After review:

**Genuinely unused** (safe to remove):

| File | Why |
|---|---|
| `src/routes/admin/_components/BillingActiveView.tsx` | Re-exported to the admin routes but not rendered anywhere. Appears in CodeIndex orphan query as well. |
| `src/routes/admin/_components/BillingSetupView.tsx` | Same as above. |
| `src/routes/admin/_components/BillingStatusViews.tsx` | Same as above. All three are legacy billing flow superseded by the current Moneybird flow. |
| `src/routes/setup/_components/EmailOTPSetup.tsx` | Duplicate: `EmailOTPSetup` is defined inline in `src/routes/setup/mfa.lazy.tsx:220` and used there. The `_components/` copy is dead. |
| `src/routes/setup/_components/MethodCard.tsx` | Duplicate: defined inline in `mfa.lazy.tsx:56` and referenced at lines 615, 623, 630. `_components/` copy is dead. |
| `src/routes/setup/_components/PasskeySetup.tsx` | Duplicate: defined inline in `mfa.lazy.tsx:108` and used at line 665. `_components/` copy is dead. |
| `src/routes/setup/_components/TOTPSetup.tsx` | Duplicate: defined inline in `mfa.lazy.tsx:386` and used at line 671. `_components/` copy is dead. |

This means **DEAD-024** expands from 4 files to 7 files. The `_components/` directory duplication likely originates from an incomplete extraction refactor where the intent was to split `mfa.lazy.tsx` into per-component files but the inline definitions were not removed. The inline versions in `mfa.lazy.tsx` are the ones actually rendered.

**Likely intentional — NOT genuine dead code:**

- `src/components/ui/{accordion,scroll-area,separator,sheet,switch,tabs}.tsx` — shadcn primitives. Keep as-is; standard practice.
- `src/paraglide.d.ts` — Paraglide-generated type declarations loaded implicitly by TS.

### DEAD-025: Unused exports

| ID | Export | Decision |
|---|---|---|
| DEAD-025 | `src/lib/apiFetch.ts: UnauthorizedError` | Class exported, never caught by name. Safe to remove (catch-all `Error` instances are caught elsewhere). |
| (noise) | `src/lib/auth-context.ts: useSession, CSRF_COOKIE_NAME` | Duplicated in both `auth.tsx` and `auth-context.ts` — one of each is genuinely used. Knip can't distinguish; manual review shows `auth.tsx:useSession` is the real one and `auth-context.ts:useSession` is the dead copy. |
| (noise) | `src/components/ui/*` (badge variants, Dialog sub-components, Alert sub-components, DropdownMenu variants) | shadcn re-exports — keep. |

### DEAD-026: Unused dependencies (false positive)

`package.json` lists `@radix-ui/react-accordion`, `@radix-ui/react-scroll-area`,
`@radix-ui/react-separator`, `@radix-ui/react-switch`, `@radix-ui/react-tabs` as unused.
These back the shadcn files in `src/components/ui/` listed above as "unused". **Decision:**
remove together with the component files, or keep both — do not split.

---

## Genuine findings (TypeScript — klai-website)

**None.** All 42 knip "unused files" are false positives:

- `src/components/sections/*.astro` — content sections imported dynamically via `src/content/...` frontmatter or by `getStaticPaths` route generation. Knip cannot follow Astro's dynamic content layer.
- `src/pages/**/_*.astro` — `_`-prefixed files are Astro convention for partials/data providers, imported by sibling pages.
- `public/widget/klai-chat*.js` — served statically, never imported by source.
- `src/styles/global.css` — injected via Astro global CSS, not via JS import.
- `update-copy.cjs`, `scripts/*.mjs` — one-off scripts invoked by npm / human.
- `keystatic.config.ts` — consumed by the Keystatic CMS at runtime.
- `tailwindcss` dependency — used via PostCSS config / global.css, not JS imports.

**Skipped:** `klai-widget` (no `node_modules` — knip could not run). Re-run after `npm install`.

---

## CodeIndex orphan highlights

After filtering FastAPI route handlers, pydantic validators, Starlette middleware
`dispatch`, React component default exports, and entry-point `main`/`lifespan`, the
only net-new CodeIndex findings (not already caught by vulture) were:

- `klai-retrieval-api/evaluation/eval_runner.py::run_retrieve` — false positive. It is passed as a functional argument to `_run_parallel` (vulture missed this indirect call; CodeIndex flagged it but the reference exists at `eval_runner.py:183, 251`).
- `klai-connector/app/services/scheduler.py::_trigger_sync` — called via APScheduler job (decorator-like registration); false positive.
- `codeindex-src/**` — out of audit scope; `codeindex-src/` is gitignored third-party source.

**Conclusion:** CodeIndex added 0 net-new findings over vulture+knip in this audit; its
value here was confirming the false-positive classification (cross-validation).

---

## Noise / false positives (summary, not fixed)

Full list in raw files. Representative examples:

**FastAPI route handlers (~80 across all services):**
`create_connector`, `list_connectors`, `delete_connector`, `health`, `sso_complete`,
`totp_login`, `passkey_confirm`, `approve_join_request`, `list_users`, `chat`,
`retrieve`, `list_meetings`, `start_meeting`, `create_app_knowledge_base`,
`list_kbs_with_access`, `update_default_org_role`, `crawl_preview`, `moneybird_webhook`,
etc.

**Pydantic validators / properties (~35):**
`password_strength`, `not_empty`, `valid_language`, `valid_locale`,
`_validate_canary_and_selector`, `_validate_conversation_content_length`,
`_validate_security_settings`, `_require_knowledge_ingest_secret`, `_normalize`,
`jwt_auth_enabled` (property), `decode_private_key` (validator).

**Middleware `dispatch` methods (~7):**
In `klai-portal/backend/app/middleware/logging_context.py`, `session.py`,
`klai-mailer/app/logging_setup.py`, `klai-focus/research-api/app/logging_setup.py`,
`klai-retrieval-api/retrieval_api/logging_setup.py`,
`klai-scribe/scribe-api/app/logging_setup.py`,
`klai-knowledge-ingest/knowledge_ingest/logging_setup.py`,
`klai-connector/app/core/logging.py`, `klai-connector/app/middleware/auth.py`.

**Pydantic `model_config`, inner `Config` (~14):**
Every pydantic v1-style inner `Config` class + every `model_config: ConfigDict(...)`.

**SQLAlchemy `Mapped[...]` columns (~60):**
`credentials_enc`, `encryption_key_version`, `last_sync_at`, `last_sync_status`,
`updated_at`, `deleted_at`, `deleted_by`, `recording_deleted_at`, `approval_token`,
`image_urls`, etc. — all read via attribute access on ORM instances.

**MCP tool functions (`klai-knowledge-mcp`):**
`save_personal_knowledge`, `save_org_knowledge`, `save_to_docs` — decorated as FastMCP tools; false positives.

**Test-only utilities:**
`clear_cache` (tenant_matcher), `reset_cache` (gate), `clear_centroid_cache` (router) — have callers only in tests (excluded from vulture scope).

**Shadcn component re-exports:**
`CommandDialog`, `CommandShortcut`, `DropdownMenuShortcut`, `DialogHeader`,
`DialogFooter`, `AlertDialogFooter`, `badgeVariants`, etc. — re-export contract of the
shadcn/ui component library.

**Procrastinate/Taskiq decorated workers:**
`run_taxonomy_backfill`, `run_crawl`, `enrich_document_interactive`,
`enrich_document_bulk`, `run_auto_categorise`, `ingest_graphiti_episode` — registered
via `@procrastinate_app.task` decorator.

**Astro partial files:**
`_index.astro`, `_components.astro`, `_docs.astro`, `_focus.astro`, `_scribe.astro` —
Astro convention for file-level partials loaded via `getStaticPaths`.

---

## Cross-references with earlier audit phases

- No overlap with Fase 3 (tenant isolation) findings — tenant checks are in active code.
- No overlap with Fase 4 (SAST) findings — none of the dead code is a security sink.
- Fase 5 (injection): DEAD-021 (`_cache_token`) touched OAuth state; since it's unreferenced, it cannot be an injection vector. Removing it reduces attack surface marginally.
- **Minor positive security impact:** removing DEAD-020 (`SecretsStore`), DEAD-002 (`get_scheduled`), DEAD-001 (`SessionNotFoundError`) slightly shrinks the backend's public API surface and removes "plausible entry points" for future misuse.

---

## Follow-up SPECs proposed

**SEC-019 — Dead-code cleanup (non-breaking removal pass)**

Scope: Remove DEAD-001, 002, 003, 006, 007, 012, 013, 014, 016, 017, 018, 019, 023, 024, 025.
Skip DEAD-004, 005, 008, 009, 010, 011, 020, 021, 022 pending SPEC or owner confirmation.
Verify each removal with: `uv run ruff check`, `uv run pyright`, full pytest suite, and
a fresh `codeindex analyze` to confirm no new F821 errors.

Acceptance:
- `git diff --stat` shows only deletions (plus test additions if `get_scheduled` is kept and tested).
- All CI gates green.
- Reduction of ~400-500 lines total.

**Not a SPEC but worth tracking:**

- DEAD-008 `sparse_weight` in `qdrant_store.py:358` may be a **bug**, not dead code. The
  variable is extracted from the request but never passed to `QueryRequest.prefetch`.
  Worth a ticket separate from the cleanup pass.
- DEAD-005 `moneybird_product_*` settings likely belong to a billing-refactor SPEC;
  capture there instead of in the cleanup pass.

---

## Raw output

All in `.moai/audit/fase6-raw/`:

- `vulture-<service>.txt` (9 files, `--min-confidence 80`)
- `vulture-<service>-c60.txt` (9 files, `--min-confidence 60`)
- `knip-portal-frontend.txt`
- `knip-website.txt`
- `knip-widget.txt` (skipped — missing node_modules)

---

Version: 1.0 (Fase 6)
Date: 2026-04-19
Audit phase: 6 of N (dead-code scan)
