"""What the help articles did not support, per day, from real widget answers.

SPEC-RAG-ANSWER-JUDGES-001. Every support-mode answer stores what the
statement-level check found in ``widget_messages.answer_signals``: how many
statements the articles do not support, whether the answer was repaired, and
what the visitor ended up seeing. This prints that per day so the effect on real
traffic can be read without rebuilding the query each time.

Run on the server:

    docker exec -w /repo/klai-portal/backend klai-core-portal-api-1 \
        python scripts/grounding_report.py [days] [org_id]

Reads only, across tenants, and excludes preview and test conversations.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    # Running "python scripts/grounding_report.py" puts scripts/ on the path,
    # not the backend root, so ``app`` would not import. Same bootstrap as
    # create_product_update.py.
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import text  # noqa: E402

from app.core.database import cross_org_session  # noqa: E402

_QUERY = text(
    """
    SELECT date_trunc('day', a.created_at)::date AS day,
           count(*) AS answers,
           count(*) FILTER (WHERE (a.answer_signals->>'unsupported')::int > 0) AS with_unsupported,
           count(*) FILTER (WHERE (a.answer_signals->>'repaired')::bool) AS repaired,
           count(*) FILTER (WHERE a.answer_signals->>'decision' = 'refusal') AS refused,
           count(*) FILTER (WHERE a.answer_signals ? 'judge_failed') AS check_failed,
           round(avg((a.answer_signals->>'unsupported')::int)::numeric, 2) AS avg_unsupported
    FROM widget_messages a
    JOIN widget_conversations c ON c.id = a.conversation_id
    WHERE a.role = 'assistant'
      AND NOT c.is_preview
      AND NOT c.is_test
      AND a.created_at > now() - make_interval(days => :days)
      AND a.answer_signals ? 'unsupported'
      AND (:org_id = 0 OR c.org_id = :org_id)
    GROUP BY 1
    ORDER BY 1
    """
)


async def main(days: int, org_id: int) -> None:
    async with cross_org_session() as session:
        rows = (await session.execute(_QUERY, {"days": days, "org_id": org_id})).mappings().all()
    if not rows:
        print("Geen gemeten antwoorden in deze periode.")
        return
    print(
        f"{'dag':<12}{'antwoorden':>11}{'met onbewezen':>15}{'gerepareerd':>13}{'geweigerd':>11}{'check faalde':>14}{'gem.':>7}"
    )
    for row in rows:
        print(
            f"{row['day']!s:<12}{row['answers']:>11}{row['with_unsupported']:>15}"
            f"{row['repaired']:>13}{row['refused']:>11}{row['check_failed']:>14}{row['avg_unsupported']!s:>7}"
        )


if __name__ == "__main__":
    asyncio.run(
        main(
            int(sys.argv[1]) if len(sys.argv) > 1 else 7,
            int(sys.argv[2]) if len(sys.argv) > 2 else 0,
        )
    )
