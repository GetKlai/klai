# Backend Pitfalls

> Python async services (FastAPI, httpx, asyncio) in klai-mono.

## Index
> Keep this index in sync — add a row when adding an entry below.

| Entry | Sev | Rule |
|---|---|---|
| [backend-async-sequential-loop](#backend-async-sequential-loop) | MED | Use `asyncio.gather`, not `await` in a for loop |
| [backend-async-no-per-call-timeout](#backend-async-no-per-call-timeout) | MED | Always set `timeout=` on external httpx calls |
| [backend-config-default-vs-env](#backend-config-default-vs-env) | LOW | Config defaults should not silently override env vars |

---

## backend-async-sequential-loop

**Severity:** MEDIUM

**Problem:** `await` calls inside a `for` loop execute sequentially. When fetching multiple external resources (URLs, API calls), total latency is the sum of all individual calls.

```python
# WRONG — sequential, latency = sum(all fetches)
for url in urls:
    result = await fetch(url)   # waits for each before starting the next
    results.append(result)
```

**Fix:** Use `asyncio.gather` to run all fetches in parallel:

```python
# CORRECT — parallel, latency = max(all fetches)
results = await asyncio.gather(*[fetch(url) for url in urls])
```

**Seen in:** `focus/research-api` web mode — 5 sequential docling URL fetches caused 25-50s latency. After parallelising: ~5-10s.

---

## backend-async-no-per-call-timeout

**Severity:** MEDIUM

**Problem:** `asyncio.gather` runs tasks in parallel but still waits for all of them to finish. If one external call is slow (e.g. 60s httpx timeout), the entire gather blocks until that task times out.

```python
# RISKY — one slow URL blocks the whole gather for up to 120s
results = await asyncio.gather(*[docling.convert_url(url) for url in urls])
```

**Fix:** Wrap each task with `asyncio.wait_for` to enforce a per-call deadline:

```python
_TIMEOUT = 15.0  # seconds per call

async def _fetch(url: str):
    try:
        return await asyncio.wait_for(docling.convert_url(url), timeout=_TIMEOUT)
    except asyncio.TimeoutError:
        logger.warning("Timeout fetching: %s", url)
        return None

results = await asyncio.gather(*[_fetch(url) for url in urls])
```

The outer httpx/service timeout (e.g. 120s) is a safety net — the `wait_for` is the real deadline.

**Seen in:** `focus/research-api` web mode — added `_WEB_URL_TIMEOUT = 15.0` after parallelising.

---

## backend-config-default-vs-env

**Severity:** LOW

**Problem:** A wrong default in `pydantic-settings` `BaseSettings` is silently masked by an env var override in production. The bug only surfaces in fresh deployments that don't have the env var set.

```python
# WRONG default — masked in production by SEARXNG_URL=http://searxng:8080
searxng_url: str = "http://searxng:8888"
```

**Fix:** Always set the default to the real production value. Verify by checking the actual service port (`docker ps`) before writing the default.

**Seen in:** `focus/research-api` config — default port was 8888, actual container port 8080.
