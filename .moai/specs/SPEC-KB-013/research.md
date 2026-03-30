# SPEC-KB-013: Research — Per-Conversation KB Scope Selection

> Deep codebase analysis — March 2026

---

## 1. Core question answered

**Can per-KB scope selection be added to the LibreChat chat interface without modifying LibreChat source code?**

**Short answer:** A clean checkbox/dropdown UI inside LibreChat is not possible without forking LibreChat. But two mechanisms that ARE accessible from within the LibreChat chat interface can be used without any LibreChat code changes:

1. **System prompt tag convention** — user sets `[klai-kb: engineering, product]` or `[klai-kb: off]` in the conversation system prompt. LibreChat's system prompt editor is accessible from within every conversation.
2. **Multiple model specs in `librechat.yaml`** — expose separate named model presets for common KB scopes. User picks from the model selector dropdown in the chat UI.

The portal settings approach (storing user preference in PostgreSQL) is NOT in the chat interface per se but IS the only approach that supports per-user defaults without any manual syntax.

---

## 2. Current retrieval architecture

### 2.1 The hook (`deploy/litellm/klai_knowledge.py`)

The `KlaiKnowledgeHook.async_pre_call_hook` runs before every LiteLLM completion call. Current flow:

```
1. Skip if call_type not completion
2. Extract last user message → skip if trivial (< 8 chars or greeting pattern)
3. Read org_id from LiteLLM key metadata → skip if not set (master key)
4. Read user_id from data["user"] → skip if not set
5. Feature gate: GET /internal/v1/users/{user_id}/feature/knowledge (cached 300s)
   → skip if not enabled (fail-closed)
6. POST to retrieval-api: { query, org_id, user_id, scope="both", top_k, conversation_history }
7. Inject chunks as system message prefix
```

**Key observation:** The hook HARDCODES `scope="both"` and does NOT pass `kb_slugs`. There is no pre-step — the retrieval-api call always fires for eligible users.

### 2.2 The `RetrieveRequest` model (`klai-retrieval-api/retrieval_api/models.py:8`)

```python
class RetrieveRequest(BaseModel):
    query: str
    org_id: str
    scope: Literal["personal", "org", "both", "notebook", "broad"] = "org"
    user_id: str | None = None
    notebook_id: str | None = None
    top_k: int = 8
    conversation_history: list[dict] = Field(default_factory=list)
    kb_slugs: list[str] | None = None  # ← filtering already live, hook doesn't use it
```

**Finding:** `kb_slugs` filtering in `retrieval-api` is already implemented. The gap is exclusively in the hook and the user-facing interface to control it.

### 2.3 Portal internal API (`klai-portal/backend/app/api/internal.py:181`)

The feature gate endpoint `/internal/v1/users/{librechat_user_id}/feature/knowledge` currently returns only `{"enabled": bool}`. This is the one portal call the hook already makes per request (cached 300s). Extending this response to include KB scope preference costs nothing extra — the cache TTL already absorbs the cost.

### 2.4 LibreChat configuration (`deploy/librechat/librechat.yaml`)

LibreChat is configured with:
- One custom endpoint: "Klai AI" → LiteLLM at `http://litellm:4000/v1`
- One model spec: `klai-primary` with a default system prompt
- `modelSpecs.list[].preset.systemPrompt` is the default system prompt per model spec

**Key finding about LibreChat's data flow:**

LibreChat sends a standard OpenAI-compatible API request to LiteLLM. The hook receives the full request dict as `data`. This dict contains:
- `data["messages"]` — including the system message with the preset system prompt
- `data["user"]` — LibreChat MongoDB user ID
- `data["model"]` — the requested model name
- Any `addParams` from the endpoint config would appear here

LibreChat's `addParams` in `librechat.yaml` custom endpoints can add arbitrary body parameters to every API call. These appear directly in `data`. However, `addParams` is STATIC per endpoint config — not dynamic per conversation.

### 2.5 PortalUser model (`klai-portal/backend/app/models/portal.py:41`)

Current fields: `zitadel_user_id`, `org_id`, `role`, `preferred_language`, `status`, `display_name`, `email`, `librechat_user_id`.

**No KB preference fields exist.** Need to add:
- `kb_retrieval_enabled: bool` (default True, preserves current behavior)
- `kb_slugs_filter: list[str] | null` (null = all KBs)

---

## 3. What LibreChat DOES support (without forking)

### 3.1 System prompt per conversation

Every conversation in LibreChat has an editable system prompt. Users access it via the settings icon on any conversation. If the default system prompt includes a parseable tag, users can see and modify it.

**LibreChat's `modelSpecs.preset.systemPrompt`** sets the default system prompt for new conversations using that model spec. If this includes `[klai-kb: off]`, every new conversation with that model spec starts with KB disabled.

### 3.2 Multiple model specs

`librechat.yaml` can define multiple entries in `modelSpecs.list`. Each appears as a separate item in LibreChat's model selector (the dropdown at the top of every conversation). This is IN the chat interface.

Example: Adding `klai-primary-no-kb` with `systemPrompt: "[klai-kb:off]\nYou are a helpful AI assistant."` would give users a one-click way to start a KB-free conversation.

**Limitation:** KB slugs in model specs are hardcoded at deployment time, so org-specific KB slug selection requires per-tenant `librechat.yaml` customization. Currently each tenant already has a separate container (and potentially `librechat.yaml`), so this is feasible.

### 3.3 System prompt editing in-conversation

In LibreChat, users can open the conversation settings and directly edit the system prompt for that conversation. This is IN the chat interface. If the hook supports a tag syntax like `[klai-kb: off]` or `[klai-kb: engineering, product]`, users can set it per conversation without any preset/model config change.

---

## 4. What REQUIRES LibreChat modification

A proper KB selector UI — e.g. a multi-select checklist in the conversation settings sidebar showing available KBs with toggle switches — is not possible without modifying LibreChat's React frontend and MongoDB schema. This would require:

1. Adding a KB selection component to LibreChat's conversation settings panel
2. Storing the selection in LibreChat's MongoDB conversation documents
3. LibreChat passing the selection as `extra_body` or a custom header to LiteLLM
4. Ongoing maintenance of a LibreChat fork

**Verdict: out of scope for SPEC-KB-013.** The fork maintenance cost is too high. The system prompt approach delivers equivalent functionality with zero fork cost.

---

## 5. The "pre-step" requirement

The user wants "a step in front that determines if we should retrieve at all."

Currently the hook ALWAYS calls retrieval-api for eligible users, even when retrieval would add nothing. The fix is simple:

```python
# After feature gate check, read KB preference:
kb_pref = await _get_kb_preference(user_id, org_id, cache)
if not kb_pref["kb_retrieval_enabled"]:
    return data  # ← pre-step: skip retrieval entirely, no HTTP call to retrieval-api
```

This pre-step also eliminates unnecessary retrieval-api HTTP calls for users who have disabled KB lookup.

---

## 6. Architecture decision: two-layer approach

Given the constraints, the right architecture is **two complementary layers**:

| Layer | Where | How | User action |
|-------|--------|-----|-------------|
| **Default preference** | Portal account settings | `PortalUser` DB fields + extended internal API | Set once in portal → persists across all chats |
| **Per-conversation override** | LibreChat system prompt | Hook parses `[klai-kb: ...]` tag | Add tag to system prompt in LibreChat conversation settings |

The hook priority order:
1. System prompt tag present → use tag value (overrides everything)
2. No tag → use portal preference (`kb_retrieval_enabled` + `kb_slugs_filter`)
3. No portal preference → current behavior (all KBs, retrieval enabled)

---

## 7. Files to touch

| File | Change |
|------|--------|
| `deploy/litellm/klai_knowledge.py` | Parse system prompt tag, read KB pref from extended feature gate, pre-step skip |
| `klai-portal/backend/app/models/portal.py` | Add `kb_retrieval_enabled`, `kb_slugs_filter` to `PortalUser` |
| `klai-portal/backend/alembic/versions/{hash}_add_kb_pref.py` | Migration |
| `klai-portal/backend/app/api/internal.py` | Extend feature gate endpoint to return KB preference |
| `klai-portal/backend/app/api/app_account.py` | PATCH /api/app/account/kb-preference endpoint |
| `klai-portal/frontend/src/routes/app/account.tsx` | KB preference section in account settings |
| `klai-portal/frontend/messages/en.json` + `nl.json` | i18n keys |
| `deploy/litellm/tests/test_klai_knowledge_hook.py` | Tests for new behavior |
| `deploy/librechat/librechat.yaml` | Optional: add model spec variants |

---

## 8. Risks and constraints

| Risk | Mitigation |
|------|-----------|
| System prompt tag is user-visible | Tag is stripped before model receives it — transparent to the model |
| Portal preference cached 300s in hook | Acceptable lag. User who changes preference may see old behavior for up to 5 min. |
| `kb_slugs_filter` with invalid slugs | Hook passes unknown slugs to retrieval-api; retrieval-api returns 0 results for unknown slugs (safe degradation) |
| No KB slugs API for the frontend | Need `GET /api/app/knowledge-bases` to list available KBs for the multi-select — already exists in `app_knowledge_bases.py` |
| Existing tests | `test_klai_knowledge_hook.py` tests the hook — must update |
