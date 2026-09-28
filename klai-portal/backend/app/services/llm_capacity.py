"""Recognise "LiteLLM has no capacity left for this alias".

Same semantics as klai-knowledge-ingest's ``knowledge_ingest/llm_capacity.py``,
verified there against litellm==1.96.2. klai-judge runs on the Vibe allowance
it shares with klai-ingest, with no fallback and a daily ``max_budget``
(``deploy/litellm/config.yaml``), so both answers below are expected states:

* Allowance full: Mistral answers 402 and the proxy passes the status through.
* Daily budget spent, or every account marked FULL: LiteLLM raises "No
  deployments available ...", which ``ProxyException`` turns into HTTP 429
  (``proxy/_types.py:3465-3466``).

An ordinary rate-limit 429 does not match: the proxy already retried it and
the next call can succeed.
"""

from __future__ import annotations

import httpx

_NO_DEPLOYMENTS = "No deployments available"


def is_llm_capacity_error(exc: BaseException) -> bool:
    """True when ``exc`` or an exception it was raised from is a capacity answer."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, httpx.HTTPStatusError):
            response = current.response
            return response.status_code == 402 or (response.status_code == 429 and _NO_DEPLOYMENTS in response.text)
        current = current.__cause__ or current.__context__
    return False
