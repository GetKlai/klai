"""Direct characterization tests for _evidence_pack_decision_sources.

Pins the log-safe source-provenance projection BEFORE it is lifted out of
retrieve.py into api/decision_log.py (it previously had only indirect coverage
via the decision_record substring assert in test_api.py). Reached through the
``retrieval_api.api.retrieve`` namespace (which re-exports it) so the same test
passes before and after the move.
"""

from __future__ import annotations

from types import SimpleNamespace

from retrieval_api.api.retrieve import _evidence_pack_decision_sources


def _source(**kw):
    return SimpleNamespace(**kw)


def _pack(sources):
    return SimpleNamespace(sources=sources)


def test_no_sources_attr_returns_empty():
    assert _evidence_pack_decision_sources(object()) == []


def test_sources_none_returns_empty():
    assert _evidence_pack_decision_sources(_pack(None)) == []


def test_sources_not_a_list_returns_empty():
    assert _evidence_pack_decision_sources(_pack({"a": 1})) == []


def test_full_source_projection():
    pack = _pack(
        [
            _source(
                source_id="s1",
                title="T",
                source_url="https://docs.getklai.com/refunds",
                source_label="L",
                evidence_ids=["e1", "e2"],
                relevance_score=0.5,
            )
        ]
    )
    assert _evidence_pack_decision_sources(pack) == [
        {
            "source_id": "s1",
            "title": "T",
            "url": "https://docs.getklai.com/refunds",
            "source_label": "L",
            "evidence_ids": ["e1", "e2"],
            "relevance_score": 0.5,
        }
    ]


def test_relevance_score_rounded_to_4dp():
    pack = _pack([_source(relevance_score=0.123456)])
    assert _evidence_pack_decision_sources(pack)[0]["relevance_score"] == 0.1235


def test_relevance_score_none_passthrough():
    pack = _pack([_source(relevance_score=None)])
    assert _evidence_pack_decision_sources(pack)[0]["relevance_score"] is None


def test_evidence_ids_none_becomes_empty_list():
    pack = _pack([_source(evidence_ids=None)])
    assert _evidence_pack_decision_sources(pack)[0]["evidence_ids"] == []


def test_missing_attrs_default_to_none():
    pack = _pack([_source()])
    assert _evidence_pack_decision_sources(pack)[0] == {
        "source_id": None,
        "title": None,
        "url": None,
        "source_label": None,
        "evidence_ids": [],
        "relevance_score": None,
    }


def test_truncates_to_first_five_sources():
    pack = _pack([_source(source_id=f"s{i}") for i in range(8)])
    out = _evidence_pack_decision_sources(pack)
    assert len(out) == 5
    assert [e["source_id"] for e in out] == ["s0", "s1", "s2", "s3", "s4"]
