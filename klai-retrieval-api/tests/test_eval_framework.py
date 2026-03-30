"""Tests for the RAGAS evaluation framework structure and helpers (SPEC-EVIDENCE-001, R8).

These tests validate the evaluation framework's configuration loading,
query set structure, and scoring helpers -- NOT the actual RAGAS execution
(which requires an LLM endpoint).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml


EVAL_DIR = Path(__file__).resolve().parent.parent / "evaluation"


class TestEvalConfig:
    """eval_config.yaml must be well-formed and contain required keys."""

    @pytest.fixture
    def config(self) -> dict:
        config_path = EVAL_DIR / "eval_config.yaml"
        assert config_path.exists(), f"eval_config.yaml not found at {config_path}"
        return yaml.safe_load(config_path.read_text())

    def test_config_has_required_keys(self, config):
        """Config must contain model, metrics, thresholds, and dimensions."""
        assert "model" in config
        assert "metrics" in config
        assert "thresholds" in config
        assert "dimensions" in config

    def test_config_model_is_klai(self, config):
        """Model must use klai-large (Klai model policy: no OpenAI/Anthropic models)."""
        assert config["model"] == "klai-large"

    def test_config_metrics_contains_ragas_metrics(self, config):
        """Metrics list must include context_precision, faithfulness, answer_relevancy."""
        metrics = config["metrics"]
        assert "context_precision" in metrics
        assert "faithfulness" in metrics
        assert "answer_relevancy" in metrics

    def test_config_metrics_contains_retrieval_metrics(self, config):
        """Metrics list must include ndcg_at_10 and recall_at_10."""
        metrics = config["metrics"]
        assert "ndcg_at_10" in metrics
        assert "recall_at_10" in metrics

    def test_config_dimensions_are_toggleable(self, config):
        """Each dimension must be individually toggleable."""
        dims = config["dimensions"]
        assert "content_type" in dims
        assert "temporal_decay" in dims
        assert "assertion_mode" in dims
        # Each dimension must have an enabled flag
        for key, dim in dims.items():
            assert "enabled" in dim, f"Dimension {key} missing 'enabled' flag"

    def test_config_thresholds_has_significance_level(self, config):
        """Thresholds must include a significance level for Wilcoxon test."""
        assert "significance_level" in config["thresholds"]
        assert config["thresholds"]["significance_level"] == 0.05


class TestCuratedQueries:
    """test_queries_curated.json must be well-formed and contain ground truth."""

    @pytest.fixture
    def queries(self) -> list[dict]:
        queries_path = EVAL_DIR / "test_queries_curated.json"
        assert queries_path.exists(), f"test_queries_curated.json not found at {queries_path}"
        return json.loads(queries_path.read_text())

    def test_queries_is_nonempty_list(self, queries):
        """Must contain at least one query."""
        assert isinstance(queries, list)
        assert len(queries) >= 1

    def test_each_query_has_required_fields(self, queries):
        """Each query must have query, ground_truth_chunks, and expected_answer."""
        for i, q in enumerate(queries):
            assert "query" in q, f"Query {i} missing 'query'"
            assert "ground_truth_chunks" in q, f"Query {i} missing 'ground_truth_chunks'"
            assert "expected_answer" in q, f"Query {i} missing 'expected_answer'"
            assert "org_id" in q, f"Query {i} missing 'org_id'"

    def test_ground_truth_chunks_are_lists(self, queries):
        """ground_truth_chunks must be a list of chunk IDs."""
        for i, q in enumerate(queries):
            assert isinstance(q["ground_truth_chunks"], list), (
                f"Query {i}: ground_truth_chunks must be a list"
            )


class TestEvalRunner:
    """eval_runner.py must be importable and expose key functions."""

    def test_eval_runner_module_exists(self):
        """evaluation/eval_runner.py must exist."""
        runner_path = EVAL_DIR / "eval_runner.py"
        assert runner_path.exists(), f"eval_runner.py not found at {runner_path}"

    def test_eval_runner_has_main_function(self):
        """eval_runner.py must define a main() coroutine."""
        runner_path = EVAL_DIR / "eval_runner.py"
        source = runner_path.read_text()
        assert "async def main(" in source, "eval_runner.py must define async def main()"

    def test_eval_runner_has_run_baseline(self):
        """eval_runner.py must define a run_baseline() function."""
        runner_path = EVAL_DIR / "eval_runner.py"
        source = runner_path.read_text()
        assert "run_baseline" in source, "eval_runner.py must define run_baseline"

    def test_eval_runner_has_run_evidence_tier(self):
        """eval_runner.py must define a run_evidence_tier() function."""
        runner_path = EVAL_DIR / "eval_runner.py"
        source = runner_path.read_text()
        assert "run_evidence_tier" in source, "eval_runner.py must define run_evidence_tier"

    def test_eval_runner_has_wilcoxon_comparison(self):
        """eval_runner.py must include Wilcoxon signed-rank test."""
        runner_path = EVAL_DIR / "eval_runner.py"
        source = runner_path.read_text()
        assert "wilcoxon" in source.lower(), (
            "eval_runner.py must include wilcoxon signed-rank test"
        )

    def test_eval_runner_has_retry_logic(self):
        """eval_runner.py must include retry/backoff for rate-limited LLM calls."""
        runner_path = EVAL_DIR / "eval_runner.py"
        source = runner_path.read_text()
        assert "retry" in source.lower() or "backoff" in source.lower(), (
            "eval_runner.py must include retry/backoff logic"
        )

    def test_eval_runner_no_forbidden_models(self):
        """eval_runner.py must NOT reference OpenAI/Anthropic model names."""
        runner_path = EVAL_DIR / "eval_runner.py"
        source = runner_path.read_text().lower()
        forbidden = ["gpt-4", "gpt-3.5", "claude-", "text-davinci"]
        for model in forbidden:
            assert model not in source, (
                f"eval_runner.py references forbidden model: {model}"
            )

    def test_eval_runner_uses_per_dimension_isolation(self):
        """eval_runner.py must support per-dimension isolation testing."""
        runner_path = EVAL_DIR / "eval_runner.py"
        source = runner_path.read_text()
        # Must iterate over dimensions or have dimension-specific logic
        assert "dimensions" in source, (
            "eval_runner.py must reference dimensions for per-dimension isolation"
        )
