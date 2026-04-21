"""RAGAS evaluation runner for evidence-tier scoring (SPEC-EVIDENCE-001, R8).

Compares retrieval quality between flat scoring (baseline) and evidence-tier
scoring (treatment) using RAGAS metrics and statistical tests.

Usage:
    python evaluation/eval_runner.py                     # full evaluation
    python evaluation/eval_runner.py --baseline-only     # only baseline run
    python evaluation/eval_runner.py --dimension content_type  # single dimension

Requirements (install separately, not in main retrieval-api deps):
    pip install ragas scipy numpy

Judge model: klai-large (Mistral Large via LiteLLM proxy).
Never uses OpenAI/Anthropic models.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import random
import time
from pathlib import Path
from typing import Any

import httpx
import yaml

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

EVAL_DIR = Path(__file__).resolve().parent


# -- Configuration loading ----------------------------------------------------


def load_config() -> dict:
    """Load evaluation configuration from eval_config.yaml."""
    config_path = EVAL_DIR / "eval_config.yaml"
    return yaml.safe_load(config_path.read_text())


def load_curated_queries() -> list[dict]:
    """Load curated test queries with ground truth."""
    queries_path = EVAL_DIR / "test_queries_curated.json"
    return json.loads(queries_path.read_text())


# -- Retry logic for rate-limited LLM calls ----------------------------------


async def retry_with_backoff(
    coro_fn,
    *args,
    max_retries: int = 5,
    base_delay: float = 2.0,
    max_delay: float = 60.0,
    **kwargs,
) -> Any:
    """Execute an async function with exponential backoff retry on failure.

    Designed for rate-limited LLM API calls (klai-large via LiteLLM).
    """
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            return await coro_fn(*args, **kwargs)
        except (httpx.HTTPStatusError, httpx.TimeoutException, Exception) as exc:
            last_exc = exc
            if attempt == max_retries - 1:
                break
            delay = min(base_delay * (2 ** attempt) + random.uniform(0, 1), max_delay)
            logger.warning(
                "Attempt %d/%d failed: %s. Retrying in %.1fs...",
                attempt + 1, max_retries, exc, delay,
            )
            await asyncio.sleep(delay)

    raise RuntimeError(f"All {max_retries} retry attempts failed") from last_exc


# -- Retrieval execution ------------------------------------------------------


async def run_retrieve(
    query: str,
    org_id: str,
    retrieval_url: str,
    scope: str = "org",
    top_k: int = 10,
) -> dict:
    """Execute a single retrieval request against the retrieval API."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{retrieval_url}/retrieve",
            json={
                "query": query,
                "org_id": org_id,
                "scope": scope,
                "top_k": top_k,
            },
        )
        resp.raise_for_status()
        return resp.json()


# -- Scoring helpers -----------------------------------------------------------


def compute_ndcg_at_k(
    retrieved_ids: list[str],
    ground_truth_ids: list[str],
    k: int = 10,
) -> float:
    """Compute NDCG@k for a single query.

    retrieved_ids: ordered list of chunk IDs returned by retrieval.
    ground_truth_ids: set of relevant chunk IDs.
    """
    import math

    relevance = [1.0 if cid in ground_truth_ids else 0.0 for cid in retrieved_ids[:k]]

    # DCG
    dcg = sum(rel / math.log2(i + 2) for i, rel in enumerate(relevance))

    # Ideal DCG
    ideal_rels = sorted(relevance, reverse=True)
    idcg = sum(rel / math.log2(i + 2) for i, rel in enumerate(ideal_rels))

    return dcg / idcg if idcg > 0 else 0.0


def compute_recall_at_k(
    retrieved_ids: list[str],
    ground_truth_ids: list[str],
    k: int = 10,
) -> float:
    """Compute Recall@k: fraction of ground truth chunks in the top-k results."""
    if not ground_truth_ids:
        return 0.0
    top_k = set(retrieved_ids[:k])
    hits = len(top_k.intersection(ground_truth_ids))
    return hits / len(ground_truth_ids)


# -- Baseline and evidence-tier runs ------------------------------------------


async def run_baseline(
    queries: list[dict],
    config: dict,
) -> list[dict]:
    """Run retrieval with flat scoring (all evidence flags disabled).

    Returns a list of result dicts, one per query.
    """
    retrieval_url = config.get("retrieval_url", "http://localhost:8000")
    retry_cfg = config.get("retry", {})
    results = []

    # Disable all evidence tier dimensions for baseline
    env_overrides = {
        "EVIDENCE_SHADOW_MODE": "false",
        "EVIDENCE_CONTENT_TYPE_ENABLED": "false",
        "EVIDENCE_TEMPORAL_DECAY_ENABLED": "false",
        "EVIDENCE_ASSERTION_MODE_ENABLED": "false",
    }

    for i, q in enumerate(queries):
        logger.info("Baseline query %d/%d: %s", i + 1, len(queries), q["query"][:60])
        try:
            # Set env before query (for local single-process testing)
            original_env = {k: os.environ.get(k) for k in env_overrides}
            os.environ.update(env_overrides)

            resp = await retry_with_backoff(
                run_retrieve,
                q["query"],
                q["org_id"],
                retrieval_url,
                max_retries=retry_cfg.get("max_retries", 5),
                base_delay=retry_cfg.get("base_delay_seconds", 2.0),
                max_delay=retry_cfg.get("max_delay_seconds", 60.0),
            )

            retrieved_ids = [c["chunk_id"] for c in resp.get("chunks", [])]
            gt_ids = q.get("ground_truth_chunks", [])

            results.append({
                "query": q["query"],
                "retrieved_chunk_ids": retrieved_ids,
                "ndcg_at_10": compute_ndcg_at_k(retrieved_ids, gt_ids, k=10),
                "recall_at_10": compute_recall_at_k(retrieved_ids, gt_ids, k=10),
                "chunks": resp.get("chunks", []),
            })
        except Exception as exc:
            logger.error("Baseline query %d failed: %s", i + 1, exc)
            results.append({"query": q["query"], "error": str(exc)})
        finally:
            # Restore original env
            for k, v in original_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    return results


async def run_evidence_tier(
    queries: list[dict],
    config: dict,
    dimensions: dict[str, bool] | None = None,
) -> list[dict]:
    """Run retrieval with evidence-tier scoring enabled.

    dimensions: override which dimensions are enabled. If None, uses config.
    Returns a list of result dicts, one per query.
    """
    retrieval_url = config.get("retrieval_url", "http://localhost:8000")
    retry_cfg = config.get("retry", {})
    results = []

    # Enable evidence tier with specified dimensions
    dim_config = config.get("dimensions", {})
    env_overrides = {"EVIDENCE_SHADOW_MODE": "false"}

    if dimensions is not None:
        for dim_name, enabled in dimensions.items():
            dim = dim_config.get(dim_name, {})
            env_var = dim.get("env_var", f"EVIDENCE_{dim_name.upper()}_ENABLED")
            env_overrides[env_var] = str(enabled).lower()
    else:
        for dim_name, dim in dim_config.items():
            env_var = dim.get("env_var", f"EVIDENCE_{dim_name.upper()}_ENABLED")
            env_overrides[env_var] = str(dim.get("enabled", True)).lower()

    for i, q in enumerate(queries):
        logger.info("Evidence-tier query %d/%d: %s", i + 1, len(queries), q["query"][:60])
        try:
            original_env = {k: os.environ.get(k) for k in env_overrides}
            os.environ.update(env_overrides)

            resp = await retry_with_backoff(
                run_retrieve,
                q["query"],
                q["org_id"],
                retrieval_url,
                max_retries=retry_cfg.get("max_retries", 5),
                base_delay=retry_cfg.get("base_delay_seconds", 2.0),
                max_delay=retry_cfg.get("max_delay_seconds", 60.0),
            )

            retrieved_ids = [c["chunk_id"] for c in resp.get("chunks", [])]
            gt_ids = q.get("ground_truth_chunks", [])

            results.append({
                "query": q["query"],
                "retrieved_chunk_ids": retrieved_ids,
                "ndcg_at_10": compute_ndcg_at_k(retrieved_ids, gt_ids, k=10),
                "recall_at_10": compute_recall_at_k(retrieved_ids, gt_ids, k=10),
                "chunks": resp.get("chunks", []),
            })
        except Exception as exc:
            logger.error("Evidence-tier query %d failed: %s", i + 1, exc)
            results.append({"query": q["query"], "error": str(exc)})
        finally:
            for k, v in original_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    return results


# -- Statistical comparison ---------------------------------------------------


def compare_results(
    baseline: list[dict],
    treatment: list[dict],
    config: dict,
) -> dict:
    """Compare baseline vs treatment using paired Wilcoxon signed-rank test.

    Returns a comparison report dict with per-metric statistics.
    """
    from scipy.stats import wilcoxon

    sig_level = config.get("thresholds", {}).get("significance_level", 0.05)
    report: dict[str, Any] = {
        "n_queries": len(baseline),
        "significance_level": sig_level,
        "metrics": {},
    }

    for metric in ("ndcg_at_10", "recall_at_10"):
        baseline_scores = [
            r.get(metric, 0.0) for r in baseline if "error" not in r
        ]
        treatment_scores = [
            r.get(metric, 0.0) for r in treatment if "error" not in r
        ]

        if len(baseline_scores) != len(treatment_scores):
            logger.warning("Mismatched result counts for %s, skipping", metric)
            continue

        if len(baseline_scores) < 5:
            logger.warning("Too few samples (%d) for Wilcoxon test on %s", len(baseline_scores), metric)
            report["metrics"][metric] = {
                "baseline_mean": sum(baseline_scores) / max(len(baseline_scores), 1),
                "treatment_mean": sum(treatment_scores) / max(len(treatment_scores), 1),
                "note": "insufficient samples for statistical test",
            }
            continue

        baseline_mean = sum(baseline_scores) / len(baseline_scores)
        treatment_mean = sum(treatment_scores) / len(treatment_scores)
        improvement_pct = (
            ((treatment_mean - baseline_mean) / baseline_mean * 100)
            if baseline_mean > 0 else 0.0
        )

        # Wilcoxon signed-rank test (paired, non-parametric)
        try:
            stat, p_value = wilcoxon(baseline_scores, treatment_scores)
            significant = p_value < sig_level
        except ValueError:
            # All differences are zero
            stat, p_value, significant = 0.0, 1.0, False

        report["metrics"][metric] = {
            "baseline_mean": round(baseline_mean, 4),
            "treatment_mean": round(treatment_mean, 4),
            "improvement_pct": round(improvement_pct, 2),
            "wilcoxon_statistic": round(float(stat), 4),
            "p_value": round(float(p_value), 6),
            "significant": significant,
        }

    return report


# -- Main entry point ---------------------------------------------------------


async def main(
    baseline_only: bool = False,
    dimension: str | None = None,
) -> None:
    """Run the full evaluation pipeline.

    Args:
        baseline_only: If True, only run baseline measurement.
        dimension: If set, only test this dimension in isolation.
    """
    config = load_config()
    queries = load_curated_queries()

    logger.info("Loaded %d curated queries", len(queries))
    logger.info("Model: %s", config["model"])
    logger.info("Metrics: %s", config["metrics"])

    # Ensure output directory exists
    output_dir = EVAL_DIR / "results"
    output_dir.mkdir(exist_ok=True)

    # Run baseline
    logger.info("=== Running baseline (flat scoring) ===")
    t0 = time.monotonic()
    baseline = await run_baseline(queries, config)
    baseline_ms = (time.monotonic() - t0) * 1000
    logger.info("Baseline complete in %.0f ms", baseline_ms)

    baseline_path = output_dir / "baseline.json"
    baseline_path.write_text(json.dumps(baseline, indent=2))
    logger.info("Baseline results saved to %s", baseline_path)

    if baseline_only:
        logger.info("Baseline-only mode; stopping.")
        return

    if dimension:
        # Single dimension isolation
        logger.info("=== Running dimension isolation: %s ===", dimension)
        dim_config = config.get("dimensions", {})
        if dimension not in dim_config:
            logger.error("Unknown dimension: %s. Available: %s", dimension, list(dim_config.keys()))
            return
        dimensions = {d: (d == dimension) for d in dim_config}
        treatment = await run_evidence_tier(queries, config, dimensions=dimensions)
    else:
        # Full evidence-tier run
        logger.info("=== Running evidence-tier scoring ===")
        treatment = await run_evidence_tier(queries, config)

    evidence_path = output_dir / "evidence_tier.json"
    evidence_path.write_text(json.dumps(treatment, indent=2))
    logger.info("Evidence-tier results saved to %s", evidence_path)

    # Statistical comparison
    logger.info("=== Comparing results (Wilcoxon signed-rank test) ===")
    report = compare_results(baseline, treatment, config)

    report_path = output_dir / "comparison_report.json"
    report_path.write_text(json.dumps(report, indent=2))
    logger.info("Comparison report saved to %s", report_path)

    # Print summary
    logger.info("=== Summary ===")
    for metric, stats in report.get("metrics", {}).items():
        if "note" in stats:
            logger.info("  %s: %s", metric, stats["note"])
        else:
            logger.info(
                "  %s: baseline=%.4f  treatment=%.4f  improvement=%.2f%%  p=%.6f  %s",
                metric,
                stats["baseline_mean"],
                stats["treatment_mean"],
                stats["improvement_pct"],
                stats["p_value"],
                "SIGNIFICANT" if stats["significant"] else "not significant",
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RAGAS evaluation for evidence-tier scoring")
    parser.add_argument("--baseline-only", action="store_true", help="Only run baseline measurement")
    parser.add_argument(
        "--dimension",
        choices=["content_type", "temporal_decay", "assertion_mode"],
        help="Test a single dimension in isolation",
    )
    args = parser.parse_args()
    asyncio.run(main(baseline_only=args.baseline_only, dimension=args.dimension))
