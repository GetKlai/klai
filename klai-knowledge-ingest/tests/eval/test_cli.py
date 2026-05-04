"""CLI smoke tests for the ad-hoc eval runner (SPEC-RAG-EVAL-001 Unit 5)."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from unittest.mock import patch

import pytest

from knowledge_ingest.eval.__main__ import _build_parser, main


def test_parser_requires_suite() -> None:
    """--suite is mandatory."""
    parser = _build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_parser_accepts_suite_only() -> None:
    """--variant defaults to None when omitted (resolved by run_evaluation)."""
    args = _build_parser().parse_args(["--suite", "chat"])
    assert args.suite == "chat"
    assert args.variant is None


def test_parser_accepts_variant_override() -> None:
    args = _build_parser().parse_args(["--suite", "chat", "--variant", "contextual_v1"])
    assert args.suite == "chat"
    assert args.variant == "contextual_v1"


def test_main_invokes_run_evaluation_and_prints_json() -> None:
    """main() forwards args to run_evaluation and prints the result as JSON."""
    fake_result = {
        "suite": "chat",
        "variant": "smoke-test",
        "queries_processed": 30,
        "rows_written": 30,
    }

    with patch(
        "knowledge_ingest.eval.__main__.run_evaluation",
        return_value=fake_result,
    ) as mock_run:
        buf = io.StringIO()
        with redirect_stdout(buf):
            exit_code = main(["--suite", "chat", "--variant", "smoke-test"])

    assert exit_code == 0
    mock_run.assert_called_once_with(suite="chat", variant="smoke-test")
    parsed = json.loads(buf.getvalue())
    assert parsed == fake_result


def test_main_passes_none_variant_when_unset() -> None:
    """When --variant is omitted, main() forwards variant=None to run_evaluation."""
    fake_result = {
        "suite": "chat",
        "variant": "baseline",
        "queries_processed": 0,
        "rows_written": 0,
    }
    with patch(
        "knowledge_ingest.eval.__main__.run_evaluation",
        return_value=fake_result,
    ) as mock_run:
        buf = io.StringIO()
        with redirect_stdout(buf):
            exit_code = main(["--suite", "chat"])

    assert exit_code == 0
    mock_run.assert_called_once_with(suite="chat", variant=None)
