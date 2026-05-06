# F6 — parent_lookup gebruikt format-string i.p.v. structlog kwargs

**Severity:** NIT — log queryability
**Status:** OPEN — needs verification

## Initial finding

[`klai-retrieval-api/retrieval_api/services/parent_lookup.py:39`](../../../klai-retrieval-api/retrieval_api/services/parent_lookup.py#L39):

```python
logger.warning("parent_lookup_no_pool count=%d", len(unique_ids))
```

En [`parent_lookup.py:48`](../../../klai-retrieval-api/retrieval_api/services/parent_lookup.py#L48):

```python
logger.warning(
    "parent_lookup_failed count=%d error=%s",
    len(unique_ids),
    str(exc)[:200],
)
```

Andere modules in dezelfde service gebruiken structlog kwargs:

```python
logger.warning("retrieval_events_cap_hit", pending=len(_pending), cap=cap, event_type=event_type)
```

`%d` interpolatie maakt `count` geen losse field maar onderdeel van de message — onqueryable in VictoriaLogs (kan geen `_time:5m AND parent_lookup_no_pool AND count>100` doen).

Bovendien: `error=str(exc)[:200]` is precies de TRY401/anti-pattern uit `klai/projects/portal-logging-py.md`. Beter: `exc_info=True`.

## Open vragen voor verificatie

1. Gebruikt portal-logging-py rule (`/Users/mvletter/Developer/Klai/.claude/rules/klai/projects/portal-logging-py.md`) op retrieval-api consistent? Vind alle instances in retrieval-api die format-string-style loggen.
2. Is `parent_lookup_no_pool` ooit afgevuurd in productie? Als ja: hebben we last gehad van count-veld onqueryability bij debugging?

## Voorgestelde fix

```python
# Line 39:
logger.warning("parent_lookup_no_pool", count=len(unique_ids))

# Line 47-52:
except Exception:
    logger.warning(
        "parent_lookup_failed",
        count=len(unique_ids),
        exc_info=True,
    )
```

## Verification

**Status:** CONFIRMED — finding is real, but smaller in impact than the title suggests.

**Rule check.** `.claude/rules/klai/projects/portal-logging-py.md` mandates:
1. structlog kwargs (`logger.warning("event", count=N, error=str(e))`) — not `%`-format. Quote: *"Pass structured key/value pairs — not string concatenation. IDs, counts, and status values as separate kwargs make logs queryable in VictoriaLogs."*
2. `exc_info=True` for any except-block log (HARD rule). Quote: *"`logger.warning("failed", error=str(exc))` throws away the stack frame. Prefer `exc_info=True` over string interpolation of the exception."*
3. ruff `TRY401` is enabled to catch the `error=str(exc)` anti-pattern.

Both lines 39 and 48 violate rule 1 (`count=%d` is in the message string, not a kwarg). Line 48 additionally violates rule 2 (`error=str(exc)[:200]` instead of `exc_info=True`).

**Ruff config.** `klai-retrieval-api/pyproject.toml` already enables `G` (flake8-logging-format) + `TRY` rule families with `TRY401` un-ignored — so the lint config is correct. The reason it doesn't fire on these lines: retrieval-api has no `quality` CI job that runs `uv run ruff check .` on every PR (unlike portal-api's `portal-api.yml`). The local lint would catch this; CI does not.

**Scope across the codebase.**
- **retrieval-api `services/*.py` (mid-migration):** 5 other format-string warning sites — `coreference.py:63`, `gate.py:65`, `reranker.py:59`, `tei.py:63`, `main.py:42`. Some sibling files (`events.py`, `graph_search.py`, `router.py`, `rate_limit.py`, `search.py`) already use the structlog-kwargs idiom, so the codebase is partially migrated.
- **retrieval-api `evaluation/eval_runner.py`:** ~13 lines of `%s`/`%d` style. Standalone CLI script, not on the request path — much lower priority.
- **portal-api:** ~100+ format-string log lines across `app/api/*.py` and `app/services/*.py`. Not in scope of this audit (separate cleanup).
- **`error=str(exc)` without `exc_info=True`:** ~50+ instances repo-wide (mailer, knowledge-ingest, portal-api). Out of audit scope.

**Production data.** `docker logs --since 30d klai-core-retrieval-api-1 | grep -c parent_lookup` = **0**. The line has not fired in production in the last 30 days, so the queryability gap has caused no actual debug pain. Pure hygiene fix.

## Recommended fix

Two-line change in `parent_lookup.py`:

```python
# Line 39:
logger.warning("parent_lookup_no_pool", count=len(unique_ids))

# Lines 47-52:
except Exception:
    logger.warning(
        "parent_lookup_failed",
        count=len(unique_ids),
        exc_info=True,
    )
```

Out of scope for this finding (track separately if desired):
- Migrate the other 5 retrieval-api `services/*.py` sites to kwargs.
- Add a `quality` CI job to retrieval-api that runs `uv run ruff check .` so future regressions of `G`/`TRY` rules fail CI — without it the lint config is decorative.

## Risk if not fixed

**Severity:** NIT, confirmed. Two concrete (small) consequences:

1. **Queryability gap.** When the line eventually fires (e.g. parent-chunks DB outage), VictoriaLogs cannot answer `parent_lookup_no_pool AND count>100` — count is buried in the message string. Operator falls back to grep-on-message, slower triage.
2. **Stack-frame loss on the failure path (line 48).** If `pool.fetch()` ever raises something unexpected (asyncpg version bump, parent_chunks schema drift), there is no traceback in the log — only the truncated `str(exc)[:200]`. Same anti-pattern that pitfall `data-before-code` and the rule's HARD section warn about. Cost of fix is two lines; cost of debugging without the traceback at 3am is ~30 min of "where did this come from?".

No production impact today (0 hits in 30d). Net cost-vs-benefit of the fix is strongly positive — fixes belong in the same PR as any other change to this file.
