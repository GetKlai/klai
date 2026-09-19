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
    purpose: str
    how_to_read: str
    read_automatically: bool
    source: str


SIGNALS: list[ObservabilitySignal] = [
    ObservabilitySignal(
        name="partner_chat_answer_judge",
        kind="log",
        service="portal-api (widget)",
        purpose=(
            "Per widgetantwoord het oordeel van beide controles achteraf: of het de vraag "
            "beantwoordt, en of de artikelen de uitspraken dragen. Laat zien waarom een bezoeker "
            "een antwoord, een weigering of een afspraakknop kreeg."
        ),
        how_to_read='VictoriaLogs: service:portal-api AND "partner_chat_answer_judge"',
        read_automatically=False,
        source="SPEC-RAG-ANSWER-JUDGES-001, REQ-2 en REQ-3",
    ),
    ObservabilitySignal(
        name="partner_chat_answer_repair",
        kind="log",
        service="portal-api (widget)",
        purpose=(
            "Per reparatie of die gelukt is, niets overliet, of mislukte. Zonder dit is niet te zien "
            "hoe vaak een antwoord is ingekort omdat de artikelen een uitspraak niet droegen."
        ),
        how_to_read='VictoriaLogs: service:portal-api AND "partner_chat_answer_repair"',
        read_automatically=False,
        source="evolutie.md 2.8 en 2.9",
    ),
    ObservabilitySignal(
        name="partner_chat_turn_timing",
        kind="log",
        service="portal-api (widget)",
        purpose=(
            "Per beurt waar de wachttijd heen ging: zoeken, controle vooraf, schrijven en de "
            "controles achteraf. Nodig om te zien of een nieuwe controle het gesprek trager maakt."
        ),
        how_to_read='VictoriaLogs: service:portal-api AND "partner_chat_turn_timing"',
        read_automatically=False,
        source="SPEC-RAG-ANSWER-JUDGES-001, REQ-4",
    ),
    ObservabilitySignal(
        name="answer_grounding_late",
        kind="log",
        service="portal-api (widget)",
        purpose=(
            "Het oordeel van een controle die langer duurde dan haar budget van 4 seconden. De "
            "bezoeker wacht er niet op; het antwoord blijft ongecontroleerd. Dit legt vast wat die "
            "controle gevonden zou hebben, zodat te beslissen is of het budget omhoog moet."
        ),
        how_to_read='VictoriaLogs: service:portal-api AND "answer_grounding_late"',
        read_automatically=False,
        source="evolutie.md 2.37",
    ),
    ObservabilitySignal(
        name="widget_messages.answer_signals",
        kind="record",
        service="portal-api (database)",
        purpose=(
            "Per opgeslagen widgetantwoord hoeveel uitspraken de artikelen niet droegen, of het "
            "gerepareerd is, welke beslissing viel, en of een controle mislukte. De bron voor het "
            "dagrapport."
        ),
        how_to_read="Postgres: kolom answer_signals in widget_messages (JSONB)",
        read_automatically=False,
        source="SPEC-RAG-ANSWER-JUDGES-001",
    ),
    ObservabilitySignal(
        name="kb_answer_grounding",
        kind="log",
        service="litellm (interne chat)",
        purpose=(
            "Per strikt kennisbankantwoord in de interne chat hoeveel uitspraken er zijn, hoeveel de "
            "artikelen niet dragen en hoeveel ze tegenspreken. Dezelfde controle als op de widget, "
            "zodat beide paden te vergelijken zijn."
        ),
        how_to_read='VictoriaLogs: service:litellm AND "kb_answer_grounding"',
        read_automatically=False,
        source="evolutie.md 2.11, 2.14 en 2.22",
    ),
    ObservabilitySignal(
        name="kb_answer_repaired",
        kind="log",
        service="litellm (interne chat)",
        purpose=(
            "Een intern antwoord is ingekort omdat de artikelen uitspraken niet droegen, met de "
            "lengte voor en na. Samen met kb_answer_repair_kept is te zien hoe vaak dat gebeurt."
        ),
        how_to_read='VictoriaLogs: service:litellm AND ("kb_answer_repaired" OR "kb_answer_repair_kept")',
        read_automatically=False,
        source="evolutie.md 2.22 en 2.23",
    ),
    ObservabilitySignal(
        name="query_variants_run / _added / _failed",
        kind="record",
        service="retrieval-api",
        purpose=(
            "Per eerste vraag hoeveel extra formuleringen er gezocht zijn, hoeveel nieuwe passages "
            "dat opleverde en hoeveel formuleringen mislukten. Laat zien of de herformuleringen op "
            "echt verkeer doen wat ze in de meting deden."
        ),
        how_to_read="Het beslisrecord van een zoekopdracht (decision_record in de retrieval-trace)",
        read_automatically=False,
        source="evolutie.md 2.33 en 2.34",
    ),
    ObservabilitySignal(
        name="scripts/grounding_report.py",
        kind="report",
        service="portal-api (operatorscript)",
        purpose=(
            "Dagrapport over widget_messages.answer_signals: per dag hoeveel antwoorden er waren, "
            "hoeveel een onbewezen uitspraak bevatten, hoeveel gerepareerd of geweigerd werden, en "
            "hoe vaak een controle mislukte."
        ),
        how_to_read=(
            "docker exec -w /repo/klai-portal/backend klai-core-portal-api-1 "
            "python scripts/grounding_report.py [dagen] [org_id]"
        ),
        read_automatically=False,
        source="SPEC-RAG-ANSWER-JUDGES-001",
    ),
    ObservabilitySignal(
        name="scripts/simulate_conversations.py",
        kind="instrument",
        service="portal-api (operatorscript)",
        purpose=(
            "Meet hele gesprekken in plaats van losse antwoorden: een model speelt de bezoeker met "
            "een doel uit een echt gesprek. Alleen bedoeld om twee versies te vergelijken, nooit als "
            "absoluut slagingspercentage. Deelt de snelheidslimiet met bezoekers, dus met rem."
        ),
        how_to_read=(
            "docker exec -w /repo/klai-portal/backend klai-core-portal-api-1 "
            "python scripts/simulate_conversations.py <widget-uuid> [gesprekken] [beurten]"
        ),
        read_automatically=False,
        source="evolutie.md 2.19, 2.24 en 2.35",
    ),
    ObservabilitySignal(
        name="scripts/audit-public-tenant-data.py",
        kind="guard",
        service="repository (pre-commit en CI)",
        purpose=(
            "Houdt gebruikscijfers per klant uit deze publieke repository. Draait bij elke commit "
            "en in CI, en blokkeert zonder dat iemand ernaar hoeft te kijken."
        ),
        how_to_read="Draait vanzelf; faalt de commit, dan staat de reden in de uitvoer",
        read_automatically=True,
        source="PR #1531",
    ),
]
