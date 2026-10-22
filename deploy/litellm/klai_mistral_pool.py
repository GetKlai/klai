"""
Enforces the "move to the next Mistral Pro organisation only when the
current one is FULL" rule across every klai-* Mistral alias (klai-primary,
klai-fast, klai-medium, klai-large, klai-ingest, klai-judge).

FULL is a property of an account (the deployment's API key), not of an
`order` number: klai-ingest's only deployment is `order: 1` on a different
key than the chat aliases' `order: 1`, so a 402 on one must neither move the
chat aliases off the Klai organisation nor stop ingest when the Klai
organisation is full. `order` still decides which non-FULL account an alias
uses first.

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
   FULL-unverified promotion. Also builds self._known_deployment_accounts
   (deployment id -> account) from whatever it sees, since the failure hook
   below needs it and does not otherwise get the deployment's key or order.
   Net effect:
   - Healthy: unaffected -- order 1 was already the only eligible order.
   - Persistent 429 / rpm-budget exhaustion / any non-402 failure on order
     1: order 1 is not FULL, so order 2 never survives this filter. When
     the router's own order-based fallback then asks for `_target_order=2`
     (a request kwarg the router pops right after this hook), this hook
     rewrites it to the eligible order, so the router narrows to what we
     returned (order 1, or empty). Order 2 is never called. 1.96.2 did that
     narrowing itself ("target_order doesn't match any deployment -- return
     all"); 1.105.1's `get_order_filtered_deployments` returns an empty list
     instead, and the resulting "No deployments available"
     RouterRateLimitError -- a ValueError without a status_code, so an HTTP
     500 at the proxy -- replaced Mistral's 429 as the request's final error.
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
   the failing account is looked up via self._known_deployment_accounts
   built in point 1 -- determined from the deployment that was actually
   called (model_info id -> account), not arithmetic on `target_order`.
   Marks that account FULL only when `exception.status_code == 402`.
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

Which keys are FULL: the host probe is the canary. Before October 2026
a 402 benched a key for 5 minutes and the next real user request probed it
again, so while one workspace stayed exhausted for three days, one user
request per alias every five minutes paid a failed first try before
failing over. Now `mistral-api-probe.sh` (systemd timer, every 2 minutes)
completes one token with every MISTRAL_API_KEY* in the host env file and
writes /opt/klai/state/litellm/mistral-keys.json, mounted read-only here:
`{"generated_at": ..., "keys": {"<fingerprint>": {"state": "ok"|"full"|
"error", "since": ..., "http_status": ...}}}`, keyed by the first 12 hex
chars of sha256(key) so the file holds no secret. The key here is the
resolved value: the proxy replaces every `os.environ/` reference in
config.yaml, model_list included, before it builds the router
(proxy_server.py `_check_for_os_environ_vars`, litellm 1.96.2).
MISTRAL_VIBE_KEY (klai-ingest, klai-judge) is not probed: it is Vibe-scoped
(see config.yaml), a mistral-small probe would not test it, and it has no
second order to move to, so it stays on the request-path bench below. A
key the file calls `full` is never offered. A 402 on a real request still benches its key at
once (the probe can be up to 2 minutes behind) for 5 minutes, and a probe
run written after that 402 decides earlier: `ok` releases the key, `full`
keeps it off. The file's age is not judged here: a probe or timer that has
died is the `mistral_api_probe_missing` alert's job, and until it is fixed
the last verdict holds. The 5-minute bench therefore only matters for a key
the probe does not cover (the Vibe key) and for the two minutes between a
402 and the next probe run. Known ceiling: bench expiry is a plain
timestamp, not a single-flight probe, so a burst right at expiry can all
reach a still-full account; concurrent 402s from that burst re-arm the
bench once, not once per request. An asyncio.Lock gating one canary request
is the upgrade path if that burst ever matters.

Every FULL/ok transition is logged as `klai_mistral_pool_state_change
key_fingerprint=<fp> state=<state> source=probe|request`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path

from litellm.integrations.custom_logger import CustomLogger

STATUS_FILE = Path("/app/state/mistral-keys.json")
# The probe runs every 2 minutes; reading more often than this buys nothing
# and this check sits on every deployment pick.
STATUS_FILE_RECHECK_SECONDS = 10
# How long a 402 keeps a key off when no later probe run has judged it. See
# module docstring.
BENCH_SECONDS = 300

logger = logging.getLogger(__name__)


def key_fingerprint(key: str) -> str:
    """Same value mistral-api-probe.sh writes; never the key itself."""
    return hashlib.sha256(key.encode()).hexdigest()[:12]


class _Bench:
    # Not a @dataclass: LiteLLM loads this module without registering it in
    # sys.modules, which crashes dataclass processing under
    # `from __future__ import annotations` (tests/test_callback_module_loading.py).
    __slots__ = ("at", "until")

    def __init__(self, at: float, until: float) -> None:
        self.at = at
        self.until = until


class KlaiMistralPoolHook(CustomLogger):
    """Keeps every klai-* Mistral alias on the lowest non-FULL account order."""

    def __init__(self, status_file: Path = STATUS_FILE) -> None:
        super().__init__()
        self._status_file = status_file
        self._status_checked_at = float("-inf")
        self._status_mtime: float | None = None
        # fingerprint -> state from the last readable file; None when unusable.
        self._probe_states: dict[str, str] | None = None
        # Keyed by account (the resolved API key the router already holds in
        # this process); never logged, only its fingerprint is.
        self._marks: dict[str, _Bench] = {}
        self._known_orders: dict[str, dict[int, str]] = {}
        self._known_deployment_accounts: dict[str, str] = {}

    def _log_change(self, account_fingerprint: str, state: str, source: str) -> None:
        logger.warning(
            "klai_mistral_pool_state_change key_fingerprint=%s state=%s source=%s", account_fingerprint, state, source
        )

    def _reload_status(self) -> None:
        try:
            mtime = self._status_file.stat().st_mtime
        except OSError as exc:
            # Missing is normal until the probe's first run after a deploy;
            # anything else (permissions, a broken mount) is not.
            if not isinstance(exc, FileNotFoundError):
                logger.warning("klai_mistral_pool_status_file_unreadable error=%s", exc)
            self._status_mtime = self._probe_states = None
            return
        if mtime == self._status_mtime:
            return
        self._status_mtime = mtime
        try:
            keys = json.loads(self._status_file.read_text())["keys"]
            states = {fp: entry["state"] for fp, entry in keys.items()}
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            logger.warning("klai_mistral_pool_status_file_unreadable error=%s", exc)
            self._probe_states = None
            return
        previous = self._probe_states or {}
        for fp, state in states.items():
            if previous.get(fp) != state:
                self._log_change(fp, state, "probe")
        self._probe_states = states

    def _probe_states_now(self) -> dict[str, str]:
        now = time.time()
        if now - self._status_checked_at >= STATUS_FILE_RECHECK_SECONDS:
            self._status_checked_at = now
            self._reload_status()
        return self._probe_states or {}

    def _is_full(self, account: str) -> bool:
        fp = key_fingerprint(account)
        state = self._probe_states_now().get(fp)
        if state == "full":
            return True
        mark = self._marks.get(account)
        if mark is None:
            return False
        if state == "ok" and mark.at < self._status_mtime:
            del self._marks[account]
            self._log_change(fp, "ok", "probe")
            return False
        # An ok verdict older than the 402, no verdict, or an unprobed key: the
        # bench decides, so a dead probe cannot keep a key off for good.
        return time.time() < mark.until

    def _mark_full(self, account: str) -> None:
        now = time.time()
        mark = self._marks.get(account)
        if mark is not None and now < mark.until:
            return
        self._marks[account] = _Bench(at=now, until=now + BENCH_SECONDS)
        # Past the early return there was no active bench: a first 402, or one
        # after the bench expired, is a state change either way.
        self._log_change(key_fingerprint(account), "full", "request")

    def _lowest_eligible_order(self, orders: dict[int, str]) -> int | None:
        for order in sorted(orders):
            if not self._is_full(orders[order]):
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

        present_orders: dict[int, str] = {}
        for deployment in healthy_deployments:
            litellm_params = deployment.get("litellm_params") or {}
            order = litellm_params.get("order")
            if order is None:
                continue
            account = str(litellm_params.get("api_key"))
            present_orders[order] = account
            deployment_id = (deployment.get("model_info") or {}).get("id")
            if deployment_id is not None:
                self._known_deployment_accounts[deployment_id] = account

        known = self._known_orders.setdefault(model, {})
        if not present_orders and not known:
            # Not an ordered Mistral alias (e.g. klai-bge-m3): leave untouched.
            return healthy_deployments
        known.update(present_orders)

        eligible_order = self._lowest_eligible_order(known)
        if eligible_order is None:
            # Every known account is FULL.
            return []
        if request_kwargs is not None and "_target_order" in request_kwargs:
            # See module docstring point 1: an order-based fallback hop to a
            # non-eligible order retries the eligible one, so the request ends
            # on the provider's own error instead of an empty selection.
            request_kwargs["_target_order"] = eligible_order

        # May be empty if `eligible_order` isn't in THIS call's healthy_
        # deployments (e.g. cooling down) -- deliberately not falling
        # through to a higher, non-FULL-verified order in that case (see
        # module docstring point 1).
        return [d for d in healthy_deployments if (d.get("litellm_params") or {}).get("order") == eligible_order]

    def _account_of(self, kwargs) -> str | None:
        deployment_id = ((kwargs.get("litellm_params") or {}).get("model_info") or {}).get("id")
        return self._known_deployment_accounts.get(deployment_id)

    async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):
        exception = kwargs.get("exception")
        if getattr(exception, "status_code", None) != 402:
            return
        account = self._account_of(kwargs)
        if account is not None:
            self._mark_full(account)

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        account = self._account_of(kwargs)
        mark = self._marks.get(account) if account is not None else None
        # A request that was already in flight when the 402 came in says
        # nothing about the key now; only one started after the mark clears
        # it. start_time is litellm's naive datetime.now() (utils.py
        # function_setup), so .timestamp() is the same clock as time.time().
        if mark is not None and mark.at < start_time.timestamp():
            del self._marks[account]
            self._log_change(key_fingerprint(account), "ok", "request")

    async def log_success_fallback_event(self, original_model_group, kwargs, original_exception):
        # LiteLLM 1.96.2 logs a spent deployment budget only at debug level
        # (router_strategy/budget_limiter.py) unless the whole request fails,
        # so klai-primary running out and klai-large serving it would go
        # unseen. Its message text is kept so the litellm_budget_exhausted
        # alert matches both this and an outright refusal.
        if "crossed budget" in str(original_exception):
            logger.error("klai_mistral_pool: %s served by fallback: %s", original_model_group, original_exception)


klai_mistral_pool_hook = KlaiMistralPoolHook()
