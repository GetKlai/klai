#!/usr/bin/env python3
"""Reject literal bare .env references in Compose env_file declarations."""

from __future__ import annotations

import re
import sys
from pathlib import Path


ENV_FILE_KEY = re.compile(r"^(?:env_file|'env_file'|\"env_file\")\s*:\s*(.*)$")
PATH_KEY = re.compile(r"^(?:path|'path'|\"path\")\s*:\s*(.*)$")


def strip_yaml_comment(value: str) -> str:
    """Strip a YAML comment while preserving # inside quoted values."""
    quote: str | None = None
    escaped = False
    result: list[str] = []
    index = 0

    while index < len(value):
        char = value[index]
        if quote == '"':
            result.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif quote == "'":
            result.append(char)
            if char == quote:
                if index + 1 < len(value) and value[index + 1] == quote:
                    result.append(value[index + 1])
                    index += 1
                else:
                    quote = None
        elif char in {"'", '"'}:
            quote = char
            result.append(char)
        elif char == "#":
            break
        else:
            result.append(char)
        index += 1

    return "".join(result).rstrip()


def unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def split_flow_sequence(value: str) -> list[str]:
    """Split a simple YAML flow sequence without splitting quoted commas."""
    items: list[str] = []
    current: list[str] = []
    quote: str | None = None
    escaped = False
    nested_depth = 0

    for char in value:
        if quote == '"':
            current.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif quote == "'":
            current.append(char)
            if char == quote:
                quote = None
        elif char in {"'", '"'}:
            quote = char
            current.append(char)
        elif char in "[{":
            nested_depth += 1
            current.append(char)
        elif char in "]}":
            nested_depth = max(0, nested_depth - 1)
            current.append(char)
        elif char == "," and nested_depth == 0:
            items.append("".join(current).strip())
            current = []
        else:
            current.append(char)

    items.append("".join(current).strip())
    return items


def is_bare_env_value(value: str) -> bool:
    value = strip_yaml_comment(value).strip().rstrip(",").strip()
    value = value.removeprefix("[").removesuffix("]").strip()
    if not value:
        return False

    if value.startswith("{"):
        mapping = value[1:].removesuffix("}")
        return any(is_bare_env_value(field) for field in split_flow_sequence(mapping))

    path_match = PATH_KEY.match(value)
    if path_match:
        value = path_match.group(1).strip()

    return unquote(value) == ".env"


def contains_bare_env(value: str) -> bool:
    value = strip_yaml_comment(value).strip()
    if value.startswith("[") and value.endswith("]"):
        return any(is_bare_env_value(item) for item in split_flow_sequence(value[1:-1]))
    return is_bare_env_value(value)


def find_violations(path: Path) -> list[int]:
    violations: list[int] = []
    block_indent: int | None = None
    flow_sequence = False

    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        logical = strip_yaml_comment(raw_line.rstrip())
        if not logical:
            continue

        content = logical.lstrip(" ")
        indent = len(logical) - len(content)

        if block_indent is not None:
            if flow_sequence:
                if contains_bare_env(content):
                    violations.append(line_number)
                if "]" in content:
                    block_indent = None
                    flow_sequence = False
                continue

            if indent > block_indent:
                item = content[1:].strip() if content.startswith("-") else content
                if contains_bare_env(item):
                    violations.append(line_number)
                continue

            block_indent = None

        key_match = ENV_FILE_KEY.match(content)
        if not key_match:
            continue

        value = key_match.group(1).strip()
        if not value:
            block_indent = indent
        elif value.startswith("[") and "]" not in value:
            block_indent = indent
            flow_sequence = True
            if contains_bare_env(value[1:]):
                violations.append(line_number)
        elif contains_bare_env(value):
            violations.append(line_number)

    return violations


def main(argv: list[str]) -> int:
    paths = [Path(value) for value in argv] or [
        Path("deploy/docker-compose.yml"),
        Path("deploy/docker-compose.gpu.yml"),
    ]
    failed = False

    for path in paths:
        if not path.is_file():
            continue
        for line_number in find_violations(path):
            failed = True
            print(
                f"ERROR: {path}:{line_number}: bare .env in env_file is forbidden; "
                "use explicit environment entries or a per-service env file.",
                file=sys.stderr,
            )

    if failed:
        return 1

    print("env-scope-guard: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
