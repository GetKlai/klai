# Serena Integration for Development

Serena MCP provides semantic code tools (symbol navigation, references, symbol-level editing) and persistent project memories. Use it as the primary tool for understanding code structure.

## New machine setup

### 1. Install Serena

```bash
uv tool install git+https://github.com/oraios/serena
```

Do NOT use `uvx --from git+...` in `.mcp.json` — that clones and rebuilds Serena on every Claude
Code startup, which exceeds the MCP timeout and causes Serena to fail silently.

### 2. Configure MCP

The MCP config lives at `/Users/mark/Server/projects/.mcp.json` (one level above the klai repo —
not committed to git). This file is shared across all projects in the workspace.

**Required content** (restore this if the file is missing or Serena stops working):

```json
{
  "mcpServers": {
    "playwright": {
      "type": "stdio",
      "command": "npx",
      "args": [
        "@playwright/mcp@latest",
        "--executable-path",
        "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
        "--user-data-dir",
        "/Users/mark/Library/Caches/ms-playwright/klai-profile"
      ],
      "env": {}
    },
    "serena": {
      "type": "stdio",
      "command": "serena",
      "args": ["start-mcp-server", "--project-from-cwd"],
      "env": {}
    },
    "context7": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@upstash/context7-mcp"],
      "env": {}
    }
  }
}
```

**Common failure mode:** If Serena stops loading, check that the `command` is `"serena"` (not
`"uvx"` with `--from git+...`). The uvx variant clones and rebuilds on every startup → MCP
timeout → Serena never available.

### 3. Disable dashboard auto-open

By default Serena opens a browser tab every time Claude Code starts. Disable this in
`~/.serena/serena_config.yml`:

```yaml
web_dashboard_open_on_launch: false
```

The dashboard remains available at `http://localhost:24282/dashboard/` when needed.

### 4. Restore memories and project config

Serena memories and `project.yml` are stored in the workspace at `.serena/` (not committed to git).
After a fresh clone, activate the project and Serena will initialise a fresh memory store.

## Session Start

**Note:** With `--project-from-cwd` in `.mcp.json`, Serena auto-activates the project at MCP startup — `activate_project` is NOT needed and will fail if called. Skip straight to reading memories.

At the beginning of each development session:
1. Read relevant memories based on the task domain (not all memories every time):
   - Architecture questions: `architecture-overview`
   - Backend work: `backend-patterns`, `domain-model`
   - Frontend/website work: `frontend-standards`
   - Services/connectors: `services-overview`
   - Infrastructure/deployment: `deployment-context`
   - Claude assets: `claude-assets`

## Code Exploration (prefer Serena over Read)

When understanding code structure or relationships:
1. Use `get_symbols_overview` to see what a file contains (classes, functions, routes)
2. Use `find_symbol` with `include_body=False` to locate symbols across the codebase
3. Use `find_referencing_symbols` to understand who calls/uses a symbol
4. Only read full symbol bodies (`include_body=True`) when you need implementation details
5. Use `search_for_pattern` for free-text search — parameter is `substring_pattern` (not `pattern`)
6. Fall back to Read/Grep only for non-code files (markdown, yaml, config)

This is more token-efficient than reading entire files with the Read tool.

## search_for_pattern — Scoping Rules (HARD)

**[HARD] Never call `search_for_pattern` without at least one scoping parameter.**

Unscoped searches on the klai monorepo return thousands of matches across specs, docs, memories,
and reports — Serena truncates the result to a useless file:line listing and the search is wasted.

**Always apply at least one of these filters:**

| Parameter | When to use | Example |
|---|---|---|
| `relative_path` | You know the directory/file | `"klai-portal/backend/app"` |
| `paths_include_glob` | You know the file type | `"**/*.py"`, `"**/*.{ts,tsx}"` |
| `paths_exclude_glob` | Exclude noise directories | `"**/.moai/**"` |
| `restrict_search_to_code_files` | Only looking for code symbols | `true` |

**Decision tree:**

1. Looking for code in a specific service? → `relative_path` to that service dir
2. Looking for code across the whole repo? → `restrict_search_to_code_files: true` + `paths_include_glob` for the language
3. Looking for text in config/docs? → `relative_path` to the relevant doc dir, or `paths_include_glob: "**/*.md"`
4. Broad exploration across the whole repo? → Use Grep (Claude's native tool) instead — it handles large result sets better

**Anti-patterns (NEVER do these):**

```
# BAD — no scoping, matches everything
search_for_pattern(substring_pattern="portal")

# BAD — pattern too generic, no path restriction
search_for_pattern(substring_pattern="docker")
```

**Correct patterns:**

```
# GOOD — scoped to backend code
search_for_pattern(substring_pattern="def provision_tenant", relative_path="klai-portal/backend")

# GOOD — scoped to Python files, code only
search_for_pattern(substring_pattern="class PortalUser", restrict_search_to_code_files=True, paths_include_glob="**/*.py")

# GOOD — looking in specific directory with context
search_for_pattern(substring_pattern="REDIS_URL", relative_path="deploy", context_lines_after=3)
```

**If a search returns "The answer is too long":** Do NOT raise `max_answer_chars`. Instead:
1. Add `relative_path` to narrow the directory
2. Add `paths_include_glob` to filter file types
3. Make the `substring_pattern` more specific
4. Add `restrict_search_to_code_files: true` if you only need code

**Searching in ignored directories (specs, workflow, docs):**

The project's `ignored_paths` in `.serena/project.yml` excludes `.moai/specs/`, `.workflow/`,
`claude-docs/specs/`, and `claude-docs/gtm/` from Serena searches to reduce noise.

When you DO need to search these directories:
- Use Claude's native **Grep** tool — it is not affected by Serena's ignored_paths
- Use Serena's `read_file` tool to read specific files in ignored dirs (always works)
- Do NOT remove entries from `ignored_paths` — the noise reduction is worth the workaround

## Before Editing Code

Before modifying any function, class, or method:
1. Use `find_referencing_symbols` to check what depends on the code you are changing
2. Ensure changes are backward-compatible, or update all callers
3. For symbol-level replacements, prefer `replace_symbol_body` over Edit when replacing an entire function/method/class

## When Delegating to MoAI Subagents

MoAI subagents (Task tool spawns) cannot access Serena tools. When delegating:
1. Use Serena first to gather relevant context (symbol signatures, file structure, reference chains)
2. Include that context in the subagent's prompt so it does not need to rediscover it
3. This saves tokens: one Serena call vs. the subagent reading multiple files

## Memory Management

After completing significant work, write or update relevant Serena memories:
- New patterns discovered: update `backend-patterns` or `frontend-standards`
- Architecture changes: update `architecture-overview`
- New domain concepts: update `domain-model`
- New services: update `services-overview`

Memories persist between sessions and across `/clear` commands — they are the long-term knowledge store.
