"""What the answer chain measures, why, and whether anyone reads it.

A signal that only exists when someone knows to query for it is, in practice,
not used: on 2026-09-19 eight measurement signals from this chain were emitted
in production and not one of them was read by a dashboard or an alert. This
list is the documentation that makes them findable. It is shown to platform
admins under Status, and it describes THAT something is recorded and WHY —
never what the records contain.

``read_automatically`` is the column that matters. It is ``False`` for every
signal nothing consumes on its own, which is most of them, and that is meant
to be uncomfortable to look at.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

SignalKind = Literal["log", "record", "report", "instrument", "guard"]


class ObservabilitySignal(BaseModel):
    name: str
    kind: SignalKind
    service: str
    # Both languages here rather than in Paraglide: the list is documentation, and
    # splitting each entry across three files would turn one register into three
    # that drift apart.
    purpose: dict[str, str]
    how_to_read: str
    read_automatically: bool
    source: str


SIGNALS: list[ObservabilitySignal] = [
    ObservabilitySignal(
        name="partner_chat_answer_judge",
        kind="log",
        service="portal-api (widget)",
        purpose={
            "nl": (
                "Per widgetantwoord het oordeel van beide controles achteraf: of het de vraag "
                "beantwoordt, en of de artikelen de uitspraken dragen. Laat zien waarom een bezoeker "
                "een antwoord, een weigering of een afspraakknop kreeg."
            ),
            "en": "Per widget answer, the verdict of both checks after writing: whether it answers the question, and whether the articles carry its statements. Shows why a visitor got an answer, a refusal or an appointment button.",
        },
        how_to_read='VictoriaLogs: service:portal-api AND "partner_chat_answer_judge"',
        read_automatically=False,
        source="SPEC-RAG-ANSWER-JUDGES-001, REQ-2, REQ-3",
    ),
    ObservabilitySignal(
        name="partner_chat_answer_repair",
        kind="log",
        service="portal-api (widget)",
        purpose={
            "nl": (
                "Per reparatie of die gelukt is, niets overliet, of mislukte. Zonder dit is niet te zien "
                "hoe vaak een antwoord is ingekort omdat de artikelen een uitspraak niet droegen."
            ),
            "en": "Per repair, whether it succeeded, left nothing, or failed. Without it there is no way to see how often an answer was cut back because the articles did not carry a statement.",
        },
        how_to_read='VictoriaLogs: service:portal-api AND "partner_chat_answer_repair"',
        read_automatically=False,
        source="evolutie.md 2.8, 2.9",
    ),
    ObservabilitySignal(
        name="partner_chat_turn_timing",
        kind="log",
        service="portal-api (widget)",
        purpose={
            "nl": (
                "Per beurt waar de wachttijd heen ging: zoeken, controle vooraf, schrijven en de "
                "controles achteraf. Nodig om te zien of een nieuwe controle het gesprek trager maakt."
            ),
            "en": "Per turn, where the waiting time went: retrieval, the check before, writing, and the checks after. Needed to see whether a new check makes the conversation slower.",
        },
        how_to_read='VictoriaLogs: service:portal-api AND "partner_chat_turn_timing"',
        read_automatically=False,
        source="SPEC-RAG-ANSWER-JUDGES-001, REQ-4",
    ),
    ObservabilitySignal(
        name="answer_grounding_late",
        kind="log",
        service="portal-api (widget)",
        purpose={
            "nl": (
                "Het oordeel van een controle die langer duurde dan haar budget van 4 seconden. De "
                "bezoeker wacht er niet op; het antwoord blijft ongecontroleerd. Dit legt vast wat die "
                "controle gevonden zou hebben, zodat te beslissen is of het budget omhoog moet."
            ),
            "en": "The verdict of a check that ran past its 4 second budget. The visitor does not wait for it and the answer stays unchecked. This records what that check would have found, so it can be decided whether the budget should go up.",
        },
        how_to_read='VictoriaLogs: service:portal-api AND "answer_grounding_late"',
        read_automatically=False,
        source="evolutie.md 2.37",
    ),
    ObservabilitySignal(
        name="widget_messages.answer_signals",
        kind="record",
        service="portal-api (database)",
        purpose={
            "nl": (
                "Per opgeslagen widgetantwoord hoeveel uitspraken de artikelen niet droegen, of het "
                "gerepareerd is, welke beslissing viel, en of een controle mislukte. De bron voor het "
                "dagrapport."
            ),
            "en": "Per stored widget answer, how many statements the articles did not carry, whether it was repaired, which decision was taken, and whether a check failed. The source for the daily report.",
        },
        how_to_read="Postgres: widget_messages.answer_signals (JSONB)",
        read_automatically=False,
        source="SPEC-RAG-ANSWER-JUDGES-001",
    ),
    ObservabilitySignal(
        name="kb_answer_grounding",
        kind="log",
        service="litellm (internal chat)",
        purpose={
            "nl": (
                "Per strikt kennisbankantwoord in de interne chat hoeveel uitspraken er zijn, hoeveel de "
                "artikelen niet dragen en hoeveel ze tegenspreken. Dezelfde controle als op de widget, "
                "zodat beide paden te vergelijken zijn."
            ),
            "en": "Per strict knowledge-base answer in the internal chat, how many statements there are, how many the articles do not carry and how many they contradict. The same check as on the widget, so both paths can be compared.",
        },
        how_to_read='VictoriaLogs: service:litellm AND "kb_answer_grounding"',
        read_automatically=False,
        source="evolutie.md 2.11, 2.14, 2.22",
    ),
    ObservabilitySignal(
        name="kb_answer_repaired",
        kind="log",
        service="litellm (internal chat)",
        purpose={
            "nl": (
                "Een intern antwoord is ingekort omdat de artikelen uitspraken niet droegen, met de "
                "lengte voor en na. Samen met kb_answer_repair_kept is te zien hoe vaak dat gebeurt."
            ),
            "en": "An internal answer was cut back because the articles did not carry some statements, with the length before and after. Together with kb_answer_repair_kept it shows how often that happens.",
        },
        how_to_read='VictoriaLogs: service:litellm AND ("kb_answer_repaired" OR "kb_answer_repair_kept")',
        read_automatically=False,
        source="evolutie.md 2.22, 2.23",
    ),
    ObservabilitySignal(
        name="query_variants_run / _added / _failed",
        kind="record",
        service="retrieval-api",
        purpose={
            "nl": (
                "Per eerste vraag hoeveel extra formuleringen er gezocht zijn, hoeveel nieuwe passages "
                "dat opleverde en hoeveel formuleringen mislukten. Laat zien of de herformuleringen op "
                "echt verkeer doen wat ze in de meting deden."
            ),
            "en": "Per first question, how many extra phrasings were searched, how many new passages that produced and how many phrasings failed. Shows whether the paraphrases do on real traffic what they did in the measurement.",
        },
        how_to_read="retrieval-api: decision_record of the retrieval trace",
        read_automatically=False,
        source="evolutie.md 2.33, 2.34",
    ),
    ObservabilitySignal(
        name="scripts/grounding_report.py",
        kind="report",
        service="portal-api (operator script)",
        purpose={
            "nl": (
                "Dagrapport over widget_messages.answer_signals: per dag hoeveel antwoorden er waren, "
                "hoeveel een onbewezen uitspraak bevatten, hoeveel gerepareerd of geweigerd werden, en "
                "hoe vaak een controle mislukte."
            ),
            "en": "Daily report over widget_messages.answer_signals: per day how many answers there were, how many contained an unsupported statement, how many were repaired or refused, and how often a check failed.",
        },
        how_to_read=(
            "docker exec -w /repo/klai-portal/backend klai-core-portal-api-1 "
            "python scripts/grounding_report.py [days] [org_id]"
        ),
        read_automatically=False,
        source="SPEC-RAG-ANSWER-JUDGES-001",
    ),
    ObservabilitySignal(
        name="scripts/simulate_conversations.py",
        kind="instrument",
        service="portal-api (operator script)",
        purpose={
            "nl": (
                "Meet hele gesprekken in plaats van losse antwoorden: een model speelt de bezoeker met "
                "een doel uit een echt gesprek. Alleen bedoeld om twee versies te vergelijken, nooit als "
                "absoluut slagingspercentage. Deelt de snelheidslimiet met bezoekers, dus met rem."
            ),
            "en": "Measures whole conversations instead of single answers: a model plays the visitor with a goal taken from a real conversation. Only meant to compare two versions, never as an absolute success rate. Shares the rate limit with visitors, so it is throttled.",
        },
        how_to_read=(
            "docker exec -w /repo/klai-portal/backend klai-core-portal-api-1 "
            "python scripts/simulate_conversations.py <widget-uuid> [conversations] [turns]"
        ),
        read_automatically=False,
        source="evolutie.md 2.19, 2.24, 2.35",
    ),
    ObservabilitySignal(
        name="scripts/audit-public-tenant-data.py",
        kind="guard",
        service="repository (pre-commit, CI)",
        purpose={
            "nl": (
                "Houdt gebruikscijfers per klant uit deze publieke repository. Draait bij elke commit "
                "en in CI, en blokkeert zonder dat iemand ernaar hoeft te kijken."
            ),
            "en": "Keeps per-customer usage figures out of this public repository. Runs on every commit and in CI, and blocks without anyone having to look.",
        },
        how_to_read=".githooks/pre-commit and .github/workflows/public-tenant-data.yml",
        read_automatically=True,
        source="PR #1531",
    ),
]
