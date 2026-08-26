"""SPEC-GRAPH-SCALE-001 REQ-1 / AC-1 — pre-flight graph-build estimator.

Covers the calibrated Voys data point, both refusal paths (budget and
edge-ceiling), and the throttled live-ingest warning hook.
"""

from __future__ import annotations

from unittest.mock import patch

from knowledge_ingest import build_estimate
from knowledge_ingest.build_estimate import estimate_graph_build, maybe_warn_graph_scale


def _reset_throttle_state() -> None:
    build_estimate._scale_check_last_run.clear()
    build_estimate._scale_warn_last_run.clear()


def setup_function(_fn) -> None:
    _reset_throttle_state()


# ---------------------------------------------------------------------------
# estimate_graph_build — calibration point
# ---------------------------------------------------------------------------


def test_calibration_point_matches_the_voys_measurement():
    """6,593,861 chars, 0 pre-existing edges -> ~20h, ~17,100 new edges."""
    estimate = estimate_graph_build(total_chars=6_593_861, current_edge_count=0)

    assert abs(estimate.predicted_hours - 20.0) <= 0.5
    assert abs(estimate.predicted_final_edges - 17_100) <= 200
    assert estimate.refusal is None


def test_calibration_point_final_edges_is_an_int():
    estimate = estimate_graph_build(total_chars=6_593_861, current_edge_count=0)
    assert isinstance(estimate.predicted_final_edges, int)


# ---------------------------------------------------------------------------
# Refusal — predicted build time exceeds the operator budget
# ---------------------------------------------------------------------------


def test_refusal_fires_above_the_configured_budget():
    """~20x the Voys corpus, from an empty graph, blows well past 48h."""
    estimate = estimate_graph_build(total_chars=66_000_000, current_edge_count=0)

    assert estimate.refusal is not None
    assert "SPEC-GRAPH-SCALE-001" in estimate.refusal
    assert estimate.predicted_hours > estimate.budget_hours


def test_refusal_is_none_comfortably_under_budget():
    """A small corpus on an empty graph must proceed."""
    estimate = estimate_graph_build(total_chars=100_000, current_edge_count=0)
    assert estimate.refusal is None


# ---------------------------------------------------------------------------
# Refusal — predicted final edge count exceeds the timeout-derived ceiling
# ---------------------------------------------------------------------------


def test_refusal_fires_near_the_edge_ceiling_even_with_a_small_corpus():
    """A graph already near the ceiling refuses even a tiny additional corpus.

    Default constants put the ceiling at
    0.6 * (5000ms * 1000) / 15.6us/edge ~= 192,307 edges. Starting one edge
    below the ceiling and adding any new edges must push predicted_final_edges
    over it, while predicted_hours alone (dominated by the small dC) stays
    under budget -- isolating the edge-ceiling refusal path specifically.
    """
    ceiling_probe = estimate_graph_build(total_chars=0, current_edge_count=0)
    ceiling = ceiling_probe.edge_ceiling

    estimate = estimate_graph_build(total_chars=100_000, current_edge_count=ceiling - 10)

    assert estimate.refusal is not None
    assert "SPEC-GRAPH-SCALE-001" in estimate.refusal
    assert estimate.predicted_final_edges > estimate.edge_ceiling


def test_refusal_absent_comfortably_under_the_edge_ceiling():
    estimate = estimate_graph_build(total_chars=1000, current_edge_count=100)
    assert estimate.refusal is None


# ---------------------------------------------------------------------------
# maybe_warn_graph_scale — throttled 50%-of-limit warning
# ---------------------------------------------------------------------------


def test_warns_above_fifty_percent_of_the_edge_ceiling():
    probe = estimate_graph_build(total_chars=0, current_edge_count=0)
    ceiling = probe.edge_ceiling

    with patch.object(build_estimate.logger, "warning") as mock_warn:
        warned = maybe_warn_graph_scale("org-a", current_edge_count=int(ceiling * 0.6))

    assert warned is True
    mock_warn.assert_called_once()
    args, kwargs = mock_warn.call_args
    assert args[0] == "graph_scale_warning"
    assert kwargs["org_id"] == "org-a"
    assert kwargs["spec"] == "SPEC-GRAPH-SCALE-001"
    assert kwargs["edge_ceiling"] == ceiling


def test_does_not_warn_below_fifty_percent_of_either_limit():
    probe = estimate_graph_build(total_chars=0, current_edge_count=0)
    ceiling = probe.edge_ceiling

    # 5%, not 10%: with the default constants 10% of the edge ceiling lands
    # almost exactly on 50% of the budget-hours limit too (both derive from
    # the same quadratic model), which made that boundary flaky. 5% is
    # comfortably under both.
    with patch.object(build_estimate.logger, "warning") as mock_warn:
        warned = maybe_warn_graph_scale("org-b", current_edge_count=int(ceiling * 0.05))

    assert warned is False
    mock_warn.assert_not_called()


def test_second_call_within_the_hour_is_throttled():
    probe = estimate_graph_build(total_chars=0, current_edge_count=0)
    ceiling = probe.edge_ceiling
    hot_count = int(ceiling * 0.9)

    with patch.object(build_estimate.logger, "warning") as mock_warn:
        first = maybe_warn_graph_scale("org-c", current_edge_count=hot_count)
        second = maybe_warn_graph_scale("org-c", current_edge_count=hot_count)

    assert first is True
    assert second is False
    mock_warn.assert_called_once()


def test_separate_orgs_throttle_independently():
    probe = estimate_graph_build(total_chars=0, current_edge_count=0)
    ceiling = probe.edge_ceiling
    hot_count = int(ceiling * 0.9)

    with patch.object(build_estimate.logger, "warning") as mock_warn:
        first_org = maybe_warn_graph_scale("org-d", current_edge_count=hot_count)
        second_org = maybe_warn_graph_scale("org-e", current_edge_count=hot_count)

    assert first_org is True
    assert second_org is True
    assert mock_warn.call_count == 2


# ---------------------------------------------------------------------------
# should_check_graph_scale — throttle gating the FalkorDB query itself
# ---------------------------------------------------------------------------


def test_should_check_graph_scale_allows_first_call_and_throttles_second():
    first = build_estimate.should_check_graph_scale("org-f")
    second = build_estimate.should_check_graph_scale("org-f")

    assert first is True
    assert second is False


def test_should_check_graph_scale_throttles_independently_per_org():
    first_org = build_estimate.should_check_graph_scale("org-g")
    other_org = build_estimate.should_check_graph_scale("org-h")

    assert first_org is True
    assert other_org is True
