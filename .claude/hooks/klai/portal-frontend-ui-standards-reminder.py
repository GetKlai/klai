#!/usr/bin/env python3
"""Remind agents about the canonical portal UI standards on frontend edits."""

import json
import os
import sys
from pathlib import Path


PORTAL_PREFIX = "klai-portal/frontend/"
LIBRECHAT_CLIENT_UI_FILES = {"deploy/librechat/klai-entrypoint.sh"}
STANDARDS_DOC = "klai-portal/frontend/docs/ui-standards.md"
UI_EXTENSIONS = {".ts", ".tsx", ".css", ".json", ".md"}


def _as_relative(path_value: str) -> str:
    path = Path(path_value)
    if not path.is_absolute():
        return path.as_posix().lstrip("./")

    project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if project_dir:
        try:
            return path.relative_to(project_dir).as_posix()
        except ValueError:
            pass
    return path.as_posix()


def _candidate_paths(payload: dict) -> list[str]:
    tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
    paths: list[str] = []
    for key in ("file_path", "path"):
        value = tool_input.get(key)
        if isinstance(value, str):
            paths.append(_as_relative(value))
    edits = tool_input.get("edits")
    if isinstance(edits, list):
        for edit in edits:
            if isinstance(edit, dict):
                value = edit.get("file_path") or edit.get("path")
                if isinstance(value, str):
                    paths.append(_as_relative(value))
    return paths


def _is_portal_frontend_edit(path: str) -> bool:
    if not path.startswith(PORTAL_PREFIX):
        return False
    suffix = Path(path).suffix
    return suffix in UI_EXTENSIONS


def _is_librechat_client_ui_edit(path: str) -> bool:
    return path in LIBRECHAT_CLIENT_UI_FILES


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0

    paths = _candidate_paths(payload)
    is_portal_ui = any(_is_portal_frontend_edit(path) for path in paths)
    is_librechat_ui = any(_is_librechat_client_ui_edit(path) for path in paths)
    if not is_portal_ui and not is_librechat_ui:
        return 0

    message = (
        "Portal/LibreChat UI edit detected. Before changing UI, read "
        f"`{STANDARDS_DOC}`, name the existing portal reference screen you are "
        "following, and keep portal patterns separate from website/marketing "
        "patterns. For LibreChat provenance UI, follow the Chat Disclosure Rows "
        "pattern and keep it close to LibreChat's native message metadata."
    )
    print(json.dumps({"systemMessage": message}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
