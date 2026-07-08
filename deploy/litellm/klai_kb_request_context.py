"""Request and message-context helpers for LiteLLM KB retrieval.

Environment-derived constants in this module are boot-time configuration:
production imports the LiteLLM hook once per process, and runtime env toggles
take effect on process restart.
"""

from __future__ import annotations

import os
import re
from typing import Any

RETRIEVE_HISTORY_API_CONTENT_LIMIT_CHARS = 8000
RETRIEVE_HISTORY_MAX_CONTENT_CHARS = min(
    int(os.getenv("KNOWLEDGE_RETRIEVE_HISTORY_MAX_CONTENT_CHARS", "7800")),
    RETRIEVE_HISTORY_API_CONTENT_LIMIT_CHARS - 100,
)
RETRIEVE_HISTORY_OMISSION_MARKER = (
    "\n\n[... content omitted from retrieval conversation history ...]\n\n"
)

TRIVIAL_PATTERNS = re.compile(
    r"^(ok|okay|oke|oké|ja|nee|yes|no|bedankt|thanks|thank you|"
    r"dank je|dank u|graag|np|prima|goed|good|sure|hmm+|ah+|oh+|"
    r"begrepen|understood|clear|got it|doei|bye|hoi|hallo|hello|hi)[\s!.?]*$",
    re.IGNORECASE,
)

META_QUERY_PATTERNS = re.compile(
    r"^\s*(?:"
    r"wat\s+(?:kan|kun)\s+(?:ik|je|jij)"
    r"(?:\s+(?:hier|met\s+(?:klai|jou|je)|allemaal|doen)){0,3}"
    r"|wat\s+(?:is|doet|doe)\s+(?:klai|jij|je)"
    r"|wat\s+(?:kan|kun)\s+(?:klai|je|jij)(?:\s+doen)?"
    r"|hoe\s+werkt\s+(?:deze\s+chat|dit|klai|jij|je)"
    r"|hoe\s+(?:gebruik|werk)\s+ik\s+(?:met\s+)?(?:deze\s+chat|klai|dit)"
    r"|wie\s+ben\s+je"
    r"|waarvoor\s+is\s+(?:dit|klai)"
    r"|waar\s+is\s+(?:dit|klai)\s+voor"
    r"|help"
    r"|what\s+can\s+(?:i|you)\s+do"
    r"(?:\s+(?:here|with\s+(?:klai|you))){0,2}"
    r"|what\s+(?:is|are|does)\s+klai(?:\s+do)?"
    r"|how\s+does\s+(?:this\s+chat|this|klai)\s+work"
    r"|how\s+do\s+i\s+use\s+(?:this\s+chat|klai|this)"
    r"|who\s+are\s+you"
    r")[\s!.?]*$",
    re.IGNORECASE,
)

TITLE_GENERATION_RE = re.compile(
    r"(?:"
    r"\b(?:generate|write|create|provide|give|summarize)\b"
    r"(?=[\s\S]{0,240}\b(?:title|name|summary)\b)"
    r"(?=[\s\S]{0,240}\b(?:conversation|chat)\b)"
    r"|\b(?:title|name)\s+(?:this|the)\s+(?:conversation|chat)\b"
    r")",
    re.IGNORECASE,
)

WEB_SEARCH_TOOL_RE = re.compile(
    r"(?:^|[_\-\s])"
    r"(?:web[_\-\s]*search|websearch|search[_\-\s]*web|browser|searx|firecrawl)"
    r"(?:$|[_\-\s])",
    re.IGNORECASE,
)

KLAI_SOURCES_METADATA_MARKER_RE = re.compile(
    r"\n?<!--\s*klai_sources=[A-Za-z0-9_-]+={0,2}\s*-->\n?",
    re.IGNORECASE,
)
# Multilingual: an English chat emits an English footer (**Sources** / **Agent
# activity**). Match those too so a backend or model-imitated English footer is
# stripped from history before the next model input.
# SPEC-CHAT-SOURCE-DISCLOSURE-001 REQ-DISC-05.
KLAI_BACKEND_FOOTER_HEADING_RE = re.compile(
    r"(?im)^[ \t]*(?:\*\*)?(Bronnen|Sources|Agent activiteit|Agent activity)(?:\*\*)?[ \t]*$"
)
_FOOTER_SOURCES_HEADINGS = {"bronnen", "sources"}
_FOOTER_ACTIVITY_HEADINGS = {"agent activiteit", "agent activity"}


def is_trivial(text: str) -> bool:
    text = text.strip()
    if len(text) < 8:
        return True
    return bool(TRIVIAL_PATTERNS.match(text))


def is_meta_query(text: str) -> bool:
    """Return whether the user asks about Klai itself, not KB content."""
    return bool(META_QUERY_PATTERNS.match(text.strip()))


def message_text(message: dict[str, Any]) -> str:
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            str(part.get("text", ""))
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return ""


def is_title_generation_request(messages: list[dict]) -> bool:
    """Detect LibreChat's internal conversation-title prompt."""
    for message in messages:
        if message.get("role") not in {"system", "developer", "user"}:
            continue
        text = message_text(message)
        if not text or len(text) > 4000:
            continue
        if TITLE_GENERATION_RE.search(text):
            return True
    return False


def truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}
    if isinstance(value, (int, float)):
        return value != 0
    return False


def request_metadata(data: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for candidate in (
        data.get("metadata"),
        data.get("litellm_metadata"),
        data.get("litellm_params", {}).get("metadata")
        if isinstance(data.get("litellm_params"), dict)
        else None,
    ):
        if isinstance(candidate, dict):
            metadata.update(candidate)
    return metadata


def tool_name(tool: object) -> str:
    if not isinstance(tool, dict):
        return ""
    function = tool.get("function")
    names = [
        tool.get("name"),
        tool.get("type"),
        function.get("name") if isinstance(function, dict) else None,
    ]
    return " ".join(str(name) for name in names if name)


def tool_description(tool: object) -> str:
    if not isinstance(tool, dict):
        return ""
    function = tool.get("function")
    descriptions = [
        tool.get("description"),
        function.get("description") if isinstance(function, dict) else None,
    ]
    return " ".join(str(description) for description in descriptions if description)


def is_web_search_tool(tool: object) -> bool:
    """Return True when a single tool entry advertises web search."""
    name = tool_name(tool)
    if name and WEB_SEARCH_TOOL_RE.search(name):
        return True
    if name.strip().lower() == "search" and "web" in tool_description(tool).lower():
        return True
    return False


def request_has_web_search(data: dict[str, Any]) -> bool:
    """Return True when this LiteLLM request advertises Web Search."""
    metadata = request_metadata(data)
    for key in (
        "klai_web_search_enabled",
        "web_search_enabled",
        "webSearch",
        "web_search",
    ):
        if truthy(metadata.get(key)):
            return True

    if isinstance(data.get("web_search_options"), dict):
        return True

    tools = data.get("tools")
    if not isinstance(tools, list):
        return False
    return any(is_web_search_tool(tool) for tool in tools)


def strip_web_search_tools(data: dict[str, Any]) -> int:
    """Remove web-search affordances from a LiteLLM request in place.

    Strict KB mode (``kb_narrow=True``) promises answers grounded ONLY in the
    knowledge base. The web-search tool is a LibreChat surface the KB hook does
    not otherwise gate; leaving it in ``data["tools"]`` lets the model call it
    and fold live web results into a Strict answer. That made "web is not an
    answer source in Strict" depend on the model obeying a prompt — prompt-hope,
    not enforcement. Removing the tool (and the OpenAI-style
    ``web_search_options``) makes it deterministic for the tool-calling path.

    NOTE: web results that LibreChat injects as plain message *content* (not as
    a tool the model calls) are NOT removed here — that is a LibreChat-side
    concern and must be gated at the frontend (do not offer Web while Strict is
    selected). See ``custom_router`` for the content-injection signal.

    Returns the number of web affordances removed (0 when none were present).
    """
    removed = 0

    tools = data.get("tools")
    if isinstance(tools, list):
        kept = [tool for tool in tools if not is_web_search_tool(tool)]
        if len(kept) != len(tools):
            removed += len(tools) - len(kept)
            if kept:
                data["tools"] = kept
            else:
                # An empty ``tools`` list is rejected by some providers; drop
                # the key entirely so no tools are advertised.
                data.pop("tools", None)

    if isinstance(data.get("web_search_options"), dict):
        data.pop("web_search_options", None)
        removed += 1

    return removed


def general_runtime_capabilities_block(data: dict[str, Any]) -> str:
    if not request_has_web_search(data):
        return ""
    return (
        "[Klai Runtime Capabilities]\n"
        "Knowledge Base: none selected.\n"
        "Web Search: available for this turn.\n"
        "Instruction: for questions that need a live lookup, use the available "
        "Web Search tool or provided web results now. Do NOT tell the user to "
        "enable Search unless the tool call fails or no search result is returned.\n"
        "[End Klai Runtime Capabilities]"
    )


def last_user_message(messages: list[dict]) -> str | None:
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return " ".join(
                    p.get("text", "") for p in content if p.get("type") == "text"
                )
    return None


def strip_klai_backend_footer_from_text(text: str) -> str:
    """Remove Klai-managed citation/provenance footer from assistant history."""
    without_marker = KLAI_SOURCES_METADATA_MARKER_RE.sub("\n", text)
    matches = list(KLAI_BACKEND_FOOTER_HEADING_RE.finditer(without_marker))
    first_activity_index = next(
        (
            index
            for index, match in enumerate(matches)
            if match.group(1).lower() in _FOOTER_ACTIVITY_HEADINGS
        ),
        None,
    )
    if first_activity_index is None:
        return without_marker.rstrip() if without_marker != text else text

    cut_match = matches[first_activity_index]
    for match in reversed(matches[:first_activity_index]):
        if match.group(1).lower() in _FOOTER_SOURCES_HEADINGS:
            cut_match = match
            break
    return without_marker[: cut_match.start()].rstrip()


def strip_klai_backend_footer_from_content(content: object) -> object:
    if isinstance(content, str):
        return strip_klai_backend_footer_from_text(content)
    if isinstance(content, list):
        changed = False
        stripped_parts: list[object] = []
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                stripped_text = strip_klai_backend_footer_from_text(part["text"])
                if stripped_text != part["text"]:
                    changed = True
                    part = {**part, "text": stripped_text}
            stripped_parts.append(part)
        return stripped_parts if changed else content
    return content


def sanitize_assistant_history_messages(messages: object) -> object:
    """Strip backend-only footers from assistant messages before model input."""
    if not isinstance(messages, list):
        return messages
    sanitized: list[object] = []
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "assistant":
            sanitized.append(message)
            continue
        content = message.get("content")
        stripped_content = strip_klai_backend_footer_from_content(content)
        if stripped_content == content:
            sanitized.append(message)
        else:
            sanitized.append({**message, "content": stripped_content})
    return sanitized


def active_tool_result_contexts(messages: object) -> list[str]:
    if not isinstance(messages, list):
        return []
    contexts: list[str] = []
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "tool":
            continue
        content = message.get("content")
        if isinstance(content, str):
            contexts.append(content)
    return contexts


def build_conversation_history(messages: list[dict]) -> list[dict]:
    """Return up to the last 6 turns, excluding the current user message."""
    history: list[dict] = []
    for message in messages[:-1]:
        if message.get("role") not in ("user", "assistant"):
            continue
        content = message.get("content")
        if not isinstance(content, str):
            continue
        if message.get("role") == "assistant":
            content = strip_klai_backend_footer_from_text(content)
        history.append(
            {
                "role": message["role"],
                "content": clip_retrieval_history_content(content),
            }
        )
    return history[-6:]


def clip_retrieval_history_content(content: str) -> str:
    max_chars = RETRIEVE_HISTORY_MAX_CONTENT_CHARS
    if max_chars <= 0 or len(content) <= max_chars:
        return content

    marker = RETRIEVE_HISTORY_OMISSION_MARKER
    if max_chars <= len(marker) + 20:
        return content[:max_chars]

    remaining = max_chars - len(marker)
    head_chars = remaining // 2
    tail_chars = remaining - head_chars
    return content[:head_chars].rstrip() + marker + content[-tail_chars:].lstrip()
