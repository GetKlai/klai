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

In the workspace `.mcp.json`, the Serena entry must be:

```json
{
  "serena": {
    "command": "serena",
    "args": ["start-mcp-server", "--project-from-cwd"]
  }
}
```

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
5. Fall back to Read/Grep only for non-code files (markdown, yaml, config)

This is more token-efficient than reading entire files with the Read tool.

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
