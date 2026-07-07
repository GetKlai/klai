"""Knowledge-base context prompt assembly for the LiteLLM hook."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from klai_citations import render_evidence_context
from klai_chat_prompts import KB_CONTEXT_LANGUAGE_REMINDER
from klai_kb_answer_policy import kb_chunks_present_header
from klai_kb_urls import absolute_image_url, chunk_source_url

KB_ANSWER_FORMAT_INSTRUCTION = (
    "[ANSWER FORMAT — always follow this, EXCEPT where an "
    "active Klai Template (see block below) directs a "
    "different tone, structure, opening, wording, numbering, "
    "or whitespace:\n"
    "1. Default opening is a short TL;DR (2-3 sentences) of "
    "the answer. Write it as normal prose, not as a Markdown "
    "heading. Use the standard short-summary label in the "
    "SAME LANGUAGE as the user's question — NOT the language "
    "of the source documents. 'TL;DR' is universally "
    "understood and is a safe default in any language. SKIP "
    "this opening when an active template asks for a "
    "creative / narrative / story-style answer (e.g. 'Creatief') "
    "or for a fixed output form / specific opening.\n"
    "2. Do not write source lists, URLs, Markdown links, footnotes, "
    "or citation numbers. The application adds citations after "
    "generation from retrieved metadata.\n"
    "3. Do not preserve source-list step numbers when a retrieved "
    "chunk starts mid-procedure; rewrite steps into a clean sequence. "
    "This does not apply to numbering required by an active template.\n"
    "4. If needed for a clear explanation, or if the user asks for "
    "more detail, follow with an extended answer with inline "
    "explanation.\n"
    "   Be concise but complete. No walls of text — write as if you "
    "are helping a colleague.\n\n"
    "STRICT:\n"
    "- NEVER invent or write a URL. No notion.so, no portal.voys.nl, "
    "no guessed documentation paths.\n"
    "- NEVER use placeholder, example, or documentation-only domains.\n"
    "- Never use a title as URL target.\n\n"
    "IMAGES:\n"
    "- Only include image markdown if a chunk below already contains "
    "an explicit ![...](...) image tag.\n"
    "- Change NOTHING about the image URL. Copy the entire "
    "![...](https://...) tag exactly.\n"
    "- NEVER create, guess, search for, or suggest an image URL.\n"
    "- NEVER use placeholder, example, or documentation-only image URLs.\n"
    "- If the user asks for an image from the knowledge base and no "
    "explicit image tag is present in the chunks, say plainly that "
    "no knowledge-base image is available.\n"
    "- Knowledge-base images only: these image markdown rules apply "
    "only to images retrieved from knowledge-base chunks. They do "
    "not define how user-provided attachments may be used — see the "
    "[User-provided content] note above.\n"
    "- Do NOT add images in the TL;DR (section 1).]\n"
)

@dataclass(frozen=True)
class KbContextPrompt:
    context_block: str
    allowed_source_urls: list[str]
    allowed_image_urls: list[str]
    citation_source_urls: dict[str, str]
    low_confidence_injection_applied: bool

def build_kb_context_prompt(
    *,
    kb_narrow: bool,
    context_chunks: list[dict[str, Any]],
    trusted_sources: list[dict[str, Any]],
    templates_block: str,
    images_base_url: str,
    low_confidence_inject: bool,
    low_confidence_injection_disabled: bool,
    low_confidence_strict_text: str,
    low_confidence_open_text: str,
) -> KbContextPrompt:
    """Build the chunks-present KB prompt block and its metadata side effects."""
    lines = [kb_chunks_present_header(kb_narrow), KB_ANSWER_FORMAT_INSTRUCTION]

    # Templates intentionally sit after ANSWER FORMAT and before chunks so
    # template tone/shape is the freshest formatting instruction before content.
    if templates_block:
        lines.append(templates_block)

    citation_source_urls: dict[str, str] = {}
    for chunk_index, chunk in enumerate(context_chunks, 1):
        source_url = chunk_source_url(chunk)
        if source_url:
            citation_source_urls[str(chunk_index)] = source_url

    allowed_source_urls = sorted(
        source["url"] for source in trusted_sources if isinstance(source.get("url"), str)
    )
    allowed_image_urls: set[str] = set()

    context_block = render_evidence_context(context_chunks, include_source_urls=False)
    if context_block:
        lines.append(context_block)

    for chunk in context_chunks:
        absolute_urls = [
            url
            for url in (
                absolute_image_url(u, images_base_url=images_base_url)
                for u in chunk.get("image_urls") or []
            )
            if url
        ]
        allowed_image_urls.update(absolute_urls)
        for i, img_url in enumerate(absolute_urls, 1):
            lines.append(f"![afbeelding {i}]({img_url})")
        lines.append("")

    low_confidence_applied = low_confidence_inject and not low_confidence_injection_disabled
    lines.append("[End knowledge base context]")
    if low_confidence_applied:
        lines.append(low_confidence_strict_text if kb_narrow else low_confidence_open_text)

    lines.append(KB_CONTEXT_LANGUAGE_REMINDER)

    return KbContextPrompt(
        context_block="\n".join(lines),
        allowed_source_urls=allowed_source_urls,
        allowed_image_urls=sorted(allowed_image_urls),
        citation_source_urls=citation_source_urls,
        low_confidence_injection_applied=low_confidence_applied,
    )
