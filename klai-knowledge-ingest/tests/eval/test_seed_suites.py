"""Schema + invariant tests for the committed seed suites (SPEC-RAG-EVAL-001 Unit 4)."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from knowledge_ingest.eval.suite_loader import Suite, SuiteValidationError, load_suite

SUITES_DIR = Path(__file__).resolve().parents[2] / "knowledge_ingest" / "eval" / "suites"
SHIPPED_SUITES = ["chat", "knowledge_org"]
# The curated mix per suite. The chat suite gained 7 brand-bridging canaries in
# SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001 (REQ-7, commit b9c1d1229); knowledge_org
# keeps the original Unit-4 mix. Each suite's expected mix is therefore distinct.
EXPECTED_MIX_BY_SUITE = {
    "chat": {
        "easy_lookup": 9,
        "vague_pronoun": 9,
        "multi_doc_synthesis": 6,
        "long_tail": 4,
        "edge_case": 2,
        "brand_bridging": 7,
    },
    "knowledge_org": {
        "easy_lookup": 9,
        "vague_pronoun": 9,
        "multi_doc_synthesis": 6,
        "long_tail": 4,
        "edge_case": 2,
    },
}
EXPECTED_QUERIES_BY_SUITE = {
    name: sum(mix.values()) for name, mix in EXPECTED_MIX_BY_SUITE.items()
}  # chat: 37, knowledge_org: 30
# Real Voys tenant org id used by every query in both shipped suites.
VOYS_ORG_ID = "368884765035593759"


@pytest.fixture(params=SHIPPED_SUITES)
def shipped_suite(request: pytest.FixtureRequest) -> Suite:
    """Yield each shipped seed suite as a parsed Suite object."""
    return load_suite(SUITES_DIR / f"{request.param}.yaml")


def test_shipped_suite_loads_without_validation_error(shipped_suite: Suite) -> None:
    """Each shipped YAML must satisfy the suite-loader's schema."""
    assert shipped_suite.name in SHIPPED_SUITES
    assert shipped_suite.description, "suite must carry a non-empty description"


def test_shipped_suite_query_count(shipped_suite: Suite) -> None:
    """Each shipped suite carries its curated query count (chat: 37, knowledge_org: 30)."""
    assert len(shipped_suite.queries) == EXPECTED_QUERIES_BY_SUITE[shipped_suite.name]


def test_shipped_suite_targets_voys(shipped_suite: Suite) -> None:
    """All queries in v1 suites target the Voys tenant."""
    for q in shipped_suite.queries:
        assert q.org_zitadel_id == VOYS_ORG_ID, (
            f"Query {q.id!r} targets {q.org_zitadel_id!r}; v1 is Voys-only"
        )


def test_shipped_suite_query_ids_unique(shipped_suite: Suite) -> None:
    """Query ids must be unique within a suite (used as primary key in eval rows)."""
    ids = [q.id for q in shipped_suite.queries]
    duplicates = [item for item, count in Counter(ids).items() if count > 1]
    assert not duplicates, f"Duplicate query ids in {shipped_suite.name}: {duplicates}"


def test_shipped_suite_mix(shipped_suite: Suite) -> None:
    """Each suite follows its curated mix (chat adds brand_bridging per REQ-7)."""
    raw = (SUITES_DIR / f"{shipped_suite.name}.yaml").read_text(encoding="utf-8")
    actual_mix: Counter[str] = Counter()
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("mix:"):
            label = line.split(":", 1)[1].strip()
            actual_mix[label] += 1
    expected_mix = EXPECTED_MIX_BY_SUITE[shipped_suite.name]
    assert dict(actual_mix) == expected_mix, (
        f"Suite {shipped_suite.name} mix mismatch. Expected {expected_mix}, got {dict(actual_mix)}"
    )


def test_easy_lookup_canaries_have_expected_chunks(shipped_suite: Suite) -> None:
    """Easy-lookup canaries (regression markers) MUST carry expected_chunks."""
    raw = (SUITES_DIR / f"{shipped_suite.name}.yaml").read_text(encoding="utf-8")
    by_id = {q.id: q for q in shipped_suite.queries}
    canary_ids: list[str] = []
    pending_id: str | None = None
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith("- id:"):
            pending_id = stripped.split(":", 1)[1].strip()
        elif stripped == "mix: easy_lookup" and pending_id:
            canary_ids.append(pending_id)

    assert canary_ids, "no easy-lookup canaries detected"
    for cid in canary_ids:
        q = by_id[cid]
        assert q.expected_chunks, (
            f"Easy-lookup canary {cid!r} must declare expected_chunks (regression-detection signal)"
        )


def test_invalid_yaml_raises_validation_error(tmp_path: Path) -> None:
    """A YAML missing the required 'query' field raises SuiteValidationError."""
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "suite: bad\nqueries:\n  - id: x\n    org_zitadel_id: '1'\n",
        encoding="utf-8",
    )
    with pytest.raises(SuiteValidationError, match="missing required field 'query'"):
        load_suite(bad)
