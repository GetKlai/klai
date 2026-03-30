# SPEC-KB-014: Research Findings

```yaml
spec_id: SPEC-KB-014
document: research
```

---

## 1. Current Hook Behavior Analysis

**File:** `deploy/litellm/klai_knowledge.py`

The `KlaiKnowledgeHook.async_pre_call_hook()` method follows this flow:

1. Extract last user message, skip if trivial (line 46-50).
2. Extract `org_id` from LiteLLM team key metadata, `user_id` from `data["user"]`.
3. Check knowledge product entitlement via `POST /internal/v1/users/{id}/feature/knowledge` (cached 300s).
4. POST to retrieval-api (`KNOWLEDGE_RETRIEVE_URL`) with query, org_id, user_id, scope, top_k, conversation_history.
5. Parse response: check `retrieval_bypassed` flag, extract `chunks` array.
6. If chunks exist, build context block and prepend to system message.
7. Set `data["_klai_kb_meta"]` with injection metadata.

**Gap detection insertion points identified:**

- **After line 180 (chunks extraction):** The `chunks = result.get("chunks", [])` line. This is where we can inspect chunk scores.
- **Line 181-182 (`if not chunks: return data`):** Currently silently returns without logging. This is a hard gap that goes untracked.
- **Line 170 (`if result.get("retrieval_bypassed")`):** Gate bypass sets `gate_bypassed: True` in metadata and returns. This is intentional -- NOT a gap.

**Key observation:** The hook already sets `_klai_kb_meta` with `chunks_injected: 0` for gate-bypassed requests (line 174), but does NOT set any metadata for the `if not chunks:` early return (line 182). This means downstream hooks have no signal about empty retrievals.

**Retrieval-api response format** (from retrieval-api codebase):
```json
{
  "chunks": [
    {
      "text": "...",
      "score": 0.82,
      "reranker_score": 0.91,
      "metadata": {
        "title": "...",
        "kb_slug": "...",
        "candidates_retrieved": 20,
        "reranked_to": 5,
        "gate_margin": 0.15
      },
      "scope": "org"
    }
  ],
  "retrieval_bypassed": false
}
```

---

## 2. Internal API Pattern

**File:** `klai-portal/backend/app/api/internal.py`

The existing internal API uses a shared secret pattern:

- Router prefix: `/internal` (no version prefix for older endpoints, `/v1/` for newer ones).
- Auth guard: `_require_internal_token(request)` checks `Authorization: Bearer {settings.internal_secret}`.
- All endpoints use `Depends(get_db)` for database access.
- Existing endpoints: `GET /internal/user-language`, `GET /internal/users/{id}/products`, `GET /internal/connectors/{id}`, `POST /internal/connectors/{id}/sync-status`, `GET /internal/v1/users/{id}/feature/knowledge`.

**Design decision:** New endpoint should use `/internal/v1/gap-events` to follow the versioned pattern used by the newest endpoint.

---

## 3. Portal Frontend KB Stats

**File:** `klai-portal/frontend/src/routes/app/knowledge/$kbSlug.tsx` (1819 lines)

The `KBStats` interface (line 66):
```typescript
interface KBStats {
  docs_count: number | null
  connector_count: number
  connectors: ConnectorSummary[]
  volume: number | null
  usage_last_30d: number | null
}
```

Stats are fetched via `useQuery<KBStats>` at line 1600 from an inline `queryFn`. The overview tab renders metric tiles at approximately line 1745-1760.

**Extension approach:** Add `org_gap_count_7d: number | null` to the interface. The backend stats endpoint needs to include this field. The overview tab gets one additional metric tile.

---

## 4. App Navigation Structure

**File:** `klai-portal/frontend/src/routes/app/route.tsx`

Navigation items defined in `allNavItems` array (line 37-43):
```typescript
const allNavItems = [
  { to: '/app/chat', label: m.app_tool_chat_title(), icon: MessageSquare },
  { to: '/app/transcribe', label: m.app_tool_transcribe_title(), icon: Mic },
  { to: '/app/focus', label: m.app_tool_focus_title(), icon: BookOpen },
  { to: '/app/knowledge', label: m.app_tool_knowledge_title(), icon: Brain },
  { to: '/app/docs', label: m.app_tool_docs_title(), icon: BookMarked },
]
```

Product gating is defined in `PRODUCT_ROUTES` (line 12-18):
```typescript
const PRODUCT_ROUTES: Record<string, string[]> = {
  '/app/chat': ['chat'],
  '/app/transcribe': ['scribe'],
  '/app/focus': ['chat'],
  '/app/knowledge': ['knowledge'],
  '/app/docs': ['knowledge'],
}
```

**Extension approach:** Add `'/app/gaps': ['knowledge']` to `PRODUCT_ROUTES` and a new entry to `allNavItems` with `SearchX` icon from lucide-react.

---

## 5. Gap Scoping Decision

**Problem:** The LiteLLM hook searches across ALL knowledge bases in an org simultaneously. When no results are found, the hook does not know which specific KB failed to provide an answer. Options considered:

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| A: Org-scoped gaps | Simple, matches hook's actual behavior | Less actionable per-KB | **Selected** |
| B: Per-KB attribution (by best-matching KB) | More actionable | Complex, potentially misleading | Rejected |
| C: Org-scoped + per-KB summary in UI | Best of both worlds | Most complex | Deferred |

**Decision:** Option A -- gaps are org-scoped. The gap dashboard lives at `/app/gaps` as a standalone page, not as a tab inside KB detail. The KB overview tab gets a simple org-wide gap count tile as a cross-reference.

---

## 6. Privacy Considerations

**What is stored:** Raw query text from user chat messages.

**Who can see it:** Only org admins (same access level as KB management, user management).

**Retention:** 90 days, then eligible for deletion.

**Disclosure:** The org's privacy policy should mention that unanswered questions are stored temporarily for knowledge base improvement. This is no different from existing chat logging in LibreChat (which stores full conversations in MongoDB).

**GDPR consideration:** The query text is personal data if it contains identifying information. However, the same data already exists in LibreChat's MongoDB (full chat history). The gap table stores a subset (only unanswered queries) with shorter retention (90d vs indefinite in MongoDB).

---

## 7. Threshold Calibration

Based on the retrieval-api's reranker (Cohere rerank-multilingual-v3.0):

| Score Range | Meaning | Classification |
|---|---|---|
| 0.8 - 1.0 | High confidence match | Success |
| 0.4 - 0.8 | Moderate match, usable | Success |
| 0.2 - 0.4 | Low confidence, possibly relevant | Soft gap |
| 0.0 - 0.2 | Irrelevant | Soft gap |
| No chunks | Nothing found | Hard gap |

**Default threshold:** `KLAI_GAP_SOFT_THRESHOLD = 0.4` -- conservative start. Can be tuned per-org later if needed.

**Dense score fallback:** `KLAI_GAP_DENSE_THRESHOLD = 0.35` -- used only when reranker is not configured. Dense scores have a different distribution than reranker scores.

---

## 8. Existing File Paths (Verified)

| Purpose | Path | Exists |
|---|---|---|
| LiteLLM hook | `deploy/litellm/klai_knowledge.py` | Yes (242 lines) |
| Hook tests | `deploy/litellm/tests/test_klai_knowledge_hook.py` | Needs verification |
| KB models | `klai-portal/backend/app/models/knowledge_bases.py` | Yes (74 lines) |
| Internal API | `klai-portal/backend/app/api/internal.py` | Yes (259 lines) |
| KB admin API | `klai-portal/backend/app/api/knowledge_bases.py` | Yes (307 lines) |
| KB detail page | `klai-portal/frontend/src/routes/app/knowledge/$kbSlug.tsx` | Yes (1819 lines) |
| App layout/nav | `klai-portal/frontend/src/routes/app/route.tsx` | Yes (92 lines) |
| EN messages | `klai-portal/frontend/messages/en.json` | Yes |
| NL messages | `klai-portal/frontend/messages/nl.json` | Yes |
| Sidebar component | `klai-portal/frontend/src/components/layout/Sidebar.tsx` | Yes |

---

## 9. Deferred from This SPEC

The following items are explicitly out of scope for SPEC-KB-014:

1. **Per-KB gap attribution** -- requires retrieval-api changes to return per-KB relevance scores.
2. **Automated gap-to-content suggestions** -- AI-generated content recommendations based on gaps.
3. **VictoriaMetrics integration** -- metrics/alerting for gap rates (future observability SPEC).
4. **User-facing gap notifications** -- alerting end users when their question hit a gap.
5. **Gap trend visualization** -- charts showing gap frequency over time (could be a future dashboard enhancement).
6. **Cleanup automation** -- the 90-day retention policy is defined but the automated cleanup mechanism (cron job or pg_cron) is deferred to operational setup.
