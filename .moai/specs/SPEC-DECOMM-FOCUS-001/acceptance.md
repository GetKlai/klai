# SPEC-DECOMM-FOCUS-001 — Acceptance Criteria

## Definition of Done

Deze SPEC is `implemented` zodra ALLE onderstaande criteria onafhankelijk verifieerbaar zijn op een verse checkout van `main` ná merge én het runbook volledig is uitgevoerd op core-01.

---

## A. Code-zijde grep-gates (CI-enforced)

Run vanaf de monorepo root:

| # | Command | Expected |
|---|---|---|
| A.1 | `rg "research-api" -g '!.moai/specs/**' -g '!CHANGELOG.md'` | 0 hits |
| A.2 | `rg "klai_focus" -g '!.moai/specs/**' -g '!CHANGELOG.md'` | 0 hits |
| A.3 | `rg "klai-focus" -g '!.moai/specs/**' -g '!CHANGELOG.md' -g '!docs/runbooks/**'` | 0 hits |
| A.4 | `rg "_search_notebook\|qdrant_focus_collection" -g '!.moai/specs/**'` | 0 hits |
| A.5 | `rg "scope.*=.*['\"]notebook['\"]" --include='*.py' --include='*.ts'` | 0 hits |
| A.5b | `rg '"broad"' klai-retrieval-api/retrieval_api/ --include='*.py'` | 0 hits in production code (tests like `_is_broad_except` in portal-api unrelated, blijven) |
| A.6 | `rg "svc-research-api"` | 0 hits |
| A.7 | `ls klai-focus/ 2>&1` | `No such file or directory` |

**CI:** Add this block to `.github/workflows/portal-api.yml` (or a dedicated `decommission-grep.yml`) so any future PR that re-introduces a hit fails CI:

```yaml
- name: SPEC-DECOMM-FOCUS-001 grep-gate
  run: |
    set -e
    if rg -q "research-api|klai_focus|klai-focus|_search_notebook|qdrant_focus_collection|svc-research-api" \
       -g '!.moai/specs/**' -g '!CHANGELOG.md' -g '!docs/runbooks/**'; then
      echo "FAIL: SPEC-DECOMM-FOCUS-001 reintroduced. See SPEC-DECOMM-FOCUS-001/spec.md."
      exit 1
    fi
```

(NB: the grep-gate workflow itself contains these strings as patterns. Use a fenced/escaped form so the gate doesn't match itself, e.g. read patterns from `.spec-decomm-focus-001-forbidden.txt` and grep against it.)

---

## B. Service-zijde gedrag (test-enforced)

### B.1 retrieval-api `/retrieve` rejects both removed scopes with 422

```bash
cd klai-retrieval-api
uv run pytest tests/test_api.py::test_retrieve_scope_notebook_returns_422 \
              tests/test_api.py::test_retrieve_scope_broad_returns_422 -x
```

New tests assert:
```python
def test_retrieve_scope_notebook_returns_422(client):
    resp = client.post("/retrieve", json={"query": "test", "org_id": "org-1", "scope": "notebook"})
    assert resp.status_code == 422
    assert "notebook" in resp.json()["detail"][0]["msg"].lower()

def test_retrieve_scope_broad_returns_422(client):
    resp = client.post("/retrieve", json={"query": "test", "org_id": "org-1", "scope": "broad"})
    assert resp.status_code == 422
    assert "broad" in resp.json()["detail"][0]["msg"].lower()
```

### B.2 retrieval-api rejects `X-Caller-Service: research-api`

```bash
cd klai-retrieval-api
uv run pytest tests/test_research_api_caller_rejected.py -x
```

New test asserts:
```python
def test_research_api_caller_rejected(client):
    resp = client.post("/retrieve",
        json={"query": "test", "org_id": "org-1", "scope": "org"},
        headers={"X-Caller-Service": "research-api", "X-Internal-Secret": settings.internal_secret})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "unknown_caller_service"
```

### B.3 portal-api deprovisioning targets only `klai_knowledge`

```bash
cd klai-portal/backend
uv run pytest tests/test_deprovisioning_steps.py::test_purge_qdrant_collections_only_klai_knowledge -x
```

Existing test updated to assert the call_args list contains exactly `["klai_knowledge"]`, not `["klai_knowledge", "klai_focus"]`.

### B.4 ALL existing test-suites still pass

```bash
# Per service:
( cd klai-retrieval-api && uv run pytest -x )
( cd klai-portal/backend && uv run pytest -x )
( cd klai-knowledge-ingest && uv run pytest -x )
( cd klai-libs/identity-assert && uv run pytest -x )
( cd klai-libs/service-auth && uv run pytest -x )
( cd klai-libs/image-storage && uv run pytest -x )
```

All must be green. No `--ignore` or `--skip` flags introduced for this SPEC.

---

## C. Lint / type / format

```bash
( cd klai-retrieval-api && uv run ruff check . && uv run ruff format --check . )
( cd klai-portal/backend && uv run ruff check . && uv run ruff format --check . && uv run --with pyright pyright app/ )
( cd klai-knowledge-ingest && uv run ruff check . && uv run ruff format --check . )
( cd klai-libs/identity-assert && uv run ruff check . && uv run ruff format --check . )
( cd klai-libs/service-auth && uv run ruff check . && uv run ruff format --check . )
( cd klai-libs/image-storage && uv run ruff check . && uv run ruff format --check . )
```

All zero-error.

---

## D. Productie state (post-runbook)

Run from operator terminal after running `docs/runbooks/decommission-focus.md` end-to-end.

### D.1 Qdrant collections

```bash
ssh core-01 "docker exec klai-core-portal-api-1 sh -c 'API=\$(printenv QDRANT_API_KEY); python -c \"
import urllib.request, json
req = urllib.request.Request(\\\"http://qdrant:6333/collections\\\", headers={\\\"api-key\\\":\\\"\$API\\\"})
print(json.dumps(json.loads(urllib.request.urlopen(req).read())[\\\"result\\\"][\\\"collections\\\"]))
\"'"
```

**Expected:** `[{"name":"klai_knowledge"}]` — exactly one collection, `klai_focus` absent.

### D.2 Filesystem

```bash
ssh core-01 "ls /opt/klai/research-uploads /opt/klai/research-api-src 2>&1"
```

**Expected:** both `No such file or directory`.

### D.3 SOPS env

```bash
ssh core-01 "SOPS_AGE_KEY_FILE=~/.config/sops/age/keys.txt sops --decrypt /opt/klai/.env.sops 2>/dev/null | grep -E '^KUMA_TOKEN_RESEARCH_API|^RESEARCH_API_ZITADEL_AUDIENCE'"
```

**Expected:** empty output.

### D.4 Container topology unchanged

```bash
ssh core-01 "docker ps --format '{{.Names}}' | wc -l"
```

**Expected:** unchanged from pre-merge baseline (research-api wasn't running anyway; this catches accidental side-effect deletes).

### D.5 Caddy traffic still 0 on /research/*

```bash
ssh core-01 "docker logs --since 24h klai-core-caddy-1 2>&1 | grep -c '/research/'"
```

**Expected:** 0. (Same as pre-decommission, but verify after merge so we know nothing started routing there mid-deploy.)

### D.6 Product event recorded

```bash
ssh core-01 "docker exec klai-core-portal-api-1 sh -c '
python -c \"
import asyncio, asyncpg, os
async def main():
    c = await asyncpg.connect(host=\\\"postgres\\\", user=os.environ[\\\"POSTGRES_USER\\\"], password=os.environ[\\\"POSTGRES_PASSWORD\\\"], database=\\\"klai\\\")
    r = await c.fetch(\\\"SELECT event_type, properties, created_at FROM product_events WHERE event_type = \\\$1 ORDER BY created_at DESC LIMIT 1\\\", \\\"focus.legacy_data_purged\\\")
    print(r)
    await c.close()
asyncio.run(main())
\"'"
```

**Expected:** one row, `properties` JSON contains `point_count: 15`, `pdf_count: 2`, `tenant_id: 362757920133283846`, `spec: SPEC-DECOMM-FOCUS-001`.

---

## E. Documentation

### E.1 SERVERS.md

```bash
grep -c "research-api" klai-infra/SERVERS.md
```

**Expected:** 0 (or only in a "decommissioned 2026-05" history bullet under #Done section, NOT in the live service inventory tables).

### E.2 INTERNAL_SECRET_ROTATION.md

```bash
grep -c "research-api" klai-infra/INTERNAL_SECRET_ROTATION.md
```

**Expected:** 0.

### E.3 CHANGELOG.md

A new `### Removed (2026-05-XX)` block exists under the unreleased section, mentioning SPEC-DECOMM-FOCUS-001.

### E.4 Runbook is reachable

`docs/runbooks/decommission-focus.md` exists with steps 1–5 and a "After this runbook" section that includes the SPEC closure step.

---

## F. CI gates

### F.1 grep-gate workflow runs and is green on the merge commit

CI run for the PR's merge commit must include the SPEC-DECOMM-FOCUS-001 grep-gate (defined in section A) and exit 0.

### F.2 Existing CI for all touched services is green

`portal-api.yml`, `klai-retrieval-api.yml`, `klai-knowledge-ingest.yml`, `klai-libs-*.yml` — all green on the merge commit.

### F.3 No new images pushed for research-api

Check: `docker pull ghcr.io/getklai/research-api:<merge-sha> 2>&1` returns "manifest not found" — the workflow `klai-focus/.github/workflows/research-api.yml` is gone (came along with `git rm -r klai-focus/`), so no new image got built.

---

## G. Sanity (manual)

### G.1 `/app/focus` redirect still works

Open `https://my.getklai.com/app/focus` in a browser → 301/302 → `https://my.getklai.com/app/knowledge`. (This was already the case pre-SPEC; verify that nothing in this PR broke the redirect.)

### G.2 Knowledge KB chat still queries chunks

In `/app/knowledge` of any tenant, open a personal-KB chat, ask a question that the KB knows. Verify the response cites a chunk. (Sanity that retrieval-api still works after we ripped notebook-scope out of it.)

### G.3 No "X-Caller-Service: research-api" warnings in retrieval-api logs in 24h

```bash
ssh core-01 "docker logs --since 24h klai-core-retrieval-api-1 2>&1 | grep -i 'research-api'"
```

**Expected:** empty. (If a caller somewhere still sends this header, the new test from B.2 will catch it BEFORE merge; this is the post-merge confirmation.)

---

## Acceptance signoff

| Section | Verifier | Date | Status |
|---|---|---|---|
| A — Grep-gates | (CI) | | |
| B — Service tests | (CI) | | |
| C — Lint/type/format | (CI) | | |
| D — Productie state | (Operator, post-runbook) | | |
| E — Documentation | (Reviewer) | | |
| F — CI gates | (CI) | | |
| G — Sanity | (Operator) | | |

When all rows are green, set `.moai/specs/SPEC-DECOMM-FOCUS-001/spec.md` `status: implemented` and add a final HISTORY entry.
