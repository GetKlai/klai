"""
Enforces the "move to the next Mistral Pro organisation only when the
current one is FULL" rule across every klai-* Mistral alias (klai-primary,
klai-fast, klai-medium, klai-large).

Mistral signals FULL with HTTP 402 ("Workspace/organisation monthly
spending limit reached"). Being busy is not full: a 429 (upstream rate
limit) or LiteLLM's own per-deployment rpm/tpm budget being exhausted must
not send traffic to the next `order`. config.yaml's router_settings already
get this right for the *retry* path (RateLimitErrorAllowedFails: 1000000
keeps a 429 from cooling a deployment down). What they cannot reach is
LiteLLM 1.96.2's own unconditional "ORDER-BASED FALLBACKS" in router.py
(~L6137-6180): whenever a model group has more than one `order`, ANY
exception other than ContextWindowExceededError / ContentPolicyViolationError
that survives the configured retries makes the router try the next-higher
order automatically -- as eagerly for a persistent 429 as for a genuine 402.
Closing that gap is this hook's only job; it does not duplicate rpm/tpm
enforcement or retry policy.

Mechanism, verified against the installed litellm==1.96.2 source
(docker exec klai-core-litellm-1, read-only) and against the real pinned
package with `litellm.acompletion` patched (this module's tests):

1. `async_filter_deployments` (CustomLogger hook; router.py ~L7359 calls it
   from `async_get_available_deployment`, BEFORE the built-in `_target_order`
   narrowing at ~L10710) strips every deployment whose `order` is above the
   lowest order that isn't currently marked FULL -- EXCEPT when the request
   is itself LiteLLM's own order-based-fallback attempt (`_target_order` is
   set) AND the order it is skipping past was never retried
   (`metadata["previous_models"]` has at most one entry for it). LiteLLM
   only reaches `_target_order` after every configured retry at the lower
   order is exhausted (retry_policy.RateLimitErrorRetries: 3, i.e. 4
   attempts, for a 429; config's global num_retries: 1, i.e. 2 attempts,
   otherwise) -- UNLESS the error is non-retryable to begin with, which is
   exactly what a 402 is (confirmed empirically: a 402 always reaches the
   fallback after exactly one attempt). So "reached with only one prior
   attempt" is a reliable, config-agnostic proxy for "this could not have
   been retried", without needing the actual exception here (unavailable at
   this point -- see below). Net effect:
   - Healthy: unaffected -- order 1 was already the only eligible order.
   - Persistent 429 / rpm-budget exhaustion on order 1: order 1 is retried
     (`previous_models` grows past 1) before the fallback is even
     attempted, so this exception does not apply, order 2 never survives
     the filter, and `litellm.utils._get_order_filtered_deployments` --
     finding no order-2 candidate to narrow to -- hands back whatever we
     returned (order 1 only; see its own "target_order doesn't match any
     deployment -- return all" fallback). The request keeps
     waiting/retrying on order 1, or fails with the rate-limit error --
     order 2 is never called.
   - A 402 (or any other non-retried failure) on order 1: this SAME
     request is served by order 2 immediately, without waiting for the
     slower, log-event-based FULL marking below.
   - Order 1 already marked FULL (from a previous request): order 1 is
     stripped outright, order 2 (or the next non-FULL order) is returned,
     independently of the above.

2. `log_success_fallback_event` / `log_failure_fallback_event`
   (router_utils/fallback_event_handlers.py) fire once the router's
   order-based fallback has already tried the next order, and hand back
   both `kwargs["_target_order"]` (the order just tried) and
   `original_exception` (why the order below it was abandoned) -- the one
   place in this call path that exposes the actual exception, in
   particular `original_exception.status_code`. Unlike
   `async_log_failure_event`, these run from the router's own recursive
   fallback loop rather than the `@client`-decorated `litellm.acompletion`,
   so they still fire when `litellm.acompletion` is patched directly in
   tests (verified empirically: `async_log_failure_event` stayed silent
   under that same patch). This is what durably marks an order FULL: only
   ever on a confirmed `status_code == 402`, so a one-off non-retried
   failure of some other kind (rule 1 above) affects only the request that
   hit it, never the FULL state later requests see. Mistral organisations
   are ordered contiguously (1, 2, 3, ...; see config.yaml's `order`
   fields), so `target_order - 1` is the order that failed.

Bench time: 1 hour. A 402 means a monthly/workspace spending limit, which
does not reset until the next billing cycle or Mark raising the limit by
hand -- so most re-probes within the hour would just waste a call. An hour
is short enough to notice a same-day limit increase without adding a
second control surface, and self-corrects if wrong: a probe that gets
another 402 simply re-arms the full hour. Known ceiling: expiry is a plain
timestamp, not a single-flight probe, so a burst of concurrent requests
right at expiry could all reach the still-full account before any of their
402s re-arms it. Given Mistral's per-deployment rpm caps (45-900 depending
on alias) and how rare an organisation going full actually is, that burst
is small and self-limiting; a real single-flight probe (e.g. an
asyncio.Lock gating exactly one canary request) is the upgrade path if it
ever isn't.
"""

from __future__ import annotations

import time

from litellm.integrations.custom_logger import CustomLogger

# A 402 marks the failing organisation FULL for this long before the next
# request is allowed to probe it again. See module docstring for why 1 hour.
BENCH_SECONDS = 3600


class KlaiMistralPoolHook(CustomLogger):
    """Keeps every klai-* Mistral alias on the lowest non-FULL account order."""

    def __init__(self) -> None:
        super().__init__()
        self._full_until: dict[int, float] = {}

    def reset(self) -> None:
        """Test helper: clear all FULL state."""
        self._full_until.clear()

    def _is_full(self, order: int) -> bool:
        full_until = self._full_until.get(order)
        return full_until is not None and time.monotonic() < full_until

    def _mark_full(self, order: int) -> None:
        self._full_until[order] = time.monotonic() + BENCH_SECONDS

    def _lowest_eligible_order(self, orders_present: set[int]) -> int | None:
        for order in sorted(orders_present):
            if not self._is_full(order):
                return order
        return None

    async def async_filter_deployments(
        self,
        model,
        healthy_deployments,
        messages,
        request_kwargs=None,
        parent_otel_span=None,
    ):
        if isinstance(healthy_deployments, dict):
            return healthy_deployments

        orders_present = {
            order
            for d in healthy_deployments
            if (order := d.get("litellm_params", {}).get("order")) is not None
        }
        if not orders_present:
            # Not an ordered Mistral alias (e.g. klai-bge-m3): leave untouched.
            return healthy_deployments

        target_order = (request_kwargs or {}).get("_target_order")
        skipped_order = target_order - 1 if isinstance(target_order, int) else None
        if skipped_order in orders_present and not self._is_full(skipped_order):
            metadata = (request_kwargs or {}).get("metadata") or {}
            previous_attempts = len(metadata.get("previous_models") or [])
            if previous_attempts <= 1:
                # Reached target_order without a retry at skipped_order: this
                # request's own failure could not have been retried (see
                # module docstring point 1). Serve it from target_order now;
                # log_success/failure_fallback_event below decides whether
                # that state should outlive this one request.
                return [d for d in healthy_deployments if d.get("litellm_params", {}).get("order") == target_order]

        eligible_order = self._lowest_eligible_order(orders_present)
        if eligible_order is None:
            # Every configured account is FULL.
            return []

        return [d for d in healthy_deployments if d.get("litellm_params", {}).get("order") == eligible_order]

    def _mark_full_if_402(self, kwargs: dict, original_exception: Exception) -> None:
        if getattr(original_exception, "status_code", None) != 402:
            return
        target_order = kwargs.get("_target_order")
        if not isinstance(target_order, int):
            return
        self._mark_full(target_order - 1)

    async def log_success_fallback_event(self, original_model_group, kwargs, original_exception):
        self._mark_full_if_402(kwargs, original_exception)

    async def log_failure_fallback_event(self, original_model_group, kwargs, original_exception):
        self._mark_full_if_402(kwargs, original_exception)


klai_mistral_pool_hook = KlaiMistralPoolHook()
