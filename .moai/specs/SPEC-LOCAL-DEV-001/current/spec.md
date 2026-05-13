# SPEC-LOCAL-DEV-001: Standalone Local Development Setup

**Status:** Draft
**Created:** 2026-05-13
**Author:** Mark Vletter

---

## Problem Statement

The current local dev setup (documented in `docs/runbooks/local-dev.md`) requires access to the production Zitadel instance (`auth.getklai.com`) for authentication. This means:

- **External contributors** cannot run the portal locally (no Zitadel account)
- **New team machines** require Zitadel admin access or credentials from a team member
- **AUTH_DEV_MODE** exists but requires manually seeding a user in the DB with a known user ID — chicken-and-egg problem
- **Frontend-only mode** (Mode A) works without Docker but proxies to production API — not for backend work

As the repo goes open-source, we need two paths: one for core team (with Zitadel/infra access) and one for OS contributors (zero external access).

---

## Developer Profiles

| | **Core developer** | **OS contributor** |
|---|---|---|
| Zitadel access | Yes (auth.getklai.com) | No |
| klai-infra access | Yes (SOPS, servers) | No |
| Auth flow | Real OIDC via production Zitadel | AUTH_DEV_MODE (no Zitadel) |
| Data | Production data via proxy, or local DB with own Zitadel user | Local DB with auto-seeded dev user |
| Billing | Moneybird API (optional) | MOCK_BILLING=true |
| LLM features | Own Anthropic API key | Own key (required for AI features) |
| Recommended mode | Mode A (frontend-only) or Mode B (full-stack, prod Zitadel) | Mode C: Standalone (new) |

The existing Mode A and Mode B in `local-dev.md` continue to work for core developers. This SPEC adds **Mode C: Standalone** as the default for OS contributors and as the simplest path for core developers on new machines.

---

## Goal

On a fresh Mac with Docker + Python + Node installed:

```bash
git clone https://github.com/GetKlai/klai.git && cd klai
make setup
make dev-up        # starts Postgres, Redis, MongoDB, Meilisearch, LiteLLM
make migrate       # runs Alembic migrations
make backend       # auto-seeds dev user on first start (AUTH_DEV_MODE)
make frontend      # VITE_AUTH_DEV_MODE=true, no OIDC redirect
# Open http://localhost:5174 — logged in as dev user, no external auth needed
```

**Zero external dependencies. Zero production access. No extra containers beyond what we already have.**

---

## Design Decision: Mock Auth, Not Local Zitadel

**Rejected: Local Zitadel in Docker**
- Adds ~512MB RAM + extra container
- Requires init script to provision project/app/user via Management API
- Forces Docker on developers who currently use Mode A without it
- Overkill — we don't need real OIDC flows for local development

**Chosen: Enhanced AUTH_DEV_MODE with auto-seed**
- Already 90% implemented (backend bypasses Zitadel, frontend mocks OIDC)
- Only missing piece: backend auto-creates dev org + user at startup if DB is empty
- No new containers, no new dependencies
- Developers who want real Zitadel auth can still use Mode A/B with production Zitadel

For future e2e testing of real OIDC flows, a local Zitadel can be added as a separate Docker Compose profile — not in the default dev stack.

---

## Current State

| Component | Status | Notes |
|---|---|---|
| `docker-compose.dev.yml` | OK | Postgres, Redis, MongoDB, Meilisearch, LiteLLM |
| `Makefile` | OK | setup, dev-up, dev-down, backend, frontend, migrate, lint |
| `.env.example` files | OK | 3 locations (root, backend, frontend) |
| `docs/runbooks/local-dev.md` | OK | Thorough, but Zitadel-dependent |
| `AUTH_DEV_MODE` backend | 90% | Bypasses Zitadel token validation. Requires pre-existing DB user — crashes at startup if user missing |
| `VITE_AUTH_DEV_MODE` frontend | OK | Bypasses OIDC redirect, mocks auth context |
| Auto-seed dev user | Missing | Backend exits with error if AUTH_DEV_USER_ID not in portal_users |
| .env defaults for standalone | Missing | .env.example still defaults to production Zitadel URLs |

---

## Requirements

### REQ-1: Backend Auto-Seeds Dev User at Startup

**When** `AUTH_DEV_MODE=true` and the configured `AUTH_DEV_USER_ID` does not exist in `portal_users`,
**the system shall** automatically create a dev org and dev user in the database.

Current behavior (`app/main.py` lifespan):
```python
if settings.is_auth_dev_mode:
    if not settings.auth_dev_user_id:
        raise SystemExit(1)  # crashes
```

New behavior:
```python
if settings.is_auth_dev_mode:
    if not settings.auth_dev_user_id:
        settings.auth_dev_user_id = "dev-user-1"  # sensible default
    await ensure_dev_user_exists(db)  # creates org + user if missing
```

`ensure_dev_user_exists()` creates:
- `portal_orgs`: zitadel_org_id=`dev-org-1`, name=`Dev Organization`, slug=`dev`, plan=`professional`, provisioning_status=`complete`
- `portal_users`: zitadel_user_id=`dev-user-1` (or configured value), org_id=<dev org>, role=`admin`, display_name=`Dev User`, email=`dev@klai.local`, status=`active`

Uses `ON CONFLICT DO NOTHING` — idempotent, safe for repeated starts.

**Acceptance criteria:**
- [ ] Backend starts successfully with `AUTH_DEV_MODE=true` + `DEBUG=true` against an empty database (after migrations)
- [ ] No manual SQL insertion needed
- [ ] Startup log shows "Dev user created: dev-user-1 in org dev" (or "Dev user already exists")
- [ ] Existing behavior preserved: when `AUTH_DEV_MODE=false`, no auto-seeding occurs
- [ ] `AUTH_DEV_USER_ID` defaults to `dev-user-1` when empty and `AUTH_DEV_MODE=true` (instead of crashing)

### REQ-2: .env.example Files Default to Standalone Mode

**When** a developer runs `make setup` (which copies .env.example files),
**the system shall** produce env files that work for standalone local development without modification.

The .env.example files are restructured with two clearly labeled sections:

```
# ── Mode C: Standalone (default, no external access needed) ──
DEBUG=true
AUTH_DEV_MODE=true
AUTH_DEV_USER_ID=dev-user-1
MOCK_BILLING=true
...

# ── Mode A/B: Production Zitadel (core developers only) ──────
# Uncomment below and comment out Mode C settings above.
# AUTH_DEV_MODE=false
# ZITADEL_PAT=<get from team or klai-infra SOPS>
# ZITADEL_BASE_URL=https://auth.getklai.com
...
```

Changes to `klai-portal/backend/.env.example`:
- Mode C (standalone) settings uncommented as defaults
- Mode A/B (prod Zitadel) settings as commented alternative
- Clear labels explaining which profile each section is for
- Auto-generated keys via inline commands

Changes to `klai-portal/frontend/.env.local.example`:
- Mode C: `VITE_AUTH_DEV_MODE=true` + `VITE_API_PROXY_TARGET=http://localhost:8010` (uncommented)
- Mode A/B: `VITE_OIDC_AUTHORITY` + `VITE_OIDC_CLIENT_ID` (commented, with instructions)

**Acceptance criteria:**
- [ ] `make setup && make dev-up && make migrate && make backend` works without editing any file
- [ ] No production URLs or IDs in default (uncommented) values
- [ ] Production config documented as commented examples for team members

### REQ-3: `make seed` Target (Optional Convenience)

**When** a developer wants to reset or add additional demo data,
**the system shall** provide a `make seed` Makefile target.

Implementation:
- `dev/seed.sql` with demo data (additional orgs, meetings, knowledge items for a non-empty UI)
- `make seed` runs `docker exec -i klai-postgres-1 psql -U klai -d klai < dev/seed.sql`
- Idempotent via `ON CONFLICT DO NOTHING`
- Separate from REQ-1 (which creates only the minimal dev user at startup)

**Acceptance criteria:**
- [ ] `make seed` populates DB with demo data
- [ ] Idempotent — running twice doesn't fail or duplicate
- [ ] Portal UI shows content after seeding (not empty dashboards)

### REQ-4: Updated Documentation

**When** the setup is complete,
**the system shall** have documentation reflecting the new standalone flow.

Changes:
- Update `docs/runbooks/local-dev.md`:
  - Add Mode C: Standalone as new recommended default
  - Keep Mode A (frontend-only, prod proxy) for core devs doing UI work
  - Keep Mode B (full-stack, prod Zitadel) for core devs needing real auth
  - Remove manual SQL seeding from Auth Dev Mode section (auto-seed replaces it)
  - Add "Switching between modes" section explaining when to use which
- Add `GETTING_STARTED.md` in repo root (English) for OS contributors — only covers Mode C
- Core developer onboarding stays in `local-dev.md` (Dutch) with all three modes

**Acceptance criteria:**
- [ ] `local-dev.md` shows standalone mode as the recommended default
- [ ] `GETTING_STARTED.md` exists in English
- [ ] A developer with no Klai knowledge can follow the guide on a fresh machine

---

## Out of Scope

- Local Zitadel in Docker (can be added later as compose profile for e2e auth testing)
- Docker Compose profiles per service domain (separate SPEC)
- Service stubs/mocks for cross-service calls (separate SPEC)
- Contract testing (separate SPEC)
- Pre-commit hooks (separate SPEC)
- Moving ops files to private repo (separate SPEC)
- .devcontainer configuration (separate SPEC)

---

## Implementation Order

1. **REQ-1** — Auto-seed dev user (core change, ~50 lines in `app/main.py` or `app/core/dev_seed.py`)
2. **REQ-2** — Update .env.example files (3 files)
3. **REQ-3** — `make seed` + `dev/seed.sql`
4. **REQ-4** — Documentation updates

---

## Risks

| Risk | Mitigation |
|---|---|
| Auto-seed accidentally runs in production | Double safety gate: requires BOTH `debug=True` AND `auth_dev_mode=True`. Production validator (`_no_debug_in_production`) blocks `DEBUG=true` when `PORTAL_ENV=production` |
| RLS policies block seed inserts | Seed runs before RLS context is set — use direct connection (superuser `klai`), not tenant-scoped session |
| Existing AUTH_DEV_MODE users break | Backward compatible — if user already exists, `ON CONFLICT DO NOTHING` skips silently |
| `ZITADEL_PAT` validator crashes on placeholder value | Validator only requires non-empty when `auth_dev_mode=False` — verify this holds |

---

## Research Summary

- **AUTH_DEV_MODE** (backend `app/api/auth.py`): All Bearer tokens accepted, returns `settings.auth_dev_user_id` as authenticated user. Requires `DEBUG=true` AND `auth_dev_mode=True`.
- **VITE_AUTH_DEV_MODE** (frontend `src/lib/auth.tsx`): `DevAuthProvider` mocks entire auth context, no OIDC redirect.
- **Startup validation** (`app/main.py`): Currently exits with `SystemExit(1)` if `AUTH_DEV_USER_ID` is empty — this is the blocker that REQ-1 fixes.
- **Zitadel service mocks** (`app/services/zitadel.py`): `get_userinfo()` and `has_any_mfa()` already return mocks in dev mode. Other Zitadel calls (org creation, user provisioning) are not mocked but also not called in normal portal flows.
- **Local Zitadel** (researched, rejected): Single container ~512MB, needs init script for project/app/user provisioning. Too heavy for default dev stack; available as future compose profile if needed for e2e auth testing.
- **Mock OIDC servers** (researched, not needed): navikt/mock-oauth2-server, Soluto/oidc-server-mock. Not needed because AUTH_DEV_MODE already bypasses OIDC entirely — no token exchange happens.
