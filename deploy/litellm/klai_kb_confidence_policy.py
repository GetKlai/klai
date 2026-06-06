"""Low-confidence KB evidence policy for LiteLLM retrieval context.

Environment-derived constants in this module are boot-time configuration:
production imports the LiteLLM hook once per process, and runtime env toggles
take effect on process restart.
"""

from __future__ import annotations

import os

from klai_citations import extract_salient_query_tokens

LOW_CONFIDENCE_INJECTION_TEXT = (
    "[Klai retrieval — lage relevantie]\n"
    "Het opgehaalde KB-materiaal heeft een lage relevantie-score voor "
    "deze vraag. Citeer alleen wat letterlijk in de chunks staat. Verzin "
    "GEEN integratie-routes, productnamen, stappen, bedragen, of "
    "technische details die niet expliciet in de chunks voorkomen. "
    "Sluit af met een vraag om verduidelijking aan de gebruiker als het "
    "materiaal de vraag niet volledig dekt — dat is beter dan een "
    "verzonnen antwoord."
)
LOW_CONFIDENCE_OPEN_CONTEXT_TEXT = (
    "[Klai retrieval — lage relevantie in Open modus]\n"
    "Het opgehaalde KB-materiaal heeft een lage relevantie-score voor "
    "deze vraag. Behandel de chunks als zwakke aanvullende context. "
    "Open mode blijft actief: weiger niet alleen omdat KB-bewijs zwak, "
    "tangentieel, of afwezig is. Antwoord vanuit algemene kennis of "
    "zichtbare gebruikerscontext wanneer de vraag daarmee betrouwbaar te "
    "beantwoorden is. Presenteer zulke delen expliciet als algemene kennis "
    "of als afgeleid uit de gebruikerscontext, niet als iets dat uit de "
    "kennisbank komt. Voor organisatie-specifieke feiten, prijzen, routes, "
    "productnamen, stappen, of bronclaims: verzin ze niet en zeg kort dat "
    "de kennisbank die specifieke claim niet ondersteunt."
)
LOW_CONFIDENCE_INJECTION_DISABLED = (
    os.getenv("KNOWLEDGE_DISABLE_LOW_CONFIDENCE_INJECTION", "0") == "1"
)

def low_confidence_query_tokens(query: object) -> set[str]:
    if not isinstance(query, str):
        return set()
    return extract_salient_query_tokens(query)


def has_direct_evidence_for_query(query: object, chunks: list[dict]) -> bool:
    """Return whether low-scored retrieval still has literal answer evidence."""
    tokens = low_confidence_query_tokens(query)
    if not tokens:
        return False
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        chunk_tokens = extract_salient_query_tokens(
            " ".join(
                str(chunk.get(key) or "")
                for key in ("title", "heading_path", "source_label", "text", "content")
            )
        )
        if tokens & chunk_tokens:
            return True
    return False


def should_apply_low_confidence_injection(
    confidence_band: object,
    *,
    user_query: object,
    evidence_chunks: list[dict],
) -> bool:
    if confidence_band not in ("low", "unknown"):
        return False
    return not has_direct_evidence_for_query(user_query, evidence_chunks)
