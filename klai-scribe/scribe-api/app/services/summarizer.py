"""
Two-phase transcription summarization using LiteLLM (OpenAI-compatible API).

Supports two recording types:
  - "meeting": structured meeting analysis with speakers, decisions, action items
  - "recording": general audio content analysis with key points and quotes

Prompt 1 (extraction): structured JSON facts from transcript.
Prompt 2 (synthesis): readable Markdown summary in the transcription's language.
"""

import json
import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_http_client = httpx.AsyncClient(timeout=120.0)

_LANGUAGE_NAMES = {
    "nl": "Dutch",
    "en": "English",
    "de": "German",
    "fr": "French",
    "es": "Spanish",
    "it": "Italian",
    "pt": "Portuguese",
}

# -- Meeting prompts -----------------------------------------------------------

_MEETING_EXTRACTION_SYSTEM = """\
You are a precise meeting analyst. Extract factual information from the meeting transcript.
Return ONLY valid JSON with this exact structure:
{
  "speakers_present": ["name1", "name2"],
  "topics": ["topic1", "topic2"],
  "decisions": [
    {"decision": "...", "rationale": "... or null", "decided_by": "... or null"}
  ],
  "action_items": [
    {"owner": "name or null", "task": "description", "deadline": "... or null"}
  ],
  "commitments": [
    {"speaker": "...", "commitment": "..."}
  ],
  "key_quotes": ["verbatim sentence worth preserving"],
  "open_questions": ["question1"],
  "next_steps": ["step1"]
}
Do not add commentary. If a field has no data, use an empty array.
decisions.rationale and decisions.decided_by may be null if not mentioned.
action_items.deadline may be null if not mentioned.
"""

_MEETING_SYNTHESIS_SYSTEM = """\
You are a professional meeting summarizer. Write a clear, concise meeting summary
based on the extracted facts provided. Use the language specified. Structure:
1. A short executive summary paragraph (2-3 sentences).
2. ## Decisions (if any)
3. ## Action Items (if any, with owner)
4. ## Open Questions (if any)
5. ## Next Steps (if any)
Adapt section headings to the target language. Omit sections with no content.
"""

# -- Recording prompts ---------------------------------------------------------

_RECORDING_EXTRACTION_SYSTEM = """\
You are a precise content analyst. Extract factual information from this audio transcript.
Return ONLY valid JSON with this exact structure:
{
  "topics": ["topic1", "topic2"],
  "key_points": ["point1", "point2"],
  "quotes": ["memorable quote 1"],
  "conclusions": ["conclusion1"]
}
Do not add commentary. If a field has no data, use an empty array.
Quotes should be exact phrases from the transcript worth highlighting.
"""

_RECORDING_SYNTHESIS_SYSTEM = """\
You are a professional content summarizer. Write a clear, concise summary
based on the extracted information provided. Use the language specified. Structure:
1. A short summary paragraph (2-3 sentences).
2. ## Key Points (if any)
3. ## Conclusions (if any)
4. ## Notable Quotes (if any)
Adapt section headings to the target language. Omit sections with no content.
"""


def get_extraction_prompt(recording_type: str) -> str:
    """Return the type-specific extraction system prompt."""
    if recording_type == "meeting":
        return _MEETING_EXTRACTION_SYSTEM
    return _RECORDING_EXTRACTION_SYSTEM


def get_synthesis_prompt(recording_type: str) -> str:
    """Return the type-specific synthesis system prompt."""
    if recording_type == "meeting":
        return _MEETING_SYNTHESIS_SYSTEM
    return _RECORDING_SYNTHESIS_SYSTEM


async def _call_llm(system: str, user: str, temperature: float = 0.1) -> str:
    """Call LiteLLM chat completions endpoint and return the response content."""
    resp = await _http_client.post(
        f"{settings.litellm_base_url}/v1/chat/completions",
        headers={"Authorization": f"Bearer {settings.litellm_master_key}"},
        json={
            "model": settings.summarize_model,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        },
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


async def extract_facts(transcript: str, recording_type: str, language: str) -> dict:
    """Run extraction prompt; return structured facts dict."""
    lang_name = _LANGUAGE_NAMES.get(language or "en", "English")
    system = get_extraction_prompt(recording_type)
    user_prompt = f"Transcript ({lang_name}):\n\n{transcript}"

    raw = await _call_llm(system, user_prompt, temperature=0.1)

    # Parse JSON -- strip markdown code fences if present
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
    return json.loads(raw)


async def synthesize_summary(facts: dict, recording_type: str, language: str) -> str:
    """Run synthesis prompt; return Markdown summary string."""
    lang_name = _LANGUAGE_NAMES.get(language or "en", "English")
    system = get_synthesis_prompt(recording_type)
    user_prompt = (
        f"Write the summary in {lang_name}.\n\n"
        f"Extracted facts:\n{json.dumps(facts, ensure_ascii=False, indent=2)}"
    )
    return await _call_llm(system, user_prompt, temperature=0.3)


async def summarize_transcription(
    text: str,
    recording_type: str,
    language: str,
) -> dict:
    """Orchestrate extraction + synthesis; return summary_json dict."""
    facts = await extract_facts(text, recording_type, language)
    markdown = await synthesize_summary(facts, recording_type, language)

    if recording_type == "meeting":
        return {
            "type": "meeting",
            "markdown": markdown,
            "structured": {
                "speakers": facts.get("speakers_present", []),
                "topics": facts.get("topics", []),
                "decisions": facts.get("decisions", []),
                "action_items": facts.get("action_items", []),
                "commitments": facts.get("commitments", []),
                "key_quotes": facts.get("key_quotes", []),
                "open_questions": facts.get("open_questions", []),
                "next_steps": facts.get("next_steps", []),
            },
        }

    return {
        "type": "recording",
        "markdown": markdown,
        "structured": {
            "topics": facts.get("topics", []),
            "key_points": facts.get("key_points", []),
            "quotes": facts.get("quotes", []),
            "conclusions": facts.get("conclusions", []),
        },
    }
