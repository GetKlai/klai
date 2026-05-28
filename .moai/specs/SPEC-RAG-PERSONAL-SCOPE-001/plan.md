# Plan: SPEC-RAG-PERSONAL-SCOPE-001

> Implementation methodology: TDD (RED-GREEN-REFACTOR), single-PR rollout.
> Status: draft
> Author: Mark Vletter + Claude Opus 4.7

## Phase 0 — Worktree setup

```bash
git worktree add ../geneva-personal-scope -b feature/SPEC-RAG-PERSONAL-SCOPE-001 main
cd ../geneva-personal-scope
```

Rationale: SPEC touches 3 services and 1 new library — well above the 3-file threshold from `spec-work-in-a-worktree` pitfall.

## Phase 1 — Shared library

### 1.1 Scaffold `klai-libs/kb-slugs`

Copy the structure from `klai-libs/chat-prompts`:
- `klai-libs/kb-slugs/pyproject.toml` — name `klai-kb-slugs`, version `0.1.0`, no runtime deps
- `klai-libs/kb-slugs/klai_kb_slugs/__init__.py` — exports `personal_kb_slug`
- `klai-libs/kb-slugs/klai_kb_slugs/py.typed` — empty marker for pyright
- `klai-libs/kb-slugs/tests/test_personal_kb_slug.py` — pytest for the function
- `klai-libs/kb-slugs/README.md` — one-paragraph rationale referencing this SPEC

Function body:

```python
def personal_kb_slug(user_id: str) -> str:
    """Build the canonical personal KB slug for a user.

    Source of truth for the slug template — imported by klai-portal at
    provisioning time and by klai-retrieval-api at search time. Keep this
    function trivial; if the template ever needs to change, both services
    pick up the change atomically via re-deploy.
    """
    return f"personal-{user_id}"


__all__ = ["personal_kb_slug"]
```

### 1.2 Wire portal-api to the new library

- Add `klai-kb-slugs = { workspace = true }` (or path-dep equivalent — mirror what `klai-portal/backend/pyproject.toml` does for `klai-chat-prompts`).
- In `klai-portal/backend/app/services/default_knowledge_bases.py`: replace the inline `personal_kb_slug` body with `from klai_kb_slugs import personal_kb_slug`. Keep the local re-export so existing imports `from app.services.default_knowledge_bases import personal_kb_slug` continue to work without churn.
- Drift test: `klai-portal/backend/tests/test_personal_kb_slug_drift.py` asserts `personal_kb_slug("uX") == "personal-uX"` AND equals the legacy `f"personal-{user_id}"` literal — guard against accidental rename.

### 1.3 Wire retrieval-api to the new library

- Add `klai-kb-slugs` path-dep to `klai-retrieval-api/pyproject.toml`.
- Add Dockerfile COPY of the library (mirror the pattern used for `klai-chat-prompts`).
- Verify build: `docker build -f klai-retrieval-api/Dockerfile .` succeeds locally.

## Phase 2 — Retrieval-api filter

### 2.1 RED — failing tests first

In `klai-retrieval-api/tests/test_scope_filter.py`:

```python
def test_scope_personal_narrows_to_canonical_slug(self):
    req = _make_request(scope="personal", user_id="u1")
    conditions = _scope_filter(req)
    # User_id condition still present
    assert any(
        isinstance(c, FieldCondition) and c.key == "user_id"
        and c.match.value == "u1"
        for c in conditions
    )
    # NEW: canonical slug condition present
    assert any(
        isinstance(c, FieldCondition) and c.key == "kb_slug"
        and c.match.value == "personal-u1"
        for c in conditions
    )

def test_scope_personal_personal_role_still_narrows_to_canonical(self):
    # personal-role kb_slugs strip happens upstream of _scope_filter;
    # this test asserts the filter applies regardless of effective_role
    req = _make_request(scope="personal", user_id="u1", kb_slugs=None)
    req = req.model_copy(update={"effective_role": "personal"})
    conditions = _scope_filter(req)
    assert any(
        isinstance(c, FieldCondition) and c.key == "kb_slug"
        and c.match.value == "personal-u1"
        for c in conditions
    )

def test_scope_both_personal_portion_narrows_to_canonical(self):
    req = _make_request(scope="both", user_id="u1", kb_slugs=["org1"])
    conditions = _scope_filter(req)
    # Find the visibility_should Filter; assert the private-branch carries
    # the canonical slug condition
    visibility_filter = next(
        c for c in conditions
        if isinstance(c, Filter) and c.should is not None
    )
    private_branch = next(
        s for s in visibility_filter.should
        if s.must and any(
            isinstance(m, FieldCondition) and m.key == "visibility"
            and m.match.value == "private"
            for m in s.must
        )
    )
    slug_conditions = [
        m for m in private_branch.must
        if isinstance(m, FieldCondition) and m.key == "kb_slug"
    ]
    assert any(
        c.match.value == "personal-u1" for c in slug_conditions
    )
```

Run pytest; confirm RED.

### 2.2 GREEN — minimal patch to `_scope_filter`

```python
from klai_kb_slugs import personal_kb_slug

def _scope_filter(request: RetrieveRequest) -> list[FieldCondition | Filter]:
    conditions: list[FieldCondition | Filter] = [
        FieldCondition(key="org_id", match=MatchValue(value=request.org_id)),
    ]

    if request.scope == "personal":
        if request.user_id:
            conditions.append(
                FieldCondition(key="user_id", match=MatchValue(value=request.user_id))
            )
            # SPEC-RAG-PERSONAL-SCOPE-001 REQ-2: server-side narrow to canonical
            conditions.append(
                FieldCondition(
                    key="kb_slug",
                    match=MatchValue(value=personal_kb_slug(request.user_id))
                )
            )
    else:
        not_private = Filter(
            must_not=[FieldCondition(key="visibility", match=MatchValue(value="private"))]
        )
        visibility_should: list[Filter] = [not_private]
        if request.user_id:
            # SPEC-RAG-PERSONAL-SCOPE-001 REQ-3: personal portion narrows to canonical
            visibility_should.append(
                Filter(
                    must=[
                        FieldCondition(key="visibility", match=MatchValue(value="private")),
                        FieldCondition(key="user_id", match=MatchValue(value=request.user_id)),
                        FieldCondition(
                            key="kb_slug",
                            match=MatchValue(value=personal_kb_slug(request.user_id))
                        ),
                    ]
                )
            )
        conditions.append(Filter(should=visibility_should))

    if request.kb_slugs:
        if request.scope == "both" and request.user_id:
            conditions.append(
                Filter(
                    should=[
                        FieldCondition(key="kb_slug", match=MatchAny(any=request.kb_slugs)),
                        FieldCondition(key="user_id", match=MatchValue(value=request.user_id)),
                    ]
                )
            )
        else:
            conditions.append(
                FieldCondition(key="kb_slug", match=MatchAny(any=request.kb_slugs))
            )
    return conditions
```

Run pytest; confirm GREEN.

### 2.3 Add structured log event (REQ-8)

In `retrieve.py` (after `_scope_filter` is called inside the search functions, or via a helper invoked alongside it):

```python
if req.scope in ("personal", "both") and req.user_id:
    logger.info(
        "retrieval_personal_scope_canonical_filter_applied",
        org_id=req.org_id,
        user_id=req.user_id,
        canonical_slug=personal_kb_slug(req.user_id),
        scope=req.scope,
        effective_role=req.effective_role,
        client_supplied_kb_slugs=req.kb_slugs,
    )
```

Place the call once per request, immediately after `_apply_role_rewrite` has run and just before `_search_knowledge` is invoked — so the logged `req` reflects the post-strip state.

## Phase 3 — Regression test

`klai-retrieval-api/tests/test_personal_scope_canonical_regression.py`:

```python
"""SPEC-RAG-PERSONAL-SCOPE-001 REQ-7 — Jantine scenario.

Two private KBs owned by the same user:
  - personal-<user_id> (canonical)
  - test2 (user-created)

scope=personal MUST return only canonical KB chunks.
"""

import pytest
from retrieval_api.models import RetrieveRequest
from retrieval_api.services.search import _scope_filter
from qdrant_client.http.models import FieldCondition, Filter


@pytest.mark.parametrize("role", ["personal", "admin", "company"])
def test_personal_scope_excludes_non_canonical_user_owned_kbs(role: str):
    req = RetrieveRequest(
        query="wie is jantine?",
        org_id="o1",
        scope="personal",
        user_id="jantine-zitadel-sub",
        effective_role=role,
    )
    conditions = _scope_filter(req)
    slug_conditions = [
        c for c in conditions
        if isinstance(c, FieldCondition) and c.key == "kb_slug"
    ]
    assert len(slug_conditions) == 1
    assert slug_conditions[0].match.value == "personal-jantine-zitadel-sub", (
        f"Expected canonical slug filter for role={role}, "
        f"got {slug_conditions[0].match.value!r}"
    )
```

## Phase 4 — Test update sweep

| File | Change |
|---|---|
| `klai-retrieval-api/tests/test_scope_filter.py` | Add the 3 new tests from Phase 2.1; update `test_kb_slugs_both_scope_with_user_bypasses_personal_chunks` to expect canonical slug inside the private branch |
| `klai-retrieval-api/tests/test_role_filter.py` | Add `test_personal_role_stripped_slugs_still_canonical_narrowed` (end-to-end through `_apply_role_rewrite` + `_scope_filter`) |
| `klai-retrieval-api/tests/test_api.py` | Verify integration tests around `/retrieve` endpoint still green |
| `deploy/litellm/tests/test_klai_knowledge_hook.py` | Should require zero changes; hook keeps sending kb_slugs filter (redundant defense-in-depth) |
| `klai-portal/backend/tests/test_personal_kb_slug_drift.py` | New file — drift test for the shared library |
| `klai-libs/kb-slugs/tests/test_personal_kb_slug.py` | New file — unit tests for the library |

## Phase 5 — Pre-merge validation

```bash
# library tests
cd klai-libs/kb-slugs && uv run --extra dev pytest -q

# portal-api tests
cd klai-portal/backend && uv run pytest tests/ -q

# retrieval-api tests
cd klai-retrieval-api && uv run pytest tests/ -q

# litellm tests (should be unchanged)
cd deploy/litellm && PYTHONPATH=. .venv-test/bin/python3 -m pytest tests/ -q

# lint
cd klai-retrieval-api && uv run ruff check . && uv run ruff format --check .
cd klai-portal/backend && uv run ruff check . && uv run ruff format --check .
```

## Phase 6 — Manual e2e

After CI green + deploy:
1. Login as a test user with `effective_role=personal`.
2. Create a second private KB called `test2`. Upload a unique document.
3. Open chat with only "Persoonlijk" selected in the dropdown.
4. Ask a question that should match content in `test2` (e.g., a phrase that appears only in test2).
5. Verify the response either does not surface test2 content OR explicitly refuses (canned no-citable-sources message).
6. Switch dropdown to include "Persoonlijk" AND "test2" → repeat the query. test2 content SHOULD now appear (kb_slugs=[`personal-U`, `test2`] explicitly).
7. Query VictoriaLogs: `service:retrieval-api AND event:retrieval_personal_scope_canonical_filter_applied` → events present.

## Risks

| Risk | Mitigation |
|---|---|
| Existing user with non-canonical personal-KB content stops seeing it in Persoonlijk dropdown | Intentional — the whole point. Notify in deploy comms ("Persoonlijk now only surfaces the canonical KB; pick the other KB explicitly to include it"). |
| Knowledge-mcp users (Claude Desktop, Cursor) suddenly miss content from user-created private KBs | scope=both still returns those chunks via the user_id-bypass in the kb_slugs branch. The visibility-should narrowing only fires when kb_slugs is set or absent — re-check this in the GREEN phase. |
| Slug template renamed in the future | Single point of change in `klai-libs/kb-slugs`; both services pick up via re-deploy. Drift test catches accidental local re-introduction. |
| Pre-existing `_apply_role_rewrite` becomes redundant | Kept intentionally — preserves the original RBAC intent (personal-role can't reach org_slugs). Add a code comment cross-referencing this SPEC so future readers know the strip and the canonical filter co-exist by design. |
| LiteLLM hook's PR #715 client-side filter becomes redundant | Kept intentionally — defense-in-depth. Two layers narrow to the same set; second layer catches if first layer drops. |

## Estimated diff size

- 1 new package (klai-libs/kb-slugs): ~50 lines incl. tests
- klai-portal/backend: ~5-line edit + 1 new test file
- klai-retrieval-api: ~15-line edit + 2 new test files
- Dockerfile + pyproject updates: ~10 lines across 2 files
- Total: <300 lines additions, <50 lines deletions

Single PR, single commit (or 3 logical commits — lib / portal / retrieval — squash-merged).
