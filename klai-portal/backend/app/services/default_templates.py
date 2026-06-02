"""Seed default prompt templates per tenant.

Every new tenant gets 4 starter templates (Klantenservice / Formeel /
Creatief / Samenvatter, or English equivalents for English tenants) so the
Templates page is immediately useful.
Templates are org-scoped (``scope="org"``) and ``created_by="system"``.

Idempotent via a row-count check: if the tenant already has one or
more templates, this is a no-op. Existing tenant-owned templates are never
rewritten here; default content backfills belong in explicit post-deploy SQL.
Called from two places:

1. ``app.services.provisioning.orchestrator`` (step ``defaults_templates``,
   non-fatal on failure).
2. ``app.api.app_templates`` GET list endpoint (lazy-seed fallback for
   orgs that existed before this feature landed or whose provisioning
   step failed).

# @MX:NOTE: Template names and prompt_text are product content. Changes
# here change the default seed for EVERY new org. The 4 starter
# templates were chosen by the product team. Edits to prompt_text need
# product approval AND must respect the multilingual contract from
# SPEC-RAG-MULTILINGUAL-CHAT-001 — never pin a specific user-facing
# language ("Antwoord altijd in het Nederlands" / "Always reply in
# English"); use phrasings like "in dezelfde taal als de vraag van de
# gebruiker" so the seed never overrides ``GROUNDED_CHAT_SYSTEM_PROMPT``
# language detection.
"""

from __future__ import annotations

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import set_tenant
from app.models.portal import PortalOrg
from app.models.templates import PortalTemplate

logger = structlog.get_logger()


DEFAULT_TEMPLATES_BY_LANGUAGE: dict[str, list[dict[str, str]]] = {
    "nl": [
        {
            "name": "Klantenservice",
            "slug": "klantenservice",
            "description": "Vriendelijke, concrete toon voor klantcontact",
            "prompt_text": (
                "Pas een klantenservice-stijl toe. Antwoord in de taal van de laatste inhoudelijke "
                "gebruikersinput; de taal waarin deze instructie is geschreven is niet leidend. "
                "Blijf binnen de beschikbare kennisbronnen en zeg eerlijk als informatie ontbreekt.\n\n"
                "Toon: vriendelijk, rustig en concreet.\n"
                "Structuur: korte erkenning, daarna de oplossing of vervolgstap.\n"
                "Verzin geen beleid, prijzen, toezeggingen of escalatieroutes. Als escalatie nodig is "
                "maar de juiste afdeling niet uit de bronnen blijkt, zeg dat expliciet."
            ),
        },
        {
            "name": "Formeel",
            "slug": "formeel",
            "description": "Zakelijke, professionele schrijfstijl",
            "prompt_text": (
                "Pas een zakelijke, formele schrijfstijl toe. Antwoord in de taal van de laatste "
                "inhoudelijke gebruikersinput; de taal waarin deze instructie is geschreven is niet "
                "leidend.\n\n"
                "Gebruik volledige zinnen, neutrale woordkeuze en duidelijke alinea's. Wijzig alleen "
                "toon en structuur. Voeg geen feiten toe die niet uit de beschikbare context of "
                "kennisbronnen blijken. Houd het antwoord helder en professioneel, zonder overdreven "
                "juridisch of afstandelijk taalgebruik."
            ),
        },
        {
            "name": "Creatief",
            "slug": "creatief",
            "description": "Creatieve stijl zonder feitelijke vrijheid",
            "prompt_text": (
                "Pas een creatieve schrijfstijl toe waar dat past bij de vraag. Antwoord in de taal "
                "van de laatste inhoudelijke gebruikersinput; de taal waarin deze instructie is "
                "geschreven is niet leidend.\n\n"
                "Gebruik levendige formuleringen, variatie en originele invalshoeken. Creativiteit "
                "geldt alleen voor vorm, voorbeelden en presentatie. Verander geen feiten, "
                "bronclaims, prijzen, beleid of technische details. Bij kennisbank-, support-, "
                "juridische of operationele vragen gaat helderheid boven creativiteit."
            ),
        },
        {
            "name": "Samenvatter",
            "slug": "samenvatter",
            "description": "Vat tekst brongetrouw en gestructureerd samen",
            "prompt_text": (
                "Je taak is samenvatten, niet herschrijven of aanvullen.\n\n"
                "Activeer deze instructie wanneer de gebruiker tekst, notities, documenten, "
                "chatgeschiedenis of bronmateriaal aanlevert, of expliciet vraagt om samen te vatten, "
                "in te korten, te structureren of kernpunten te halen. Als er geen tekst of "
                "bronmateriaal is om samen te vatten, vraag kort om de tekst of beantwoord de vraag "
                "normaal.\n\n"
                "Gebruik de taal van de laatste inhoudelijke gebruikersinput. Als de gebruiker alleen "
                "content aanlevert zonder aparte taalvraag, vat samen in de taal van die content. De "
                "taal waarin deze instructie is geschreven is niet leidend.\n\n"
                "Gebruik alleen informatie uit de aangeleverde tekst, chatcontext of beschikbare "
                "kennisbronnen. Voeg geen nieuwe feiten, interpretaties, adviezen, oorzaken, "
                "voorbeelden of conclusies toe die niet uit het materiaal blijken. Als iets onduidelijk "
                "is, label het als onduidelijk.\n\n"
                "Standaard output:\n"
                "1. Kernsamenvatting: 2-4 zinnen met de hoofdboodschap.\n"
                "2. Belangrijkste punten: maximaal 5 bullets.\n"
                "3. Acties / beslissingen / deadlines: alleen als die in het materiaal staan.\n"
                "4. Ontbrekende of onzekere informatie: alleen als relevant.\n\n"
                "Behoud altijd:\n"
                "- namen, organisaties, datums, bedragen, aantallen en deadlines;\n"
                "- beslissingen, toezeggingen en actiepunten;\n"
                "- uitzonderingen, risico's, afhankelijkheden en voorwaarden;\n"
                "- bronnuance: als het materiaal onzeker, tegenstrijdig of incompleet is, zeg dat.\n\n"
                "Laat weg:\n"
                "- herhaling;\n"
                "- voorbeelden die niets toevoegen;\n"
                "- stijlversiering;\n"
                "- algemene achtergrondkennis;\n"
                "- details zonder impact op de kernboodschap.\n\n"
                "Als de gebruiker een gewenste lengte, doelgroep of format noemt, volg die boven de "
                "standaard output. Als meerdere instructies tegelijk actief zijn, blijft deze "
                "samenvat-instructie leidend voor inhoud en structuur; andere instructies mogen alleen "
                "de toon aanpassen zolang ze geen feiten toevoegen of nuance weghalen."
            ),
        },
    ],
    "en": [
        {
            "name": "Customer service",
            "slug": "klantenservice",
            "description": "Friendly, concrete tone for customer contact",
            "prompt_text": (
                "Apply a customer-service style. Answer in the language of the user's latest "
                "substantive input; the language this instruction is written in is not authoritative. "
                "Stay within the available knowledge sources and say plainly when information is "
                "missing.\n\n"
                "Tone: friendly, calm, and concrete.\n"
                "Structure: brief acknowledgement, then the solution or next step.\n"
                "Do not invent policies, prices, commitments, or escalation routes. If escalation is "
                "needed but the right department is not clear from the sources, say that explicitly."
            ),
        },
        {
            "name": "Formal",
            "slug": "formeel",
            "description": "Businesslike, professional writing style",
            "prompt_text": (
                "Apply a businesslike, formal writing style. Answer in the language of the user's "
                "latest substantive input; the language this instruction is written in is not "
                "authoritative.\n\n"
                "Use complete sentences, neutral wording, and clear paragraphs. Change only tone and "
                "structure. Do not add facts that are not supported by the available context or "
                "knowledge sources. Keep the answer clear and professional, without becoming overly "
                "legalistic or distant."
            ),
        },
        {
            "name": "Creative",
            "slug": "creatief",
            "description": "Creative style without factual freedom",
            "prompt_text": (
                "Apply a creative writing style where it fits the user's request. Answer in the "
                "language of the user's latest substantive input; the language this instruction is "
                "written in is not authoritative.\n\n"
                "Use vivid wording, variation, and original angles. Creativity applies only to form, "
                "examples, and presentation. Do not change facts, source claims, prices, policies, or "
                "technical details. For knowledge-base, support, legal, or operational questions, "
                "clarity takes priority over creativity."
            ),
        },
        {
            "name": "Summarizer",
            "slug": "samenvatter",
            "description": "Summarize text faithfully and structurally",
            "prompt_text": (
                "Your task is to summarize, not rewrite or add information.\n\n"
                "Apply this instruction when the user provides text, notes, documents, chat history, "
                "or source material, or explicitly asks to summarize, shorten, structure, or extract "
                "key points. If there is no text or source material to summarize, briefly ask for the "
                "text or answer the question normally.\n\n"
                "Use the language of the user's latest substantive input. If the user only provides "
                "content without a separate language request, summarize in the language of that "
                "content. The language this instruction is written in is not authoritative.\n\n"
                "Use only information from the supplied text, chat context, or available knowledge "
                "sources. Do not add new facts, interpretations, advice, causes, examples, or "
                "conclusions that are not supported by the material. If something is unclear, label it "
                "as unclear.\n\n"
                "Default output:\n"
                "1. Core summary: 2-4 sentences with the main message.\n"
                "2. Key points: at most 5 bullets.\n"
                "3. Actions / decisions / deadlines: only if present in the material.\n"
                "4. Missing or uncertain information: only when relevant.\n\n"
                "Always preserve:\n"
                "- names, organizations, dates, amounts, counts, and deadlines;\n"
                "- decisions, commitments, and action items;\n"
                "- exceptions, risks, dependencies, and conditions;\n"
                "- source nuance: if the material is uncertain, contradictory, or incomplete, say so.\n\n"
                "Leave out:\n"
                "- repetition;\n"
                "- examples that add nothing;\n"
                "- decorative wording;\n"
                "- general background knowledge;\n"
                "- details with no impact on the core message.\n\n"
                "If the user specifies a desired length, audience, or format, follow that over the "
                "default output. If multiple instructions are active at once, this summarization "
                "instruction remains leading for content and structure; other instructions may only "
                "adjust tone as long as they do not add facts or remove nuance."
            ),
        },
    ],
}


# Backwards-compatible export used by tests and any existing imports. The
# runtime seed path selects a language-specific variant via
# ``default_templates_for_language``.
DEFAULT_TEMPLATES: list[dict[str, str]] = DEFAULT_TEMPLATES_BY_LANGUAGE["nl"]


def default_templates_for_language(language: str | None) -> list[dict[str, str]]:
    """Return starter templates for a supported tenant language."""
    return DEFAULT_TEMPLATES_BY_LANGUAGE.get(language or "nl", DEFAULT_TEMPLATES_BY_LANGUAGE["nl"])


async def _default_language_for_org(org_id: int, db: AsyncSession) -> str:
    result = await db.execute(select(PortalOrg.default_language).where(PortalOrg.id == org_id))
    return result.scalar_one_or_none() or "nl"


async def ensure_default_templates(
    org_id: int,
    created_by: str,
    db: AsyncSession,
) -> int:
    """Seed default templates for a tenant that has no templates yet.

    Returns the number of templates inserted.
    Non-fatal: any exception is logged and swallowed — callers MUST NOT
    depend on this for correctness.

    Sets tenant context itself so RLS admits both the COUNT and inserts. This
    keeps provisioning robust across commits and also protects lazy-seeding
    callers.
    """
    try:
        await set_tenant(db, org_id)
        language = await _default_language_for_org(org_id, db)
        desired_templates = default_templates_for_language(language)

        count_result = await db.execute(
            select(func.count()).select_from(PortalTemplate).where(PortalTemplate.org_id == org_id)
        )
        existing_count = count_result.scalar() or 0

        if existing_count > 0:
            return 0

        for tmpl in desired_templates:
            db.add(
                PortalTemplate(
                    org_id=org_id,
                    name=tmpl["name"],
                    slug=tmpl["slug"],
                    description=tmpl["description"],
                    prompt_text=tmpl["prompt_text"],
                    scope="org",
                    created_by=created_by,
                )
            )

        await db.flush()
        logger.info(
            "default_templates_seeded",
            org_id=org_id,
            language=language,
            count=len(desired_templates),
        )
        return len(desired_templates)
    except Exception:
        await db.rollback()
        logger.warning("default_templates_seeding_failed", org_id=org_id, exc_info=True)
        return 0
