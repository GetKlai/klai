import argparse
import asyncio
import json
import sys
from pathlib import Path

from app.services.ingest_gap_evaluation import evaluate_ingest_snapshot, report_exit_code

evaluate = evaluate_ingest_snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--limit", type=int, choices=range(1, 101), default=10)
    args = parser.parse_args()
    try:
        snapshot = json.loads(args.input.read_text())
        report = asyncio.run(evaluate(snapshot, limit=args.limit))
    except Exception as exc:
        print(json.dumps({"error": type(exc).__name__}), file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return report_exit_code(report)


if __name__ == "__main__":
    raise SystemExit(main())
