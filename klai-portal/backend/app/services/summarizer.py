"""
Two-prompt meeting summarization using LiteLLM (OpenAI-compatible API).

Prompt 1 (extraction): structured JSON facts from transcript.
Prompt 2 (synthesis): readable Markdown summary in the meeting's language.
"""

import json
import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_LANGUAGE_NAMES = {
    "nl": "Dutch",
    "en": "English",
    "de": "German",
    "fr": "French",
    "es": "Spanish",
    "it": "Italian",
    "pt": "Portuguese",
}

_EXTRACTION_SYSTEM = """\
You are a precise meeting analyst. Extract factual information from the meeting transcript.
Return ONLY valid JSON with this exact structure:
{
  "speakers_present": ["name1", "name2"],
  "topics": ["topic1", "topic2"],
  "decisions": ["decision1"],
  "action_items": [{"owner": "name or null", "task": "description"}],
  "open_questions": ["question1"],
  "next_steps": ["step1"]
}
Do not add commentary. If a field has no data, use an empty array.
"""

_SYNTHESIS_SYSTEM = """\
You are a professional meeting summarizer. Write a clear, concise meeting summary
based on the extracted facts provided. Use the language specified. Structure:
1. A short executive summary paragraph (2-3 sentences).
2. ## Beslissingen / Decisions (if any)
3. ## Actiepunten / Action Items (if any, with owner)
4. ## Open vragen / Open Questions (if any)
5. ## Volgende stappen / Next Steps (if any)
Adapt section headings to the target language.
"""


async def _call_llm(system: str, user: str, model: str, temperature: float = 0.1) -> str:
    """Call LiteLLM chat completions endpoint and return the response content."""
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            f"{settings.litellm_base_url}/v1/chat/completions",
            headers={"Authorization": f"Bearer {settings.litellm_master_key}"},
            json={
                "model": model,
                "temperature": temperature,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


def _build_transcript_text(transcript_text: str, segments: list[dict] | None) -> str:
    """Build transcript string for the extraction prompt."""
    if segments:
        return "\n".join(f"{seg.get('speaker', 'Unknown')}: {seg.get('text', '')}" for seg in segments)
    return transcript_text


async def extract_facts(
    transcript_text: str,
    segments: list[dict] | None,
    language: str,
) -> dict:
    """Run extraction prompt; return structured facts dict."""
    lang_name = _LANGUAGE_NAMES.get(language or "en", "English")
    transcript = _build_transcript_text(transcript_text, segments)
    has_speakers = bool(segments and any(s.get("speaker") for s in segments))

    user_prompt = (
        f"Meeting transcript ({lang_name}, speaker-labeled):\n\n{transcript}"
        if has_speakers
        else f"Meeting transcript ({lang_name}, no speaker labels):\n\n{transcript}"
    )

    raw = await _call_llm(_EXTRACTION_SYSTEM, user_prompt, model=settings.extraction_model, temperature=0.1)

    # Parse JSON -- strip markdown code fences if present
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
    return json.loads(raw)


async def synthesize_summary(facts: dict, language: str) -> str:
    """Run synthesis prompt; return Markdown summary string."""
    lang_name = _LANGUAGE_NAMES.get(language or "en", "English")
    user_prompt = (
        f"Write the summary in {lang_name}.\n\nExtracted facts:\n{json.dumps(facts, ensure_ascii=False, indent=2)}"
    )
    return await _call_llm(_SYNTHESIS_SYSTEM, user_prompt, model=settings.synthesis_model, temperature=0.3)


async def summarize_meeting(
    transcript_text: str,
    segments: list[dict] | None,
    language: str,
) -> dict:
    """Orchestrate extraction + synthesis; return summary_json dict."""
    facts = await extract_facts(transcript_text, segments, language)
    markdown = await synthesize_summary(facts, language)
    return {
        "markdown": markdown,
        "structured": {
            "speakers": facts.get("speakers_present", []),
            "topics": facts.get("topics", []),
            "decisions": facts.get("decisions", []),
            "action_items": facts.get("action_items", []),
            "open_questions": facts.get("open_questions", []),
            "next_steps": facts.get("next_steps", []),
        },
    }
