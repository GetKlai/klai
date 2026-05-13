"""Ad-hoc CLI for the RAGAS evaluation harness (SPEC-RAG-EVAL-001 REQ-7).

Usage:
    python -m knowledge_ingest.eval --suite chat --variant my-experiment
    python -m knowledge_ingest.eval --suite knowledge_org

When --variant is omitted, the runner falls back to RAG_EVAL_VARIANT env var
(default 'baseline'). Synchronous wrapper around run_evaluation(); the
Procrastinate task lives in ragas_runner.register_eval_tasks().
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from knowledge_ingest.eval.ragas_runner import run_evaluation


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m knowledge_ingest.eval",
        description="Run the RAGAS evaluation harness ad-hoc against a single suite.",
    )
    parser.add_argument(
        "--suite",
        required=True,
        help="Suite name (without .yaml extension). e.g. 'chat' or 'knowledge_org'.",
    )
    parser.add_argument(
        "--variant",
        default=None,
        help=(
            "Variant tag written to every row. Defaults to RAG_EVAL_VARIANT env var "
            "or 'baseline' when both are unset."
        ),
    )
    return parser


async def _amain(suite: str, variant: str | None) -> dict:
    return await run_evaluation(suite=suite, variant=variant)


def main(argv: list[str] | None = None) -> int:
    """Synchronous CLI entrypoint. Returns process exit code."""
    args = _build_parser().parse_args(argv)
    result = asyncio.run(_amain(suite=args.suite, variant=args.variant))
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
