#!/usr/bin/env python3
"""Patch the host-mounted getklai LibreChat config in place.

This intentionally does not replace the whole file: the getklai tenant has
host-local model specs and labels. The canary only needs to pin the v0.8.6
config schema version and set an explicit non-default agent capability
allowlist so Skills/Subagents/Code cannot fall back to upstream
defaults.
"""

from __future__ import annotations

import sys
from pathlib import Path


CONFIG_VERSION = "1.3.12"
CAPABILITY_BLOCK = [
    "  agents:\n",
    "    capabilities:\n",
    "      - 'deferred_tools'\n",
    "      - 'web_search'\n",
    "      - 'artifacts'\n",
    "      - 'ocr'\n",
    "      - 'tools'\n",
]


def _is_top_level(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped) and not line.startswith((" ", "\t")) and not stripped.startswith("#")


def _find_top_level_block(lines: list[str], key: str) -> tuple[int, int] | None:
    start = next((idx for idx, line in enumerate(lines) if line == f"{key}:\n"), None)
    if start is None:
        return None
    end = len(lines)
    for idx in range(start + 1, len(lines)):
        if _is_top_level(lines[idx]):
            end = idx
            break
    return start, end


def _find_child_block(lines: list[str], start: int, end: int, key: str) -> tuple[int, int] | None:
    child_start = next(
        (idx for idx in range(start + 1, end) if lines[idx] == f"  {key}:\n"),
        None,
    )
    if child_start is None:
        return None
    child_end = end
    for idx in range(child_start + 1, end):
        line = lines[idx]
        if line.startswith("  ") and not line.startswith("    ") and line.strip():
            child_end = idx
            break
    return child_start, child_end


def patch_config(text: str) -> str:
    lines = text.splitlines(keepends=True)
    if not lines:
        raise ValueError("LibreChat config is empty")

    if lines[0].startswith("version:"):
        lines[0] = f"version: {CONFIG_VERSION}\n"
    else:
        lines.insert(0, f"version: {CONFIG_VERSION}\n\n")

    endpoints = _find_top_level_block(lines, "endpoints")
    if endpoints is None:
        lines.extend(["\n", "endpoints:\n", *CAPABILITY_BLOCK])
        return "".join(lines)

    endpoints_start, endpoints_end = endpoints
    agents = _find_child_block(lines, endpoints_start, endpoints_end, "agents")
    if agents is None:
        insert_at = endpoints_start + 1
        lines[insert_at:insert_at] = CAPABILITY_BLOCK
        return "".join(lines)

    agents_start, agents_end = agents
    lines[agents_start:agents_end] = CAPABILITY_BLOCK
    return "".join(lines)


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {Path(sys.argv[0]).name} /path/to/librechat.yaml", file=sys.stderr)
        return 2

    path = Path(sys.argv[1])
    original = path.read_text(encoding="utf-8")
    patched = patch_config(original)
    if patched == original:
        print("unchanged")
        return 0

    path.write_text(patched, encoding="utf-8")
    print("changed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
