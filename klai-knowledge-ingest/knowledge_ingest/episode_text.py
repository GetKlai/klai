import structlog

MAX_TEXT_CHARS = 30000

logger = structlog.get_logger()


def _split_with_fallback_boundaries(document_text: str, max_chars: int) -> list[str]:
    parts: list[str] = []
    remaining = document_text
    while remaining:
        if len(remaining) <= max_chars:
            parts.append(remaining)
            break

        split_at = remaining.rfind("\n\n", 1, max_chars + 1)
        if split_at < 1:
            split_at = remaining.rfind("\n", 1, max_chars + 1)
        if split_at < 1:
            split_at = next(
                (index + 1 for index in range(max_chars - 1, 0, -1) if remaining[index].isspace()),
                max_chars,
            )

        # A leading delimiter alone would recreate the empty-episode bug for
        # API input beginning with a blank line; keep it with following text.
        if not remaining[:split_at].strip():
            split_at = max_chars
        parts.append(remaining[:split_at])
        remaining = remaining[split_at:]
    return parts


def split_episode_text(document_text: str, *, max_chars: int = MAX_TEXT_CHARS) -> list[str]:
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    if not document_text:
        return []
    if len(document_text) <= max_chars:
        return [document_text]

    paragraphs = document_text.split("\n\n")
    oversized = next((paragraph for paragraph in paragraphs if len(paragraph) > max_chars), None)
    if oversized is not None or any(not paragraph for paragraph in paragraphs):
        # Markdown tables and transcripts can arrive from skip_chunking as one
        # 40k paragraph; failing here would exhaust all three task attempts and
        # leave the document with zero episodes.
        parts = _split_with_fallback_boundaries(document_text, max_chars)
        logger.warning(
            "episode_text_fallback_split",
            document_chars=len(document_text),
            max_chars=max_chars,
            part_count=len(parts),
        )
        return parts

    parts: list[str] = []
    current = paragraphs[0]
    for paragraph in paragraphs[1:]:
        candidate = f"{current}\n\n{paragraph}"
        if len(candidate) <= max_chars:
            current = candidate
        else:
            parts.append(current)
            current = paragraph
    parts.append(current)
    return parts
