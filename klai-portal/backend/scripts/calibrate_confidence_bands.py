"""Calibrate retrieval-api's confidence-band thresholds against real production behavior.

SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001. ``confidence_band_high_threshold`` (0.60) and
``confidence_band_low_threshold`` (0.30) in klai-retrieval-api/retrieval_api/config.py
were hand-picked from two data points (a 2026-05-07 incident: 0.18 = hallucinated,
0.96 = correct). They gate the litellm-hook low-confidence injection/clarify decision,
the Strict deterministic refusal, ``_kb_risk_upgrade`` routing, and the widget path —
and they drive the Grafana alert ``spec-rag-001-low-confidence-rate``. This script
measures whether 0.60/0.30 are still the right cut points, with real outcomes instead
of two anecdotes.

Existing production logs cannot answer this (checked 2026-09-24): grounding verdicts
in VictoriaLogs exist almost only for band=high (the low-confidence guard fires before
the judge runs at band=low, on the internal-chat path); ``retrieval_score`` in
``kb_citations_rendered_structured`` is a post-selection per-source score, not the
reranker top-1 that sets the band; and of 417 citation rows since 2026-09-17, 340 came
from one organisation repeating identical queries.

Method: each question goes through a real preview-session widget turn (real retrieval,
real composer, real answer model, real grounding judge) against
`/partner/v1/chat/completions` — preview conversations are marked ``is_preview`` and
every report/dashboard already excludes them, see ``grounding_report.py``. The turn's
own ``answer_signals.band`` (copied verbatim from retrieval-api's actual decision for
that turn) is the only band this script trusts. The grounding judge runs on the DRAFT,
before ``decide_answer`` turns it into a refusal/clarify/answer (see
``_judge_composed_answer`` in app/services/partner_chat.py) — so a turn's
``unsupported`` count is only meaningful when ``decision`` is ``answer`` or
``partial_answer``; refusal/off_topic/clarifying_question turns are reported as their
own share per band, never pooled into the grounded rate.

No score-based analysis: an earlier version of this script called retrieval-api's
``/retrieve`` directly for a comparable score, but that call could not reproduce the
widget turn's own request (missing ``conversation_history`` and the first-question
paraphrases ``retrieve_context`` sends — see app/services/partner_chat.py around
``first_question_variants``), so every score it produced measured a different
retrieval than the one that actually served the turn. Upgrade path: a real score-cut
scan needs the widget turn itself to record the post-boost score the band was decided
on (today's ``answer_signals.top_score`` is the pre-boost reranker score) — a separate
change to partner_chat.py, not this script.

Question set (~250, one tenant with both a real static KB and live widget traffic):
the hand-curated, difficulty-stratified queries in
klai-knowledge-ingest/knowledge_ingest/eval/suites/{chat,knowledge_org}.yaml, up to
100 real first-turn visitor questions from the last 90 days, and 120 fixed,
hand-written off-topic questions (no Klai tenant KB covers any of them) as the
"predominantly unsupported" anchor, because the curated suites and real traffic
together fall well short of 250 on their own.

Known ceiling: single tenant. Upgrade path: rerun with more orgs once a second tenant
has comparable volume.

Rate limits: the answer model is klai-primary, shared with live chat (~45 RPM
combined); the grounding judge runs on klai-medium. Paced at one request per
``KLAI_CALIBRATION_PAUSE`` seconds (default 3.0s = ~20 RPM), matching the pacing
rationale in simulate_conversations.py (an unpaced run caused a 502 incident on
2026-09-18).

Two modes:

  1. Send + analyze (default) — sends the question set, then analyzes the widget's
     preview turns in the run's wall-clock window. That window also catches any
     preview an admin runs on the same widget meanwhile, so run it when nobody else
     is testing that widget and check the turn count printed against the questions
     sent.
  2. Analyze-only, read-only, sends nothing — set ``KLAI_CALIBRATION_SINCE``
     (ISO timestamp, e.g. ``2026-09-24T09:30:00Z``) and optionally
     ``KLAI_CALIBRATION_UNTIL`` (defaults to now) to re-analyze an existing run's
     preview turns, e.g. after an interrupted run or to recheck a prior result.

Run on the server (writes nothing a visitor sees, nothing any report counts).
klai-core-portal-api-1's image does not carry klai-knowledge-ingest, so copy the two
curated suite files in first:

    docker exec klai-core-portal-api-1 mkdir -p /tmp/calibration_suites
    docker cp klai-knowledge-ingest/knowledge_ingest/eval/suites/chat.yaml \\
        klai-core-portal-api-1:/tmp/calibration_suites/chat.yaml
    docker cp klai-knowledge-ingest/knowledge_ingest/eval/suites/knowledge_org.yaml \\
        klai-core-portal-api-1:/tmp/calibration_suites/knowledge_org.yaml
    docker exec -w /repo/klai-portal/backend klai-core-portal-api-1 \\
        python scripts/calibrate_confidence_bands.py <widget_uuid> [max_questions]

    # Analyze-only (no sends):
    docker exec -e KLAI_CALIBRATION_SINCE=2026-09-24T09:30:00Z \\
        -w /repo/klai-portal/backend klai-core-portal-api-1 \\
        python scripts/calibrate_confidence_bands.py <widget_uuid>

Output: decision mix per band, and the grounded rate (Wilson 95% CI) among answered
drafts per band, printed to stdout — aggregates only, no question or answer text,
because the questions include real visitor traffic and the repo is public. Per-turn
detail (band/decision/unsupported, no text) goes to ``KLAI_CALIBRATION_OUT`` (default
/tmp/calibration_<run>.json inside the container).

When to repeat: after a reranker, embedding, or answer-model change, and otherwise
quarterly as a standing operator check.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from math import sqrt
from pathlib import Path
from uuid import uuid4

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import httpx  # noqa: E402
import yaml  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.database import cross_org_session  # noqa: E402
from app.core.provisioning_names import provisioning_names_for_slug  # noqa: E402
from app.models.portal import PortalOrg  # noqa: E402
from app.models.widgets import Widget, WidgetKbAccess  # noqa: E402
from app.services.librechat_quality_judge import _message_text, _mongo_client  # noqa: E402
from app.services.widget_auth import generate_session_token  # noqa: E402

# The curated suites this script reuses rather than re-generating; see the module
# docstring. klai-core-portal-api-1's image only contains klai-portal + klai-libs
# (checked 2026-09-24: no klai-knowledge-ingest checkout inside the container), so
# these two files must be `docker cp`'d alongside this script before running it —
# see the run line above. KLAI_CALIBRATION_SUITE_DIR overrides the drop location;
# a missing file is not fatal (see _load_suite_questions), it just shrinks the
# curated slice of the question set to the organic + off-topic questions.
_SUITE_DIR = Path(os.getenv("KLAI_CALIBRATION_SUITE_DIR") or (tempfile.gettempdir() + "/calibration_suites"))
_SUITE_PATHS = [
    _SUITE_DIR / "chat.yaml",
    _SUITE_DIR / "knowledge_org.yaml",
]

# Fixed, hand-written negatives: no Klai tenant sells any of this, so every one of
# these is a true "the KB cannot answer this" anchor, with zero risk of accidentally
# reusing tenant content. Deliberately varied in topic, length and language (Dutch +
# English, matching real visitor mix) so the low end of the curve is not one query
# repeated — that repetition is exactly what made the historical citation data
# useless (see module docstring).
_OFF_TOPIC_QUESTIONS = [
    "Wat is het weer morgen in Rotterdam?",
    "What's the best way to bake sourdough bread at home?",
    "Wie won de Champions League in 2019?",
    "Can you recommend a good sci-fi book from the 1960s?",
    "Hoe kook ik een perfect gepocheerd ei?",
    "What's the capital of Mongolia?",
    "Leg me uit hoe fotosynthese werkt.",
    "How do I train for a marathon in twelve weeks?",
    "Wat is de beste manier om een kamerplant water te geven?",
    "Explain the plot of Hamlet in two sentences.",
    "Hoeveel calorieën zitten er in een banaan?",
    "What's a good gift for a five-year-old's birthday?",
    "Kun je me een recept voor lasagne geven?",
    "How does a nuclear reactor work?",
    "Wat is de hoofdstad van Kazachstan?",
    "Can you help me plan a weekend trip to Lisbon?",
    "Hoe verwissel ik een lekke band op mijn fiets?",
    "What's the difference between a crocodile and an alligator?",
    "Wat is het verschil tussen een aandeel en een obligatie?",
    "How long does it take to learn to play the guitar?",
    "Wat kost gemiddeld een verhuizing binnen Nederland?",
    "Can you explain how compound interest works?",
    "Hoe train ik mijn hond om te zitten?",
    "What's the tallest mountain in Europe?",
    "Wat is een goede workout voor beginners?",
    "How do I remove a red wine stain from a carpet?",
    "Wat betekent de term 'inflatie' precies?",
    "Can you suggest a Netflix series similar to Stranger Things?",
    "Hoeveel uur slaap heeft een volwassene gemiddeld nodig?",
    "What's the process for getting a passport renewed in the US?",
    "Wat is het verschil tussen weersverwachting en klimaat?",
    "How do solar panels convert sunlight into electricity?",
    "Wat is de beste tijd van het jaar om naar Japan te reizen?",
    "Can you explain what a black hole is?",
    "Hoe maak ik zelf yoghurt?",
    "What are the rules of cricket?",
    "Wat is het verschil tussen mayonaise en aioli?",
    "How do I set up a compost bin in my garden?",
    "Wat is een gezonde hartslag tijdens het hardlopen?",
    "Can you recommend exercises for lower back pain?",
    "Hoeveel is 15% fooi op een rekening van 84 euro?",
    "What's the history of the Eiffel Tower?",
    "Wat is de beste manier om Spaans te leren als volwassene?",
    "How do vaccines train the immune system?",
    "Wat kost een gemiddelde bruiloft in Nederland?",
    "Can you explain the offside rule in football?",
    "Hoe lang moet je een biefstuk bakken voor medium-rare?",
    "What's a good beginner telescope for stargazing?",
    "Wat is het verschil tussen een democratie en een republiek?",
    "How do I descale a coffee machine?",
    "Wat is de oorsprong van het woord 'robot'?",
    "Can you recommend a podcast about history?",
    "Hoeveel water moet je per dag drinken?",
    "What's the difference between a virus and a bacterium?",
    "Wat is de snelste trein van Nederland naar Parijs?",
    "How do I fix a squeaky door hinge?",
    "Wat betekent 'carbon footprint' precies?",
    "Can you explain how a rainbow forms?",
    "Hoe plant ik tomaten in mijn moestuin?",
    "What's the best way to whiten teeth naturally?",
    "Wat is het verschil tussen UTC en GMT?",
    "How do noise-cancelling headphones work?",
    "Wat is een goed dieet voor iemand met een glutenallergie?",
    # Extended 2026-09-24: the curated suites and real traffic fall short of the
    # ~250 target on their own; off-topic negatives close the gap without
    # inflating the curated/organic slices past what real data supports.
    "Hoeveel eiwit heeft een volwassene per dag nodig?",
    "What causes a hangover and how do you cure one?",
    "Wat is het verschil tussen een komeet en een asteroïde?",
    "How do I negotiate a lower rent with my landlord?",
    "Wat is de beste volgorde om een huis te schilderen?",
    "Can you explain how GPS satellites determine location?",
    "Hoeveel belasting betaal je over een tweede huis in Nederland?",
    "What's a good way to start meditating as a beginner?",
    "Wat is de levensverwachting van een goudvis?",
    "How does a heat pump work?",
    "Wat is het verschil tussen een notaris en een advocaat?",
    "Can you suggest board games for a family game night?",
    "Hoe herken ik een giftige paddenstoel?",
    "What's the difference between a hurricane and a typhoon?",
    "Wat kost het gemiddeld om een keuken te laten plaatsen?",
    "How do I get red wine out of a white shirt?",
    "Wat is de oorsprong van Sinterklaas?",
    "Can you explain the water cycle simply?",
    "Hoeveel stappen per dag zijn gezond?",
    "What's the best way to store fresh basil?",
    "Wat is het verschil tussen sparen en beleggen?",
    "How do bees make honey?",
    "Wat is een gemiddeld energieverbruik voor een gezin van vier?",
    "Can you recommend exercises to improve posture?",
    "Hoe werkt een warmtepomp precies?",
    "What's the etiquette for tipping in Japan?",
    "Wat is het verschil tussen een testament en een codicil?",
    "How long does it take to compost kitchen scraps?",
    "Wat is de beste manier om te ontstressen na werk?",
    "Can you explain how vaccination herd immunity works?",
    "Hoeveel kost een gemiddelde scheiding in Nederland?",
    "What's a good beginner recipe for kombucha?",
    "Wat is het verschil tussen een hypotheek met NHG en zonder?",
    "How do I know if my houseplant needs more light?",
    "Wat is de geschiedenis van het Rijksmuseum?",
    "Can you explain how currency exchange rates are set?",
    "Hoe lang kun je gekookte rijst bewaren in de koelkast?",
    "What's the difference between UX and UI design?",
    "Wat is het verschil tussen een postcode en een adres?",
    "How do noise complaints work with Dutch neighbors legally?",
    "Wat is een goede manier om te onthouden waar je auto staat?",
    "Can you explain how a rainbow trout differs from a salmon?",
    "Hoeveel euro is honderd dollar op dit moment?",
    "What's the best way to learn touch typing?",
    "Wat is het verschil tussen AOW en pensioen?",
    "How do I remove limescale from a shower head?",
    "Wat is een gezonde bloeddruk voor een volwassene?",
    "Can you explain what a leap year is and why we have them?",
    "Hoeveel weken zwangerschapsverlof heb je in Nederland?",
    "What's a good way to childproof kitchen cabinets?",
    "Wat is het verschil tussen bio en scharrel bij eieren?",
    "How does jet lag affect the body?",
    "Wat is de snelste manier om Frans te leren voor een vakantie?",
    "Can you recommend a simple beginner sourdough starter schedule?",
    "Hoeveel is de eigen bijdrage bij de tandarts gemiddeld?",
    "What's the difference between a will and a living trust?",
    "Wat is het verschil tussen een zzp'er en een eenmanszaak?",
]

_PAUSE_BETWEEN_REQUESTS = float(os.getenv("KLAI_CALIBRATION_PAUSE") or "3.0")
_BACKOFF_SECONDS = (5.0, 15.0, 45.0)
# The four values retrieval-api's _compute_confidence_band can return; anything
# else is a schema drift and gets dropped rather than silently miscounted.
_VALID_BANDS = ("high", "medium", "low", "unknown")
# The grounding judge runs on the draft BEFORE decide_answer turns it into a
# refusal/clarify/answer (see _judge_composed_answer in partner_chat.py), so
# "unsupported" is only a claim about content the visitor actually saw when the
# turn ended up as one of these two decisions. Pooling refusal/off_topic/
# clarifying_question in with them was confirmed defect #2 in the 2026-09-24
# review of this script's first version.
_ANSWERED_DECISIONS = frozenset({"answer", "partial_answer"})


def _widget_api_base() -> str:
    return os.getenv("KLAI_WIDGET_API_BASE") or f"https://api.{settings.domain}"


async def _with_backoff(what: str, call):
    for wait in (*_BACKOFF_SECONDS, None):
        try:
            return await call()
        except httpx.HTTPStatusError as exc:
            throttled = exc.response is not None and exc.response.status_code in (429, 502, 503)
            if not throttled or wait is None:
                raise
            print(f"    {what} got {exc.response.status_code}, waiting {wait:.0f}s", flush=True)
            await asyncio.sleep(wait)
    raise RuntimeError("unreachable")


def _load_suite_questions() -> list[str]:
    """Question texts from the curated Voys eval suites."""
    out: list[str] = []
    for path in _SUITE_PATHS:
        if not path.exists():
            print(f"  suite not found, skipping: {path}", flush=True)
            continue
        data = yaml.safe_load(path.read_text())
        for q in data.get("queries", []):
            query = q.get("query")
            if query:
                out.append(query)
    return out


_ORGANIC_FIRST_QUESTIONS = text(
    """
    SELECT DISTINCT ON (m.conversation_id) m.content
    FROM widget_messages m
    JOIN widget_conversations c ON c.id = m.conversation_id
    WHERE c.widget_id = :widget_id
      AND NOT c.is_preview AND NOT c.is_test
      AND m.role = 'user'
      AND m.created_at > now() - interval '90 days'
    ORDER BY m.conversation_id, m.sequence
    LIMIT :limit
    """
)


async def _load_organic_questions(widget_id: str, limit: int) -> list[str]:
    async with cross_org_session() as session:
        rows = (await session.execute(_ORGANIC_FIRST_QUESTIONS, {"widget_id": widget_id, "limit": limit})).all()
    seen: set[str] = set()
    out: list[str] = []
    for (content,) in rows:
        key = content.strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(content)
    return out


# The widget's own tenant only: the database name comes from the widget's org
# slug, so this can never read another tenant's LibreChat. Widget history is
# short (the Voys widget went live mid-September 2026) and its internal
# LibreChat holds roughly ten times as many real questions about the same
# knowledge, asked by staff rather than visitors, so they widen the set. First
# questions only, like the organic slice: a follow-up needs its conversation.
_LIBRECHAT_DAYS = 90
_LIBRECHAT_MAX_CHARS = 800


def _load_librechat_questions(tenant_slug: str, limit: int) -> list[str]:
    """Each conversation's own opening question, when it opened inside the window.

    The opening is taken per conversation before any date or length filter: a
    first pass that filtered first picked a later follow-up as the "first
    question" of a conversation that opened before the window or whose opening
    was over-long, and sent it to the widget without its context (review of
    2026-09-24). Known ceiling: the grouping reads every user message of the
    tenant; fine for an operator run, and a date-bounded match can come first
    once a tenant's history makes that slow.
    """
    database = provisioning_names_for_slug(tenant_slug, domain=settings.domain).mongodb_database
    since = datetime.now(UTC) - timedelta(days=_LIBRECHAT_DAYS)
    pipeline = [
        {"$match": {"isCreatedByUser": True}},
        {"$sort": {"createdAt": 1}},
        {
            "$group": {
                "_id": "$conversationId",
                "text": {"$first": "$text"},
                "content": {"$first": "$content"},
                "createdAt": {"$first": "$createdAt"},
            }
        },
    ]
    with _mongo_client() as client:
        openings = list(client[database].messages.aggregate(pipeline))
    seen: set[str] = set()
    out: list[str] = []
    for doc in sorted(openings, key=lambda d: d["createdAt"]):
        created = doc["createdAt"] if doc["createdAt"].tzinfo else doc["createdAt"].replace(tzinfo=UTC)
        question = _message_text(doc).strip()
        if created < since or not 0 < len(question) <= _LIBRECHAT_MAX_CHARS or question.lower() in seen:
            continue
        seen.add(question.lower())
        out.append(question)
    return out[:limit]


@dataclass
class _WidgetContext:
    wgt_id: str
    org_id: int
    tenant_slug: str
    kb_ids: list[int]
    welcome: str


async def _load_widget_context(widget_id: str) -> _WidgetContext:
    """Fetch once and reuse for every question — none of this varies per turn."""
    from sqlalchemy import select

    async with cross_org_session() as session:
        widget = (await session.execute(select(Widget).where(Widget.id == widget_id))).scalar_one()
        org = (await session.execute(select(PortalOrg).where(PortalOrg.id == widget.org_id))).scalar_one()
        kb_ids = [
            row.kb_id
            for row in (await session.execute(select(WidgetKbAccess).where(WidgetKbAccess.widget_id == widget.id)))
            .scalars()
            .all()
        ]
    return _WidgetContext(
        wgt_id=widget.widget_id,
        org_id=widget.org_id,
        tenant_slug=org.slug,
        kb_ids=kb_ids,
        welcome=widget.widget_config.get("welcome_message", ""),
    )


def _mint_token(ctx: _WidgetContext) -> str:
    """Fresh preview session token: one isolated single-turn conversation per question."""
    return generate_session_token(
        wgt_id=ctx.wgt_id,
        org_id=ctx.org_id,
        kb_ids=ctx.kb_ids,
        secret=settings.widget_jwt_secret,
        tenant_slug=ctx.tenant_slug,
        is_preview=True,
        session_id=str(uuid4()),
    )


async def _ask(client: httpx.AsyncClient, token: str, welcome: str, question: str) -> None:
    async def _call():
        return await client.post(
            f"{_widget_api_base()}/partner/v1/chat/completions",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={
                "model": "klai-primary",
                "stream": False,
                "messages": [
                    {"role": "assistant", "content": welcome},
                    {"role": "user", "content": question},
                ],
            },
        )

    response = await _with_backoff("the widget", lambda: _call())
    response.raise_for_status()


_WINDOW_QUERY = text(
    """
    SELECT m.answer_signals
    FROM widget_conversations c
    JOIN widget_messages m ON m.conversation_id = c.id
    WHERE c.widget_id = :widget_id
      AND c.is_preview
      AND m.role = 'assistant'
      AND m.created_at >= :since
      AND m.created_at < :until
    """
)


async def _fetch_window(widget_id: str, since: datetime, until: datetime) -> list[dict]:
    """Preview assistant turns for this widget in [since, until), with a valid band.

    Time-windowed rather than keyed by the session tokens this run minted: a prior
    version tracked session keys instead and, across two interrupted re-runs of the
    full question set, ended up reading the same turns back multiple times (751
    rows reported against 259 actually in the DB for that day — defect #1 in the
    2026-09-24 review). A window has no such failure mode and doubles as the
    analyze-only mode's only input.
    """
    async with cross_org_session() as session:
        rows = (await session.execute(_WINDOW_QUERY, {"widget_id": widget_id, "since": since, "until": until})).all()
    out: list[dict] = []
    for (signals,) in rows:
        signals = signals or {}
        band = signals.get("band")
        if band not in _VALID_BANDS:
            continue
        out.append(
            {
                "band": band,
                "decision": signals.get("decision"),
                "unsupported": signals.get("unsupported"),
                "top_score": signals.get("top_score"),
                "verdict": signals.get("verdict"),
            }
        )
    return out


def _wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a binomial proportion.

    Chosen over a normal-approximation interval because several bands here have
    n < 30 judged turns, where the normal approximation can produce an interval
    outside [0, 1]; Wilson stays in range and is the standard fix (stdlib math
    only, no new dependency).
    """
    if n == 0:
        return (0.0, 0.0)
    p = successes / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = (z * sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def _print_analysis(rows: list[dict]) -> None:
    if not rows:
        print("No preview turns with a band in this window.")
        return

    by_band: dict[str, list[dict]] = {b: [] for b in _VALID_BANDS}
    for r in rows:
        by_band[r["band"]].append(r)

    out_path = Path(
        os.getenv("KLAI_CALIBRATION_OUT") or (tempfile.gettempdir() + f"/calibration_{int(time.time())}.json")
    )
    out_path.write_text(json.dumps(rows))
    print(f"\n{len(rows)} preview turns with a band. Per-turn detail (band/decision/unsupported): {out_path}")

    print("\n=== DECISION MIX BY BAND ===")
    for band in _VALID_BANDS:
        items = by_band[band]
        if not items:
            continue
        # (n, judged, zero-unsupported) per decision value.
        per_decision: dict[str, list[int]] = {}
        for r in items:
            d = r["decision"] or "none"
            judged = isinstance(r["unsupported"], int)
            n, j, z = per_decision.get(d, [0, 0, 0])
            per_decision[d] = [n + 1, j + judged, z + (judged and r["unsupported"] == 0)]
        parts = ", ".join(
            f"{d}={n} (judged={j}, zero={z})" for d, (n, j, z) in sorted(per_decision.items(), key=lambda kv: -kv[1][0])
        )
        print(f"{band:<10} n={len(items):<5} {parts}")

    print("\n=== GROUNDED RATE AMONG ANSWERED DRAFTS (decision in answer/partial_answer, judged) ===")
    print(
        f"{'band':<10}{'n_answered':>12}{'judged':>8}{'grounded':>10}{'rate':>8}{'wilson 95% CI':>18}{'non-answer %':>14}"
    )
    for band in _VALID_BANDS:
        items = by_band[band]
        if not items:
            continue
        answered = [r for r in items if r["decision"] in _ANSWERED_DECISIONS]
        judged = [r for r in answered if isinstance(r["unsupported"], int)]
        grounded = sum(1 for r in judged if r["unsupported"] == 0)
        rate = grounded / len(judged) if judged else float("nan")
        lo, hi = _wilson_interval(grounded, len(judged))
        non_answer_pct = 100 * sum(1 for r in items if r["decision"] not in _ANSWERED_DECISIONS) / len(items)
        print(
            f"{band:<10}{len(answered):>12}{len(judged):>8}{grounded:>10}{rate:>8.2f}"
            f"{f'[{lo:.2f}, {hi:.2f}]':>18}{non_answer_pct:>13.0f}%"
        )

    print(
        "\nCurrent thresholds: low=0.30 high=0.60. No score-based scan here (see module "
        "docstring) — read this table against 'predominantly grounded above high, "
        "predominantly unsupported/refused below low'."
    )
    _print_score_strips(rows)


# Where to put the widget's weak-sources bar (clarify_decision.WEAK_SOURCES_ADDENDUM
# fires on classify_gap "soft", every reranker score under 0.4). Grounded is
# not relevant: an answer can be fully carried by an article about a
# neighbouring subject. So per strip of the turn's own top_score (the
# pre-boost reranker score, see module docstring) this adds the answer judge's
# verdict, already recorded on every turn: does the reply answer the question.
_SCORE_STRIPS = (0.0, 0.3, 0.4, 0.5, 0.6, 1.01)


def _print_score_strips(rows: list[dict]) -> None:
    scored = [r for r in rows if isinstance(r.get("top_score"), int | float)]
    print(f"\n=== BY TOP SCORE STRIP ({len(scored)} turns with a score) ===")
    print("Verdict and grounding are the judges' reading of the draft, before any repair.")
    print(f"{'strip':<12}{'n':>5}{'answered':>10}{'grounded':>10}{'answers q':>12}{'partial':>10}{'not answered':>14}")
    for lo, hi in pairwise(_SCORE_STRIPS):
        items = [r for r in scored if lo <= r["top_score"] < hi]
        if not items:
            continue
        answered = [r for r in items if r["decision"] in _ANSWERED_DECISIONS]
        judged = [r for r in answered if isinstance(r["unsupported"], int)]
        grounded = sum(1 for r in judged if r["unsupported"] == 0)
        verdicts = [r["verdict"] for r in answered if r.get("verdict")]
        count = {v: sum(1 for x in verdicts if x == v) for v in ("answered", "partial", "not_answered")}
        n_v = len(verdicts)
        print(
            f"{f'{lo:.1f}-{min(hi, 1.0):.1f}':<12}{len(items):>5}{len(answered):>10}{f'{grounded}/{len(judged)}':>10}"
            f"{f'{count["answered"]}/{n_v}':>12}{f'{count["partial"]}/{n_v}':>10}{f'{count["not_answered"]}/{n_v}':>14}"
        )


async def main(widget_id: str, max_questions: int) -> None:
    since_env = os.getenv("KLAI_CALIBRATION_SINCE")
    if since_env:
        until_env = os.getenv("KLAI_CALIBRATION_UNTIL")
        since = datetime.fromisoformat(since_env)
        until = datetime.fromisoformat(until_env) if until_env else datetime.now(UTC)
        print(f"Analyze-only for widget {widget_id}: [{since}, {until}) — nothing sent.", flush=True)
        rows = await _fetch_window(widget_id, since, until)
        _print_analysis(rows)
        return

    print(f"Loading question set for widget {widget_id} ...", flush=True)
    questions = _load_suite_questions()
    print(f"  curated suites: {len(questions)}", flush=True)
    organic = await _load_organic_questions(widget_id, limit=100)
    print(f"  organic (deduplicated): {len(organic)}", flush=True)
    questions += organic
    ctx = await _load_widget_context(widget_id)
    librechat = _load_librechat_questions(
        ctx.tenant_slug, limit=int(os.getenv("KLAI_CALIBRATION_LIBRECHAT_MAX") or "300")
    )
    print(f"  librechat first questions, same tenant (deduplicated): {len(librechat)}", flush=True)
    questions += librechat
    questions += _OFF_TOPIC_QUESTIONS
    print(f"  off-topic negatives: {len(_OFF_TOPIC_QUESTIONS)}", flush=True)
    if len(questions) > max_questions:
        random.Random(42).shuffle(questions)  # noqa: S311 -- sample-picking, not security-sensitive
        questions = questions[:max_questions]
    print(f"  total: {len(questions)}", flush=True)

    since = datetime.now(UTC)
    async with httpx.AsyncClient(timeout=60.0) as client:
        for i, question in enumerate(questions, 1):
            token = _mint_token(ctx)
            try:
                await _ask(client, token, ctx.welcome, question)
            except Exception as exc:  # one bad question must not abort the run
                print(f"  {i}/{len(questions)} failed: {exc!r}", flush=True)
            if i % 20 == 0:
                print(f"  {i}/{len(questions)} sent", flush=True)
            await asyncio.sleep(_PAUSE_BETWEEN_REQUESTS)
    until = datetime.now(UTC)

    print("Waiting for writes to land, then reading back answer_signals ...", flush=True)
    await asyncio.sleep(5.0)
    rows = await _fetch_window(widget_id, since, until)
    _print_analysis(rows)


if __name__ == "__main__":
    asyncio.run(
        main(
            sys.argv[1],
            int(sys.argv[2]) if len(sys.argv) > 2 else 300,
        )
    )
