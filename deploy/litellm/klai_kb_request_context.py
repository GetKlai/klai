"""Request and message-context helpers shared by the PII hooks."""

from __future__ import annotations

from typing import Any


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
