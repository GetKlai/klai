"""Contract tests for retrieval decision tracing."""

from __future__ import annotations

import time

import pytest

from retrieval_api.tracing import RetrievalTrace


def _populated_trace(level: str = "full") -> RetrievalTrace:
    trace = RetrievalTrace(
        request_id="request-123",
        org_id="org-123",
        scope="org",
        telemetry_level=level,
        started_at=time.perf_counter(),
    )
    trace.content(
        "coreference_rewrite",
        {"original": "raw question", "resolved": "resolved question", "source": "test"},
    )
    for key, value in {
        "coreference_ms": 1.0,
        "embedding_ms": 2.0,
        "gate_margin": None,
        "gate_bypassed": False,
        "gate_ms": 0.0,
        "router": {"router_layer_used": "centroid"},
        "search_ms": 3.0,
        "search_candidates_count": 5,
        "rerank_ms": 4.0,
        "reranker_scores_top5": [0.9],
        "quality_floor_filtered": 0,
        "source_select": {"source_select_mode": "diversify"},
        "quality_boost_applied": False,
        "evidence_shadow_mode": False,
        "link_expand": {"enabled": True},
        "confidence_band": "high",
        "total_ms": 10.0,
    }.items():
        trace.meta(key, value)
    return trace


def test_decision_record_preserves_all_flat_compatibility_fields() -> None:
    record = _populated_trace().to_decision_record()

    assert set(
        (
            "coreference_rewrite",
            "coreference_ms",
            "embedding_ms",
            "gate_margin",
            "gate_bypassed",
            "gate_ms",
            "router",
            "search_ms",
            "search_candidates_count",
            "rerank_ms",
            "reranker_scores_top5",
            "quality_floor_filtered",
            "source_select",
            "quality_boost_applied",
            "evidence_shadow_mode",
            "link_expand",
            "confidence_band",
            "total_ms",
            "retention_class",
        )
    ).issubset(record)
    assert record["retention_class"] == "content"


@pytest.mark.parametrize("level", ["shadow", "off"])
def test_non_full_render_removes_top_level_and_step_content(level: str) -> None:
    trace = _populated_trace(level)
    with trace.step("coreference") as step:
        step.content("resolved_query", "resolved question")
        step.meta("source", "retrieval-api")

    record = trace.to_decision_record()

    assert "coreference_rewrite" not in record
    assert "resolved question" not in repr(record)
    assert record["trace_steps"][0]["source"] == "retrieval-api"
    assert record["retention_class"] == "metadata"


@pytest.mark.parametrize(
    ("level", "expected_retention", "content_expected"),
    [
        ("off", "metadata", False),
        ("shadow", "metadata", False),
        ("full", "content", True),
    ],
)
def test_all_candidate_transform_steps_render_safely_at_every_telemetry_level(
    level: str,
    expected_retention: str,
    content_expected: bool,
) -> None:
    trace = _populated_trace(level)
    trace.content("query_text", "private raw query")
    safe_details = {
        "qdrant_search": {"candidates_returned": 4},
        "rerank": {
            "candidates_in": 4,
            "candidates_out": 3,
            "link_expand_boost_enabled": True,
            "link_expand_boost_factor": 1.2,
            "link_expand_boosted_count": 1,
        },
        "quality_floor": {"candidates_in": 3, "candidates_out": 2, "filtered_count": 1},
        "source_select": {"candidates_in": 2, "candidates_out": 2, "mode": "diversify"},
        "quality_boost": {"candidates_in": 2, "candidates_out": 2, "boosted_count": 1},
        "parent_lookup": {"parent_ids_requested": 1, "parents_found": 1},
        "response_build": {"chunks_built": 2, "parent_text_chunks": 1},
    }
    for name, details in safe_details.items():
        trace.record_ok(name, 1.0, **details)

    record = trace.to_decision_record()

    assert [step["name"] for step in record["trace_steps"]] == list(safe_details)
    assert record["retention_class"] == expected_retention
    assert ("query_text" in record) is content_expected
    assert ("coreference_rewrite" in record) is content_expected
    for content_value in ("private raw query", "raw question", "resolved question"):
        assert (content_value in repr(record)) is content_expected
    for step in record["trace_steps"]:
        assert "private raw query" not in repr(step)
        assert "chunk_id" not in repr(step)
        assert "https://" not in repr(step)


def test_steps_keep_pipeline_order_and_stable_skip_reason() -> None:
    trace = _populated_trace("shadow")
    trace.record_ok("coreference", 1.0, source="caller")
    trace.record_ok("embed", 2.0)
    trace.mark_skipped("gate", "disabled_by_config")
    trace.mark_skipped("router", "scope_not_applicable")

    steps = trace.to_decision_record()["trace_steps"]

    assert [step["name"] for step in steps] == ["coreference", "embed", "gate", "router"]
    assert steps[2]["status"] == "skipped"
    assert steps[2]["skipped_reason"] == "disabled_by_config"


def test_step_wrapper_records_error_and_reraises_by_default() -> None:
    trace = _populated_trace("shadow")

    with pytest.raises(TimeoutError, match="provider payload must not be rendered"):
        with trace.step("graph_search"):
            raise TimeoutError("provider payload must not be rendered")

    step = trace.to_decision_record()["trace_steps"][0]
    assert step["status"] == "error"
    assert step["error_type"] == "TimeoutError"
    assert "error_message_safe" not in step
    assert "provider payload" not in repr(step)
    assert "traceback" not in repr(step).lower()


def test_fail_open_is_explicit_and_safe_message_is_opt_in() -> None:
    trace = _populated_trace("shadow")

    with trace.step("graph_search", fail_open=True, safe_error_message="upstream timeout"):
        raise TimeoutError("private query text")

    step = trace.to_decision_record()["trace_steps"][0]
    assert step["status"] == "error"
    assert step["error_message_safe"] == "upstream timeout"
    assert "private query text" not in repr(step)


def test_invalid_step_name_and_skip_reason_are_rejected() -> None:
    trace = _populated_trace("shadow")

    with pytest.raises(ValueError, match="Unknown retrieval trace step"):
        trace.record_ok("search", 1.0)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="Unknown retrieval trace skip reason"):
        trace.mark_skipped("router", "not_enough_labels")  # type: ignore[arg-type]


def test_log_kwargs_include_request_identity_and_safe_record() -> None:
    kwargs = _populated_trace("shadow").to_log_kwargs()

    assert kwargs["request_id"] == "request-123"
    assert kwargs["org_id"] == "org-123"
    assert kwargs["scope"] == "org"
    assert kwargs["telemetry_level"] == "shadow"
    assert "coreference_rewrite" not in kwargs
