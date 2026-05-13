# SPEC-DECOMM-FOCUS-001 — Implementation Plan

## Overall approach

Eén feature-branch in een dedicated worktree, één PR naar `main`. De PR is opgesplitst in 7 logische commits die los gereviewd kunnen worden. De productie-cleanup volgt na PR-merge via een runbook (`docs/runbooks/decommission-focus.md`) die een operator handmatig uitvoert.

Geen team-mode; deze opruim is sequentieel en heeft geen parallelle file-eigenaars-conflicten.

## Worktree setup

```bash
git worktree add ../klai-decomm-focus -b feature/SPEC-DECOMM-FOCUS-001 main
cd ../klai-decomm-focus
```

Alle commits in deze worktree. Push de branch zodra de eerste meaningful commit landt.

## Commits (in volgorde)

### Commit 1 — retrieval-api: scope=notebook + klai_focus weg

**Files:**
- `klai-retrieval-api/retrieval_api/services/search.py`
- `klai-retrieval-api/retrieval_api/api/retrieve.py`
- `klai-retrieval-api/retrieval_api/api/chat.py`
- `klai-retrieval-api/retrieval_api/models.py`
- `klai-retrieval-api/retrieval_api/config.py`
- `klai-retrieval-api/tests/test_notebook_filter.py` (delete)
- `klai-retrieval-api/tests/test_search.py`
- `klai-retrieval-api/tests/test_api.py`
- `klai-retrieval-api/tests/test_assertion_mode_taxonomy.py`

**Acties:**
1. `_search_notebook`, `_notebook_filter` functies weg uit `services/search.py`.
2. `scope=="notebook"` dispatch (regel 419-420) en `scope=="broad"` parallel-merge-tak (regel 422-444) in `services/search.py` weg.
3. `qdrant_focus_collection` weg uit `config.py`.
4. `Literal["personal","org","both","notebook","broad"]` → `Literal["personal","org","both"]` in `models.py`. `notebook_id` veld weg.
5. Alle `if req.scope == "notebook"` branches in `api/retrieve.py` + `api/chat.py` weg.
6. Conditionals `if req.scope != "notebook"` (graphiti skip regel 177, link-expand regel 205, reranker regel 246, knowledge.queried event regel 411) vereenvoudigen — branch is altijd actief nu de notebook-tak weg is.
7. `if req.scope != "broad"` checks weg waar van toepassing (zelfde idee).
8. Tests:
   - `test_notebook_filter.py` delete.
   - `test_broad_search_merges` (in `test_search.py`) delete.
   - Notebook-én-broad-paragrafen in andere tests (`test_api.py`, `test_assertion_mode_taxonomy.py`) weg.
   - Twee nieuwe tests:
     - `test_research_api_caller_rejected.py` — `X-Caller-Service: research-api` → 400 `unknown_caller_service`.
     - `test_retrieve_scope_notebook_returns_422` + `test_retrieve_scope_broad_returns_422` (in `test_api.py`) — Pydantic `Literal` rejection bewijst dat de scopes weg zijn.

**Verify:**
```bash
cd klai-retrieval-api
uv run pytest -x
uv run ruff check . && uv run ruff format --check .
```

### Commit 2 — portal-api: provisioning + allowlists weg

**Files:**
- `klai-portal/backend/app/services/provisioning/deprovisioning_steps.py`
- `klai-portal/backend/app/api/proxy.py` (comment-line 10)
- `klai-portal/backend/app/services/identity_verifier.py`
- `klai-portal/backend/app/services/source_extractors/_url_validator.py`
- `klai-portal/backend/tests/test_deprovisioning_steps.py`
- `klai-portal/backend/tests/test_source_extractors_ssrf.py`

**Acties:**
1. `deprovisioning_steps.py:247`: `collections = ["klai_knowledge"]`.
2. `proxy.py:10` comment regel weg (de `proxy_research` handler bestaat al niet meer).
3. `identity_verifier.py`: `research-api` uit `KNOWN_CALLER_SERVICES` (regel 64) + comment 51-53 herschrijven.
4. `_url_validator.py:49`: regel weg.
5. Tests: assertions over `klai_focus` in `test_deprovisioning_steps.py` (378, 396) weg. `test_source_extractors_ssrf.py:205` regel weg.

**Verify:**
```bash
cd klai-portal/backend
uv run pytest -x
uv run ruff check . && uv run ruff format --check .
uv run --with pyright pyright app/
```

### Commit 3 — klai-libs: gedeelde allowlists weg

**Files:**
- `klai-libs/identity-assert/klai_identity_assert/models.py`
- `klai-libs/service-auth/klai_service_auth/scopes.py`
- `klai-libs/image-storage/klai_image_storage/url_guard.py`
- `klai-libs/image-storage/tests/test_url_guard.py`
- `klai-knowledge-ingest/tests/test_url_validator.py`

**Acties:**
1. `identity-assert/models.py`: `research-api` uit `KNOWN_CALLER_SERVICES` (regel 125). Comment 106-115 herschrijven naar "decommission van research-api per SPEC-DECOMM-FOCUS-001 — research-api is geen caller meer".
2. `service-auth/scopes.py:23` docstring: `svc-research-api` schrappen.
3. `image-storage/url_guard.py:69` (`research-api`) + regel 80 (`klai-focus`) weg.
4. `test_url_guard.py:138` test-case weg.
5. `klai-knowledge-ingest/tests/test_url_validator.py:181` regel weg.

**Verify:** ruff + tests in elke lib-directory.

### Commit 4 — klai-focus directory rm

**Files:** `klai-focus/**` (41 files)

**Acties:**
1. `git rm -r klai-focus/`
2. `.gitmodules` check: zo nee een submodule, geen wijziging nodig. Zo wel, `git submodule deinit -f klai-focus && git rm klai-focus`.

**Verify:**
```bash
ls klai-focus/  # should fail
rg "klai-focus" -g '!.moai/specs/**' -g '!CHANGELOG.md' -g '!.git/**'
# Expected: 0 hits in active code
```

### Commit 4b — knowledge-ingest-flow.md update

**Files:**
- `docs/architecture/knowledge-ingest-flow.md`

**Acties:**
- Regels 799, 843, 991, 994: `broad` rij/regel weg. Tabel- en proza-text over "Focus notebook + org KB merge" wordt geschrapt of expliciet als historisch gemarkeerd onder een "Removed in 2026-05" annotation.

### Commit 5 — repo-level configs

**Files:**
- `rules/cors_middleware_last.yml`
- `.github/workflows/semgrep.yml`
- `deploy/caddy/Caddyfile`

**Acties:**
1. `rules/cors_middleware_last.yml:80` — `klai-focus/research-api/app/main.py` regel weg.
2. `.github/workflows/semgrep.yml`: regels 10, 25, 61 — `klai-focus/**` paths weg uit `paths` filter en build matrix.
3. `deploy/caddy/Caddyfile:310`: comment-regel "research-api removed in SPEC-PORTAL-UNIFY-KB-001 (Phase C)." weg. (Identiek op `/opt/klai/Caddyfile` regels 207-212; pas in repo aan, deploy-compose synct.)

**Verify:**
```bash
rg "research-api" -g '!.moai/specs/**' -g '!CHANGELOG.md'
# Expected: 0 hits
rg "klai_focus|klai-focus" -g '!.moai/specs/**' -g '!CHANGELOG.md'
# Expected: 0 hits
```

### Commit 6 — klai-infra side (separate PR in klai-infra/)

Dit is een aparte commit/PR in de `klai-infra/` submodule (eigen repo). Stappen:

```bash
cd klai-infra
git worktree add ../klai-infra-decomm-focus -b feature/SPEC-DECOMM-FOCUS-001 main
cd ../klai-infra-decomm-focus
```

**Files:**
- `core-01/.env.sops` (via SOPS roundtrip op core-01)
- `SERVERS.md`
- `INTERNAL_SECRET_ROTATION.md`

**Acties (volgorde dwingend):**

Stap 1 — SOPS edit op core-01 (per `sops-roundtrip-line-count-check`):
```bash
ssh core-01 "
  cd /tmp/klai-sops-decomm-focus
  cp ~/klai-infra/core-01/.env.sops .
  SOPS_AGE_KEY_FILE=~/.config/sops/age/keys.txt sops --decrypt --input-type dotenv --output-type dotenv .env.sops > .env.dec
  OLD=\$(wc -l < .env.dec)
  grep -v '^KUMA_TOKEN_RESEARCH_API=\\|^RESEARCH_API_ZITADEL_AUDIENCE=' .env.dec > .env.new
  NEW=\$(wc -l < .env.new)
  echo \"removed: \$((OLD - NEW))\"  # expect: 2
  SOPS_AGE_KEY_FILE=~/.config/sops/age/keys.txt sops --encrypt --input-type dotenv --output-type dotenv .env.new > .env.sops.new
  ROUNDTRIP=\$(SOPS_AGE_KEY_FILE=~/.config/sops/age/keys.txt sops --decrypt --input-type dotenv --output-type dotenv .env.sops.new | wc -l)
  LIVE=\$(wc -l < /opt/klai/.env)
  EXPECTED=\$((LIVE - 2))
  if [ \"\$ROUNDTRIP\" -ne \"\$EXPECTED\" ]; then
    echo \"REFUSING — roundtrip=\$ROUNDTRIP expected=\$EXPECTED\"
    exit 1
  fi
  mv .env.sops.new ~/klai-infra/core-01/.env.sops
"
```

Stap 2 — pull SOPS-update lokaal naar de worktree:
```bash
cd ~/Developer/Klai/klai-infra
git pull origin main
# verify the .env.sops change is present
git diff core-01/.env.sops
```

Stap 3 — SERVERS.md + INTERNAL_SECRET_ROTATION.md edit:
- `SERVERS.md` regels 71, 126, 137: research-api regels weg.
- `SERVERS.md` regels 228, 229: history-bullets bijwerken naar "decommissioned 2026-04-23 (SPEC-PORTAL-UNIFY-KB-001), tree-removed 2026-05-XX (SPEC-DECOMM-FOCUS-001)".
- `INTERNAL_SECRET_ROTATION.md` regels 64, 143: research-api regels weg.

Stap 4 — commit + push + PR in klai-infra repo.

**Verify:**
```bash
ssh core-01 "
  SOPS_AGE_KEY_FILE=~/.config/sops/age/keys.txt sops --decrypt /opt/klai/.env.sops 2>/dev/null | grep -E '^KUMA_TOKEN_RESEARCH_API|^RESEARCH_API_ZITADEL_AUDIENCE'
"
# Expected: empty
```

### Commit 7 — runbook + main repo wrap-up

**Files:**
- `docs/runbooks/decommission-focus.md` (new)
- `CHANGELOG.md`

**Inhoud van runbook (`docs/runbooks/decommission-focus.md`):**

```markdown
# Runbook — Focus / research-api decommission cleanup

> One-shot runbook. Run AFTER PR for SPEC-DECOMM-FOCUS-001 has been merged
> to main and deployed.

## Prerequisites
- SSH access to core-01 (`ssh core-01`)
- klai-infra repo with the SPEC-DECOMM-FOCUS-001 branch already merged

## Steps

### 1. Drop Qdrant klai_focus collection
ssh core-01 "docker exec klai-core-portal-api-1 sh -c 'API=\$(printenv QDRANT_API_KEY); python -c \"
import urllib.request, json
req = urllib.request.Request(
    \\\"http://qdrant:6333/collections/klai_focus\\\",
    headers={\\\"api-key\\\": \\\"\$API\\\"},
    method=\\\"DELETE\\\"
)
print(urllib.request.urlopen(req).read().decode())
\"'"
# Expected: {"result":true,"status":"ok",...}

### 2. Remove research-uploads PDFs
ssh core-01 "ls /opt/klai/research-uploads/  # final review"
ssh core-01 "rm -rf /opt/klai/research-uploads"

### 3. Remove research-api source residue
ssh core-01 "rm -rf /opt/klai/research-api-src"

### 4. Emit retention event (optional, for compliance trail)
ssh core-01 "docker exec klai-core-portal-api-1 sh -c '
python -c \"
import asyncio, asyncpg, os, json, datetime
async def main():
    c = await asyncpg.connect(host=\\\"postgres\\\", user=os.environ[\\\"POSTGRES_USER\\\"], password=os.environ[\\\"POSTGRES_PASSWORD\\\"], database=\\\"klai\\\")
    await c.execute(\\\"
        INSERT INTO product_events (event_type, org_id, user_id, properties, created_at)
        VALUES (\\\"focus.legacy_data_purged\\\", NULL, NULL, \\\$1, NOW())
    \\\", json.dumps({\\\"point_count\\\": 15, \\\"pdf_count\\\": 2, \\\"tenant_id\\\": \\\"362757920133283846\\\", \\\"spec\\\": \\\"SPEC-DECOMM-FOCUS-001\\\"}))
    await c.close()
asyncio.run(main())
\"'"

### 5. Verify cleanliness
ssh core-01 "docker exec klai-core-portal-api-1 sh -c 'API=\$(printenv QDRANT_API_KEY); python -c \"
import urllib.request, json
req = urllib.request.Request(\\\"http://qdrant:6333/collections\\\", headers={\\\"api-key\\\":\\\"\$API\\\"})
print(urllib.request.urlopen(req).read().decode())
\"'"
# Expected: only klai_knowledge in the list

ssh core-01 "ls /opt/klai/research-uploads /opt/klai/research-api-src 2>&1"
# Expected: both 'No such file or directory'
```

**CHANGELOG.md entry:**
```markdown
### Removed (2026-05-XX)
- klai-focus / research-api fully decommissioned per SPEC-DECOMM-FOCUS-001
  (afronding van SPEC-PORTAL-UNIFY-KB-001 Phase C).
- Notebook scope verwijderd uit retrieval-api.
- Qdrant `klai_focus` collection (15 chunks, single-tenant residu) verwijderd.
- SOPS vars `KUMA_TOKEN_RESEARCH_API` + `RESEARCH_API_ZITADEL_AUDIENCE` verwijderd.
```

## Risks & rollback

| Risk | Likelihood | Mitigation | Rollback |
|---|---|---|---|
| SOPS roundtrip drops extra keys | Medium | `wc -l` delta-check (commit 6) refuses if delta != -2 | Restore from `.env.bak` on core-01 |
| Hidden caller of `scope=notebook` we missed | Low | Grep run on monorepo + 24h prod-log check before merge; new test in commit 1 fails CI if a caller still sends `X-Caller-Service: research-api` | Revert PR; re-add `Literal[..., "notebook"]` |
| User asks for the 2 PDFs back | Very low (data is 2026-03-25, post-redirect) | Runbook is one-shot; before step 2 do `ls` review | Restore from core-01 backup volume (max 7d retention) |
| `klai_focus` Qdrant DELETE fails because collection doesn't exist | Low | Idempotent: API returns ok-status either way | n/a |
| Stale Caddy `/opt/klai/Caddyfile` reload | Low | deploy-compose syncs on next push | Manual `docker exec klai-core-caddy-1 caddy reload` |

## Out-of-band checks (post-merge, pre-runbook)

```bash
# Check 1: no active service uses scope=notebook
ssh core-01 "docker logs --since 24h klai-core-retrieval-api-1 | grep -E 'scope.*notebook|_search_notebook'"
# Expected: empty

# Check 2: no Caddy /research/ traffic
ssh core-01 "docker logs --since 24h klai-core-caddy-1 | grep -c '/research/'"
# Expected: 0
```

## After this runbook

- `klai-infra/SERVERS.md` is the source of truth — verify it no longer mentions research-api.
- Confirm in next standup / async update that the cleanup landed.
- Close SPEC-DECOMM-FOCUS-001 → status `implemented`.
