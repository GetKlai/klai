"""
Enforces the "move to the next Mistral Pro organisation only when the
current one is FULL" rule across every klai-* Mistral alias (klai-primary,
klai-fast, klai-medium, klai-large).

Mistral signals FULL with HTTP 402 ("Workspace/organisation monthly
spending limit reached"). Being busy is not full: a 429 (upstream rate
limit), an exhausted per-deployment rpm/tpm budget, or a transient 5xx must
not send traffic to the next `order`. config.yaml's router_settings already
get this right for the *retry* path (RateLimitErrorAllowedFails: 1000000
keeps a 429 from cooling a deployment down). What they cannot reach is
LiteLLM 1.96.2's own unconditional "ORDER-BASED FALLBACKS" in router.py
(~L6137-6180): whenever a model group has more than one `order`, ANY
exception other than ContextWindowExceededError / ContentPolicyViolationError
that survives the configured retries makes the router try the next-higher
order automatically -- as eagerly for a persistent 429 as for a genuine 402.
Closing that gap is this hook's main job; it does not duplicate rpm/tpm
enforcement or retry policy. Its one other job is logging a daily spend
ceiling (config.yaml max_budget) that a cross-model fallback absorbed, see
log_success_fallback_event.

Verified against the installed litellm==1.96.2 source
(docker exec klai-core-litellm-1, read-only) and, for anything timing- or
kwargs-shape-sensitive, against the real pinned package with a mocked HTTP
transport (this module's tests mock `httpx.AsyncClient.send`, not
`litellm.acompletion` -- patching `litellm.acompletion` directly bypasses
its `@client` decorator entirely, which is what fires the hooks below; a
test suite built on that patch would never notice this hook going silent):

1. `async_filter_deployments` (CustomLogger hook; router.py ~L7359 calls it
   from `async_get_available_deployment`, BEFORE the built-in `_target_order`
   narrowing at ~L10710) strips every deployment whose `order` is above the
   lowest order that isn't currently marked FULL -- based on every order
   this hook has ever SEEN for that model group (self._known_orders), not
   just the orders present in THIS call's `healthy_deployments`. Cooldown
   filtering runs before this hook (router.py ~L9793-9824: a deployment with
   too many recent failures is dropped from `healthy_deployments` before
   any callback sees it), so "order 1 is missing from this call" can mean
   "order 1 is cooling down after repeated 503s", not "order 1 doesn't
   exist" or "order 1 is FULL" -- only a confirmed 402 (point 2 below) may
   ever promote a higher order. If the lowest non-FULL known order isn't
   present in THIS call's `healthy_deployments` (cooldown, or genuinely
   unhealthy), this returns an EMPTY list rather than falling through to a
   higher order: litellm's own "no deployment" path (a bounded
   RouterRateLimitError) is the correct outcome here, not a silent
   FULL-unverified promotion. Also builds self._known_deployment_orders
   (deployment id -> order) from whatever it sees, since the failure hook
   below needs it and does not otherwise get `order` on its kwargs.
   Net effect:
   - Healthy: unaffected -- order 1 was already the only eligible order.
   - Persistent 429 / rpm-budget exhaustion / any non-402 failure on order
     1: order 1 is not FULL, so order 2 never survives this filter. When
     the router's own order-based fallback then asks for `target_order=2`,
     `litellm.utils._get_order_filtered_deployments` finds no order-2
     candidate to narrow to and, per its own documented fallback ("target_
     order doesn't match any deployment -- return all"), hands back
     whatever we returned (order 1, or empty). Order 2 is never called.
   - Order 1 already marked FULL (from an earlier request -- see point 2):
     order 1 is stripped, order 2 (or the next non-FULL order) is used.

2. `async_log_failure_event` (CustomLogger hook, fired from LiteLLM's own
   per-call `Logging` object -- `logging_obj.async_failure_handler`,
   scheduled via `asyncio.create_task` right where the failing call is
   caught) is the one place in this call path carrying the ACTUAL
   exception (`kwargs["exception"]`), so this is the only signal FULL
   marking acts on -- never an attempt count or a target_order guess. It
   does NOT carry `litellm_params["order"]` (empirically confirmed absent
   here, unlike in `async_filter_deployments`), only `model_info["id"]`, so
   the failing order is looked up via self._known_deployment_orders built
   in point 1 -- exactly the fix for "determine the failed order from the
   deployment that was actually called (model_info id -> order)", not
   arithmetic on `target_order`. Marks that order FULL only when
   `exception.status_code == 402`.
   Registration: this fires correctly from a plain
   `litellm.logging_callback_manager.add_litellm_callback(hook)` -- the
   same call config.yaml's `litellm_settings.callbacks` list triggers via
   `initialize_callbacks_on_proxy` -- because litellm's own `function_setup`
   (run by the `@client` decorator on every real completion call) copies
   `litellm.callbacks` into `_async_failure_callback` on first use
   (`litellm/utils.py`'s `get_dynamic_callbacks`); no separate
   `failure_callback:` config entry is needed. It is scheduled as a
   fire-and-forget task, not awaited before the retry/fallback logic picks
   the next deployment, so marking FULL in time for the SAME request that
   hit the 402 is not guaranteed by contract -- only by there being an
   `await` between the failure and the next deployment pick (there always
   is at least one: the real network round-trip to Mistral, or -- in the
   tests -- the mocked HTTP response). Verified empirically (this module's
   tests) that it does land in time for the same request; if a future
   litellm version removes that gap, the same request would simply fail
   once and the NEXT, independent request would still correctly go to
   order 2 -- never a silent FULL-unverified promotion either way.

Single process: production runs exactly one `litellm` process, no
`--num_workers` flag, no Compose `replicas:` (confirmed via `docker exec
klai-core-litellm-1 ps aux` on 2026-09-25: one PID for the litellm
process). Process-local dict state is therefore correct as-is; sharing it
(Redis, etc.) is only needed if that ever changes to more than one worker
or container replica.

Bench time: 5 minutes. A 402 means a monthly/workspace spending limit that
only a person lifts (raising the limit, enabling pay-as-you-go, adding an
account). A probe that gets another 402 costs nothing and re-arms the
bench, while a short bench means traffic returns within minutes of that
fix instead of up to an hour later (measured need, 2026-09-26: every
account went FULL overnight and recovery waits on the owner). Known ceiling: expiry is a plain
timestamp, not a single-flight probe, so a burst of concurrent requests
right at expiry could all reach the still-full account before any of their
402s re-arms it. Given Mistral's per-deployment rpm caps (45-900 depending
on alias) and how rare an organisation going full actually is, that burst
is small and self-limiting; a real single-flight probe (e.g. an
asyncio.Lock gating exactly one canary request) is the upgrade path if it
ever isn't.
"""

from __future__ import annotations

import logging
import time

from litellm.integrations.custom_logger import CustomLogger

# A 402 marks the failing organisation FULL for this long before the next
# request is allowed to probe it again. See module docstring for why 5 minutes.
BENCH_SECONDS = 300

logger = logging.getLogger(__name__)


class KlaiMistralPoolHook(CustomLogger):
    """Keeps every klai-* Mistral alias on the lowest non-FULL account order."""

    def __init__(self) -> None:
        super().__init__()
        self._full_until: dict[int, float] = {}
        self._known_orders: dict[str, set[int]] = {}
        self._known_deployment_orders: dict[str, int] = {}

    def reset(self) -> None:
        """Test helper: clear all learned/FULL state."""
        self._full_until.clear()
        self._known_orders.clear()
        self._known_deployment_orders.clear()

    def _is_full(self, order: int) -> bool:
        full_until = self._full_until.get(order)
        return full_until is not None and time.monotonic() < full_until

    def _mark_full(self, order: int) -> None:
        self._full_until[order] = time.monotonic() + BENCH_SECONDS

    def _lowest_eligible_order(self, orders: set[int]) -> int | None:
        for order in sorted(orders):
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

        present_orders: set[int] = set()
        for deployment in healthy_deployments:
            litellm_params = deployment.get("litellm_params") or {}
            order = litellm_params.get("order")
            if order is None:
                continue
            present_orders.add(order)
            deployment_id = (deployment.get("model_info") or {}).get("id")
            if deployment_id is not None:
                self._known_deployment_orders[deployment_id] = order

        known = self._known_orders.setdefault(model, set())
        if not present_orders and not known:
            # Not an ordered Mistral alias (e.g. klai-bge-m3): leave untouched.
            return healthy_deployments
        known |= present_orders

        eligible_order = self._lowest_eligible_order(known)
        if eligible_order is None:
            # Every known account is FULL.
            return []

        # May be empty if `eligible_order` isn't in THIS call's healthy_
        # deployments (e.g. cooling down) -- deliberately not falling
        # through to a higher, non-FULL-verified order in that case (see
        # module docstring point 1).
        return [d for d in healthy_deployments if (d.get("litellm_params") or {}).get("order") == eligible_order]

    async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):
        exception = kwargs.get("exception")
        if getattr(exception, "status_code", None) != 402:
            return
        deployment_id = ((kwargs.get("litellm_params") or {}).get("model_info") or {}).get("id")
        order = self._known_deployment_orders.get(deployment_id)
        if order is None:
            return
        self._mark_full(order)

    async def log_success_fallback_event(self, original_model_group, kwargs, original_exception):
        # LiteLLM 1.96.2 logs a spent deployment budget only at debug level
        # (router_strategy/budget_limiter.py) unless the whole request fails,
        # so klai-primary running out and klai-large serving it would go
        # unseen. Its message text is kept so the litellm_budget_exhausted
        # alert matches both this and an outright refusal.
        if "crossed budget" in str(original_exception):
            logger.error("klai_mistral_pool: %s served by fallback: %s", original_model_group, original_exception)


klai_mistral_pool_hook = KlaiMistralPoolHook()
