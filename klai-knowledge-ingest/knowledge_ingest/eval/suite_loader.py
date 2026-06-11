"""
YAML suite loader for the RAGAS evaluation harness (SPEC-RAG-EVAL-001).

Loads a query suite YAML file and validates its structure. Returns a Suite
dataclass containing SuiteQuery entries. Raises SuiteValidationError on
schema violations so the harness fails fast rather than silently skipping
malformed suites.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class SuiteValidationError(ValueError):
    """Raised when a suite YAML fails schema validation."""


@dataclass
class SuiteQuery:
    """One query entry from a suite YAML file."""

    id: str
    query: str
    org_zitadel_id: str
    user_zitadel_id: str | None = None
    reference_answer: str | None = None
    expected_topics: list[str] = field(default_factory=list)
    expected_chunks: list[str] = field(default_factory=list)


@dataclass
class Suite:
    """A loaded and validated query suite."""

    name: str
    description: str
    queries: list[SuiteQuery]


def load_suite(path: Path, *, require_reference_answer: bool = False) -> Suite:
    """Load and validate a suite YAML file.

    Parameters
    ----------
    path:
        Absolute or relative path to the suite ``.yaml`` file.
    require_reference_answer:
        When True, every query must define a non-empty ``reference_answer``.
        Use this for scored suites that should feed RAGAS claim metrics from
        real answers instead of legacy topic labels.

    Returns
    -------
    Suite
        Validated suite ready for evaluation.

    Raises
    ------
    SuiteValidationError
        When the YAML is missing required fields or has structural errors.
    FileNotFoundError
        When the file does not exist.
    """
    raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))

    suite_name = raw.get("suite") or path.stem
    description = raw.get("description", "")
    raw_queries: list[dict[str, Any]] = raw.get("queries", [])

    queries: list[SuiteQuery] = []
    for i, q in enumerate(raw_queries):
        q_id = q.get("id", f"<index {i}>")

        if "query" not in q or not q["query"]:
            raise SuiteValidationError(
                f"Suite {suite_name!r}: query entry {q_id!r} is missing required field 'query'."
            )
        if "org_zitadel_id" not in q or not q["org_zitadel_id"]:
            raise SuiteValidationError(
                f"Suite {suite_name!r}: query entry {q_id!r}"
                " is missing required field 'org_zitadel_id'."
            )
        reference_answer = q.get("reference_answer")
        if reference_answer is not None:
            reference_answer = str(reference_answer).strip() or None
        if require_reference_answer and not reference_answer:
            raise SuiteValidationError(
                f"Suite {suite_name!r}: query entry {q_id!r}"
                " is missing required field 'reference_answer'."
            )

        queries.append(
            SuiteQuery(
                id=q_id,
                query=str(q["query"]),
                org_zitadel_id=str(q["org_zitadel_id"]),
                user_zitadel_id=q.get("user_zitadel_id"),
                reference_answer=reference_answer,
                expected_topics=list(q.get("expected_topics") or []),
                expected_chunks=list(q.get("expected_chunks") or []),
            )
        )

    return Suite(name=suite_name, description=description, queries=queries)
