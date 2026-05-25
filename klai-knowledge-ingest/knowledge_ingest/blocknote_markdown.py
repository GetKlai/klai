"""BlockNote JSON to Markdown rendering for knowledge indexing.

Docs pages are stored in Gitea as BlockNote JSON so the editor can round-trip
its native schema. Retrieval needs readable, mostly-Markdown text instead.
This module intentionally supports the block and inline shapes we index today
and degrades unknown blocks to their rendered children.
"""

from __future__ import annotations

import json
import re
from typing import Any

JsonObject = dict[str, Any]

_LIST_BLOCK_TYPES = {"bulletListItem", "numberedListItem", "checkListItem"}
_KNOWN_BLOCK_TYPES = {
    "paragraph",
    "heading",
    *_LIST_BLOCK_TYPES,
    "codeBlock",
    "quote",
    "table",
    "image",
    "file",
}


def blocknote_json_to_markdown(body: str) -> str | None:
    """Return Markdown text if ``body`` is a BlockNote JSON array."""
    trimmed = body.strip()
    if not trimmed.startswith("["):
        return None
    try:
        blocks = json.loads(trimmed)
    except json.JSONDecodeError:
        return None
    if not isinstance(blocks, list):
        return None
    if not blocks:
        return ""
    if not all(isinstance(block, dict) and _looks_like_blocknote_block(block) for block in blocks):
        return None
    return _render_blocks(blocks).strip()


def _looks_like_blocknote_block(block: JsonObject) -> bool:
    block_type = block.get("type")
    content = block.get("content")
    children = block.get("children")
    props = block.get("props")

    if block_type is not None and not isinstance(block_type, str):
        return False
    if children is not None and not isinstance(children, list):
        return False
    if props is not None and not isinstance(props, dict):
        return False
    if block_type in _KNOWN_BLOCK_TYPES:
        return True
    return isinstance(content, (str, list, dict)) or isinstance(children, list)


def _render_blocks(blocks: list[JsonObject], depth: int = 0) -> str:
    rendered: list[str] = []
    index = 0
    while index < len(blocks):
        block = blocks[index]
        block_type = block.get("type")
        if block_type in _LIST_BLOCK_TYPES:
            items: list[str] = []
            number = 1
            while index < len(blocks) and blocks[index].get("type") == block_type:
                items.append(_render_list_item(blocks[index], depth, number))
                number += 1
                index += 1
            rendered.append("\n".join(item for item in items if item))
            continue

        block_text = _render_block(block, depth)
        if block_text:
            rendered.append(block_text)
        index += 1
    return "\n\n".join(rendered)


def _render_block(block: JsonObject, depth: int) -> str:
    block_type = block.get("type")
    content = block.get("content")
    text = "" if _is_table_content(content) else _render_inline_content(content)
    rendered_children = _render_children(block, depth)

    if block_type == "heading":
        level = _heading_level(block.get("props"))
        primary = f"{'#' * level} {text}".strip()
    elif block_type == "codeBlock":
        language = _code_language(block.get("props"))
        primary = f"```{language}\n{_extract_inline_plain_text(content)}\n```"
    elif block_type == "quote":
        primary = "\n".join(f"> {line}" for line in text.splitlines())
    elif block_type == "table" and _is_table_content(content):
        primary = _render_table(content)
    else:
        primary = text

    if primary and rendered_children:
        return f"{primary}\n\n{rendered_children}"
    return primary or rendered_children


def _render_children(block: JsonObject, depth: int) -> str:
    children = block.get("children")
    if isinstance(children, list) and all(isinstance(child, dict) for child in children):
        return _render_blocks(children, depth + 1)
    return ""


def _heading_level(props: object) -> int:
    if not isinstance(props, dict):
        return 2
    level = props.get("level")
    return max(1, min(level, 6)) if isinstance(level, int) else 2


def _code_language(props: object) -> str:
    if not isinstance(props, dict):
        return ""
    language = props.get("language")
    return language if isinstance(language, str) else ""


def _render_list_item(block: JsonObject, depth: int, number: int) -> str:
    block_type = block.get("type")
    indent = "  " * depth
    text = _render_inline_content(block.get("content"))
    if block_type == "numberedListItem":
        marker = f"{number}."
    elif block_type == "checkListItem":
        props = block.get("props") if isinstance(block.get("props"), dict) else {}
        marker = "- [x]" if props.get("checked") else "- [ ]"
    else:
        marker = "-"
    line = f"{indent}{marker} {text}".rstrip()
    rendered_children = _render_children(block, depth)
    if rendered_children:
        return f"{line}\n{rendered_children}"
    return line


def _render_inline_content(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "".join(_render_inline_item(item) for item in content)


def _render_inline_item(item: object) -> str:
    if isinstance(item, str):
        return item
    if not isinstance(item, dict):
        return ""

    item_type = item.get("type")
    if item_type == "text":
        return _apply_inline_styles(str(item.get("text") or ""), item.get("styles"))
    if item_type == "link":
        label = _render_inline_content(item.get("content"))
        href = _link_href(item)
        return f"[{label}]({href})" if href and label else label
    if item_type == "wikilink":
        props = item.get("props") if isinstance(item.get("props"), dict) else {}
        return str(props.get("title") or props.get("pageId") or "")
    return ""


def _link_href(item: JsonObject) -> str | None:
    href = item.get("href")
    if isinstance(href, str) and href.strip():
        return href.strip()
    props = item.get("props")
    if isinstance(props, dict):
        url = props.get("url")
        if isinstance(url, str) and url.strip():
            return url.strip()
    return None


def _apply_inline_styles(text: str, styles: object) -> str:
    if not text or not isinstance(styles, dict):
        return text
    leading = re.match(r"^\s*", text).group(0)
    trailing = re.search(r"\s*$", text).group(0)
    core = text[len(leading) : len(text) - len(trailing) if trailing else len(text)]
    if not core:
        return text
    if styles.get("code"):
        escaped_core = core.replace("`", "\\`")
        core = f"`{escaped_core}`"
    if styles.get("bold"):
        core = f"**{core}**"
    if styles.get("italic"):
        core = f"_{core}_"
    if styles.get("strike"):
        core = f"~~{core}~~"
    return f"{leading}{core}{trailing}"


def _extract_inline_plain_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict):
            if item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
            elif item.get("type") == "link":
                parts.append(_extract_inline_plain_text(item.get("content")))
            elif item.get("type") == "wikilink":
                props = item.get("props") if isinstance(item.get("props"), dict) else {}
                parts.append(str(props.get("title") or ""))
    return "".join(parts)


def _is_table_content(content: object) -> bool:
    return (
        isinstance(content, dict)
        and content.get("type") == "tableContent"
        and isinstance(content.get("rows"), list)
    )


def _table_cell_content(cell: object) -> object:
    if isinstance(cell, list):
        return cell
    if isinstance(cell, dict):
        return cell.get("content")
    return None


def _render_table(content: JsonObject) -> str:
    rows = content.get("rows")
    if not isinstance(rows, list) or not rows:
        return ""
    parsed_rows: list[list[str]] = []
    width = 0
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("cells"), list):
            continue
        cells = [
            _render_inline_content(_table_cell_content(cell)).replace("\n", " ").strip()
            for cell in row["cells"]
        ]
        parsed_rows.append(cells)
        width = max(width, len(cells))
    if not parsed_rows or width == 0:
        return ""
    normalized = [row + [""] * (width - len(row)) for row in parsed_rows]
    lines = [f"| {' | '.join(cell or ' ' for cell in normalized[0])} |"]
    lines.append(f"|{' --- |' * width}")
    lines.extend(f"| {' | '.join(cell or ' ' for cell in row)} |" for row in normalized[1:])
    return "\n".join(lines)
