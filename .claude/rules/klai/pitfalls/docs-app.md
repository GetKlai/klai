---
paths: "klai-docs/**"
---
# Docs-App Pitfalls

> klai-docs (Next.js) integration from portal-api (`docs_client.py`).
> Derived from SPEC-KB-003 integration debugging, 2026-03-25.

## Index
> Keep this index in sync — add a row when adding an entry below.

| Entry | Sev | Rule |
|---|---|---|
| [platform-docs-app-port](#platform-docs-app-port) | HIGH | docs-app runs on port **3010**, not 3000 |
| [platform-docs-app-basepath](#platform-docs-app-basepath) | HIGH | All routes under `/docs/api/...`, not `/api/...` |
| [platform-docs-app-visibility-values](#platform-docs-app-visibility-values) | HIGH | Map portal `internal` → docs-app `private` |
| [platform-docs-app-error-logging](#platform-docs-app-error-logging) | MED | Log status code + response text; catch ConnectError |

---

## platform-docs-app-port

**Severity:** HIGH

**Trigger:** Calling the docs-app internal API from portal-api (`docs_client.py`)

The docs-app (klai-docs) runs on port **3010**, not 3000. Docker service name is `docs-app`.

**Wrong:**
```python
base_url="http://docs-app:3000"
```

**Correct:**
```python
base_url="http://docs-app:3010/docs"
```

---

## platform-docs-app-basepath

**Severity:** HIGH

**Trigger:** Calling any API endpoint on docs-app

The Next.js app has `basePath: "/docs"` in `next.config.ts`. All routes — including internal API routes — are served under `/docs/api/...`, not `/api/...`.

**Wrong:**
```
POST http://docs-app:3010/api/orgs/{slug}/kbs   → 404 Not Found
```

**Correct:**
```
POST http://docs-app:3010/docs/api/orgs/{slug}/kbs
```

Use `base_url="http://docs-app:3010/docs"` in the httpx client so relative paths resolve correctly.

---

## platform-docs-app-visibility-values

**Severity:** HIGH

**Trigger:** Creating a KB via the docs-app API when the portal visibility is `internal`

The docs-app DB has a check constraint that only accepts `public` or `private`. The portal uses `internal` as a third visibility option. Passing `internal` causes a 500 from docs-app.

**Wrong:**
```python
json={"visibility": "internal"}  # → 500 Internal Server Error
```

**Correct:**
```python
docs_visibility = "public" if visibility == "public" else "private"
json={"visibility": docs_visibility}
```

Map portal `internal` → docs-app `private` before calling the API.

---

## platform-docs-app-error-logging

**Severity:** MEDIUM

**Trigger:** Debugging docs-app integration failures from portal-api logs

Without the response body in the log, all failures look identical (`httpx.HTTPStatusError`). Always log status code + response text.

**Wrong:**
```python
log.exception("Gitea provisioning failed for KB slug=%s", kb_slug)
```

**Correct:**
```python
log.error(
    "Gitea provisioning failed for KB slug=%s: %s %s",
    kb_slug,
    exc.response.status_code,
    exc.response.text[:500],
)
```

Also catch `httpx.ConnectError` separately — it has no `.response` attribute and accessing it raises `AttributeError`.

---

## See Also

- `patterns/platform.md` — docs-app integration patterns
- `docs/CLAUDE.md` — docs-app project instructions
