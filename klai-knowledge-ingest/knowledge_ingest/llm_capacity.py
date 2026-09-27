"""Recognise "LiteLLM has no capacity left for klai-ingest" and pace the retry.

Background LLM work runs on the ``klai-ingest`` alias: one Mistral key, no
fallback, and a daily ``max_budget`` in ``deploy/litellm/config.yaml``. When
that key's monthly allowance is spent, or LiteLLM's daily budget is, every
call fails until a person lifts the limit or the budget window rolls over.
Retrying within minutes cannot succeed, so the affected jobs are deferred
instead of failed.

What the proxy answers, verified against litellm==1.96.2 (the version pinned
by ``.github/workflows/litellm-tests.yml``) by reading the installed source
and by driving a ``litellm.Router`` configured like klai-ingest:

* Key full: Mistral answers 402 and the proxy passes the status through
  (``proxy/common_request_processing.py:2725-2729``).
* Daily budget spent: ``router_strategy/budget_limiter.py:182-185`` raises
  ``ValueError("No deployments available - crossed budget: ...")``. It carries
  no status code, and ``ProxyException.__init__`` (``proxy/_types.py:3465-3466``)
  turns any "No deployments available" message into HTTP 429.
* Every account marked FULL by ``deploy/litellm/klai_mistral_pool.py``: its
  ``async_filter_deployments`` returns an empty list, and the router raises
  ``RouterRateLimitError`` (``router_utils/handle_error.py:71-97``,
  ``types/router.py:730-743``): "No deployments available for selected model,
  Try again in 5 seconds. ... cooldown_list=[]", also HTTP 429.

The same RouterRateLimitError message is used when the only deployment is in
a 60 s cooldown after repeated 5xx answers, which is transient. That case
names the cooling deployment in ``cooldown_list``, so only an empty
``cooldown_list`` counts as capacity. An ordinary rate-limit 429 ("Model rate
limit exceeded", or Mistral's own 429) does not match either message.
"""

from __future__ import annotations

import random
import time

import httpx

# A deferred job waits one hour plus up to 30 minutes of jitter. The pool
# hook re-probes a FULL key every 5 minutes and LiteLLM's daily budget window
# rolls over at an arbitrary hour, so an hourly retry resumes work within about
# an hour of capacity returning while each deferred job spends at most one
# failing call per hour. The jitter spreads a wave deferred in the same minute
# over half an hour, so it does not return to the proxy as one burst.
DEFERRAL_DELAY_SECONDS = 3600
DEFERRAL_JITTER_SECONDS = 1800

# Deferral stops three days after the first capacity failure: long enough to
# cover a weekend before someone raises the Mistral limit, short enough that
# a limit nobody lifts ends in a failed job and an error log instead of an
# indefinite queue. A monthly allowance that stays spent longer than this is a
# decision for a person, not something a queue should wait out.
DEFERRAL_WINDOW_SECONDS = 3 * 24 * 3600

_BUDGET_CROSSED = "No deployments available - crossed budget"
_NO_DEPLOYMENT = "No deployments available for selected model"
_NO_COOLDOWN = "cooldown_list=[]"


class LLMCapacityUnavailable(Exception):
    """LiteLLM has no capacity left for this alias; retrying soon cannot help."""


def is_llm_capacity_error(exc: BaseException) -> bool:
    """True when ``exc`` or an exception it was raised from is a capacity answer."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, LLMCapacityUnavailable):
            return True
        if isinstance(current, httpx.HTTPStatusError):
            return _is_capacity_response(current.response)
        current = current.__cause__ or current.__context__
    return False


def _is_capacity_response(response: httpx.Response) -> bool:
    if response.status_code == 402:
        return True
    if response.status_code != 429:
        return False
    body = response.text
    return _BUDGET_CROSSED in body or (_NO_DEPLOYMENT in body and _NO_COOLDOWN in body)


def deferral_window_spent(deferred_since: int) -> bool:
    return time.time() - deferred_since >= DEFERRAL_WINDOW_SECONDS


def deferral_delay_seconds() -> int:
    return DEFERRAL_DELAY_SECONDS + random.randint(0, DEFERRAL_JITTER_SECONDS)  # noqa: S311
