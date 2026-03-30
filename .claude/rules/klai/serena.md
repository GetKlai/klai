# Serena Integration

> Semantic code tools (symbol navigation, references, symbol-level editing) and persistent project memories.
> Setup & installation: `docs/setup/mcp-servers.md`

## Session Start

With `--project-from-cwd` in `.mcp.json`, Serena auto-activates at startup — `activate_project` is NOT needed.

Read relevant memories based on the task domain (not all every time):
- Architecture: `architecture-overview`
- Backend: `backend-patterns`, `domain-model`
- Frontend/website: `frontend-standards`
- Services: `services-overview`
- Infrastructure: `deployment-context`
- Claude assets: `claude-assets`

## Code Exploration (prefer Serena over Read)

1. `get_symbols_overview` to see what a file contains
2. `find_symbol` with `include_body=False` to locate symbols
3. `find_referencing_symbols` to understand callers/users
4. Only `include_body=True` when you need implementation details
5. `search_for_pattern` for free-text search (parameter: `substring_pattern`)
6. Fall back to Read/Grep only for non-code files (markdown, yaml, config)

## search_for_pattern — Scoping Rules (HARD)

**[HARD] Never call `search_for_pattern` without at least one scoping parameter.**

Unscoped searches return thousands of matches — Serena truncates to useless output.

**Always apply at least one filter:**

| Parameter | When to use | Example |
|---|---|---|
| `relative_path` | You know the directory/file | `"klai-portal/backend/app"` |
| `paths_include_glob` | You know the file type | `"**/*.py"` |
| `paths_exclude_glob` | Exclude noise directories | `"**/.moai/**"` |
| `restrict_search_to_code_files` | Only code symbols | `true` |

**Decision tree:**
1. Code in a specific service? -> `relative_path` to that dir
2. Code across the whole repo? -> `restrict_search_to_code_files: true` + `paths_include_glob`
3. Text in config/docs? -> `relative_path` to the doc dir
4. Broad exploration? -> Use Grep instead (handles large result sets better)

**If "answer is too long":** Do NOT raise `max_answer_chars`. Narrow with `relative_path`, `paths_include_glob`, or a more specific pattern.

**Ignored directories:** `.moai/specs/`, `.workflow/`, `docs/specs/`, `docs/gtm/` are excluded from Serena searches. Use Grep or `read_file` for those.

## Before Editing Code

1. Use `find_referencing_symbols` to check what depends on the code you're changing
2. Ensure backward-compatibility, or update all callers
3. Prefer `replace_symbol_body` over Edit for whole function/method replacements

## Delegating to Subagents

MoAI subagents cannot access Serena tools. Use Serena first to gather context (signatures, structure, references), then include it in the subagent prompt.

## Memory Management

After significant work, update relevant memories:
- `backend-patterns`, `frontend-standards`, `architecture-overview`, `domain-model`, `services-overview`

Memories persist between sessions and across `/clear`.
