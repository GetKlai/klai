"""Pre-flight graph-build cost estimator + throttled scale-warning hook.

SPEC-GRAPH-SCALE-001 REQ-1. graphiti-core resolves every newly extracted
entity and edge against the ENTIRE existing tenant graph via brute-force
cosine scans in Cypher, so per-episode cost grows linearly with graph size
and total sequential build cost grows quadratically with source-text
volume. Left unchecked, a backfill starts happily and goes silent for days
before failing on FalkorDB's query timeout.

This module gives two things:

* ``estimate_graph_build`` — a pure function ``backfill.py`` calls before
  processing a single episode, to refuse an infeasible build loudly instead
  of starting it.
* ``should_check_graph_scale`` / ``maybe_warn_graph_scale`` — a throttled
  pair the live ingest path (``graph.py::ingest_episode``) calls after every
  successful episode, so an operator hears about the approaching wall while
  a connector's corpus is still growing gradually, not only at rebuild time.

All model constants are read from ``config.py`` settings, not hardcoded here
— REQ-6's future re-measurement of the scaling constants is a config change,
not a code change.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import structlog

from knowledge_ingest.config import settings

logger = structlog.get_logger()

# Throttle window shared by should_check_graph_scale and maybe_warn_graph_scale.
# The two throttle INDEPENDENT things (see should_check_graph_scale's
# docstring) but use the same window.
_THROTTLE_SECONDS = 3600.0

# org_id -> time.monotonic() of the last FalkorDB count query the live-ingest
# hook was allowed to make.
_scale_check_last_run: dict[str, float] = {}
# org_id -> time.monotonic() of the last time maybe_warn_graph_scale actually
# logged a warning.
_scale_warn_last_run: dict[str, float] = {}


def _edge_ceiling() -> int:
    """Edge count where the SCAN TAIL approaches the FalkorDB query timeout.

    Derived from the tail, not the average: production timeouts began at
    ~22k edges under a 1000 ms cap while the average scan there was only
    ~343 ms, so the failing tail runs ``graph_scan_tail_factor`` (~3x) above
    the average. The 0.6 is headroom under the timeout, not a hard boundary.
    """
    return int(
        0.6
        * (settings.graph_falkordb_timeout_ms * 1000)
        / (settings.graph_scan_us_per_edge * settings.graph_scan_tail_factor)
    )


@dataclass(frozen=True)
class BuildEstimate:
    predicted_hours: float
    predicted_final_edges: int
    edge_ceiling: int
    budget_hours: float
    refusal: str | None  # None = proceed; else human-readable reason


def estimate_graph_build(
    total_chars: int,
    current_edge_count: int,
    ann_effective: bool | None = None,
) -> BuildEstimate:
    """Predict sequential graph-build time and refuse an infeasible build.

    Model: ``T = a*C + b*C**2`` for ``C`` in millions of characters (Mchar),
    calibrated on the Voys tenant (6,593,861 chars, 0 pre-existing edges ->
    ~20h, ~17,100 new edges — SPEC-GRAPH-SCALE-001 §1). ``C0`` is the
    Mchar-equivalent of the graph that already exists (derived from
    ``current_edge_count`` via ``edges_per_mchar``), so a rebuild on top of a
    large existing graph is charged the same quadratic resolution-scan cost
    the real system would actually pay — the quadratic term is evaluated
    over ``[C0, C0 + dC]``, not over ``[0, dC]``.

    Refuses (sets ``refusal``) when the predicted build exceeds the operator
    time budget, OR when the predicted final edge count would push
    per-scan query latency past the configured FalkorDB timeout
    (edge_ceiling = 0.6 * timeout / scan_us_per_edge; the 0.6 is tail
    headroom under the timeout, not a hard boundary).
    """
    a = settings.graph_build_hours_per_mchar
    ann_enabled = settings.graph_ann_enabled if ann_effective is None else ann_effective
    b = (
        settings.graph_build_quad_hours_per_mchar2_ann
        if ann_enabled
        else settings.graph_build_quad_hours_per_mchar2
    )
    edges_per_mchar = settings.graph_edges_per_mchar
    budget_hours = settings.graph_build_budget_hours

    c0 = current_edge_count / edges_per_mchar
    dc = total_chars / 1e6

    predicted_hours = a * dc + b * ((c0 + dc) ** 2 - c0**2)
    predicted_final_edges = current_edge_count + round(dc * edges_per_mchar)
    edge_ceiling = _edge_ceiling()

    refusal: str | None = None
    if predicted_hours > budget_hours:
        refusal = (
            f"SPEC-GRAPH-SCALE-001: predicted build time {predicted_hours:.1f}h "
            f"exceeds the operator budget of {budget_hours:.1f}h "
            f"(predicted_final_edges={predicted_final_edges}, "
            f"edge_ceiling={edge_ceiling})"
        )
    elif not ann_enabled and predicted_final_edges > edge_ceiling:
        refusal = (
            f"SPEC-GRAPH-SCALE-001: predicted final edge count "
            f"{predicted_final_edges} exceeds the FalkorDB-timeout-derived "
            f"edge ceiling of {edge_ceiling} "
            f"(predicted build time {predicted_hours:.1f}h, "
            f"budget_hours={budget_hours:.1f})"
        )

    return BuildEstimate(
        predicted_hours=predicted_hours,
        predicted_final_edges=predicted_final_edges,
        edge_ceiling=edge_ceiling,
        budget_hours=budget_hours,
        refusal=refusal,
    )


def should_check_graph_scale(org_id: str) -> bool:
    """Gate the FalkorDB edge-count query on the live ingest path.

    Consulted FIRST, before any FalkorDB I/O — the live ingest hot path
    must not pay a FalkorDB round-trip on every single episode. Returns
    True at most once per org per hour per process, and marks the
    timestamp when it does so a concurrent/immediately-following call is
    throttled too.

    This is a separate throttle from ``maybe_warn_graph_scale``'s own: this
    one decides whether to spend the I/O at all, the other decides whether
    a warning actually gets logged once the count is known.
    """
    now = time.monotonic()
    last = _scale_check_last_run.get(org_id)
    if last is not None and now - last < _THROTTLE_SECONDS:
        return False
    _scale_check_last_run[org_id] = now
    return True


def maybe_warn_graph_scale(org_id: str, current_edge_count: int) -> bool:
    """Warn when a tenant's CURRENT graph alone is already past 50% of a limit.

    Connectors grow a corpus gradually, so a rebuild-time refusal is not
    enough — an operator should hear the wall is approaching before a
    rebuild becomes necessary. Warns when either:

    * the predicted full-rebuild time of the existing corpus-equivalent
      (``a*C0 + b*C0**2``) exceeds 50% of the operator budget, or
    * ``current_edge_count`` exceeds 50% of the timeout-derived edge
      ceiling.

    Pure aside from the log emission and throttle state — does no I/O; the
    caller is responsible for fetching ``current_edge_count``. Throttled to
    at most one warning per org per hour per process. Returns True iff a
    warning was actually logged this call.
    """
    a = settings.graph_build_hours_per_mchar
    ann_enabled = settings.graph_ann_enabled
    b = (
        settings.graph_build_quad_hours_per_mchar2_ann
        if ann_enabled
        else settings.graph_build_quad_hours_per_mchar2
    )
    edges_per_mchar = settings.graph_edges_per_mchar
    budget_hours = settings.graph_build_budget_hours

    c0 = current_edge_count / edges_per_mchar
    predicted_rebuild_hours = a * c0 + b * c0**2
    edge_ceiling = _edge_ceiling()

    over_hours = predicted_rebuild_hours > 0.5 * budget_hours
    over_edges = current_edge_count > 0.5 * edge_ceiling
    if not (over_hours or over_edges):
        return False

    now = time.monotonic()
    last = _scale_warn_last_run.get(org_id)
    if last is not None and now - last < _THROTTLE_SECONDS:
        return False
    _scale_warn_last_run[org_id] = now

    logger.warning(
        "graph_scale_warning",
        org_id=org_id,
        current_edge_count=current_edge_count,
        edge_ceiling=edge_ceiling,
        predicted_rebuild_hours=round(predicted_rebuild_hours, 2),
        spec="SPEC-GRAPH-SCALE-001",
    )
    return True
