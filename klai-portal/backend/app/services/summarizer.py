"""
Two-prompt meeting summarization using LiteLLM (OpenAI-compatible API).

Prompt 1 (extraction): structured JSON facts from transcript.
Prompt 2 (synthesis): readable Markdown summary in the meeting's language.
"""

import json

import httpx
import structlog

from app.core.config import settings

logger = structlog.get_logger()

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
  "decisions": [{"decision": "what was decided", "rationale": "why, or null", "decided_by": "name, or null"}],
  "action_items": [{"owner": "name or null", "task": "description", "deadline": "when, or null"}],
  "key_quotes": ["verbatim sentence worth preserving"],
  "open_questions": ["question1"],
  "next_steps": ["step1"]
}
Do not add commentary. If a field has no data, use an empty array.
"""

_SYNTHESIS_SYSTEM = """\
You are a professional meeting summarizer. Write a clear, concise meeting summary
based on the extracted facts provided. Use the language specified in the user message.
Structure (use headings in the target language):
1. A short executive summary paragraph (2-3 sentences).
2. A decisions section (if any decisions were made)
3. An open questions section (if any)
4. A next steps section (if any)
Do NOT include an action items section or a key quotes section — those are displayed separately in the UI.
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
        content = resp.json()["choices"][0]["message"]["content"]
        logger.info("llm_call_complete", model=model, response_length=len(content))
        return content


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
    return _parse_json_response(raw)


async def synthesize_summary(facts: dict, language: str) -> str:
    """Run synthesis prompt; return Markdown summary string."""
    lang_name = _LANGUAGE_NAMES.get(language or "en", "English")
    user_prompt = (
        f"Write the summary in {lang_name}.\n\nExtracted facts:\n{json.dumps(facts, ensure_ascii=False, indent=2)}"
    )
    return await _call_llm(_SYNTHESIS_SYSTEM, user_prompt, model=settings.synthesis_model, temperature=0.3)


def _parse_json_response(raw: str) -> dict:
    """Parse LLM JSON response, stripping markdown code fences if present."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.exception("extraction_json_parse_failed", raw_preview=raw[:200])
        raise


_STRUCTURED_KEYS = (
    ("speakers_present", "speakers"),
    ("topics", "topics"),
    ("decisions", "decisions"),
    ("action_items", "action_items"),
    ("key_quotes", "key_quotes"),
    ("open_questions", "open_questions"),
    ("next_steps", "next_steps"),
)


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
        "structured": {out: facts.get(src, []) for src, out in _STRUCTURED_KEYS},
    }
