# Serena Integration for Development

Serena MCP provides semantic code tools (symbol navigation, references, symbol-level editing) and persistent project memories. Use it as the primary tool for understanding code structure.

## Session Start

At the beginning of each development session:
1. Activate the project: `mcp__serena__activate_project(project="klai")`
2. Read relevant memories based on the task domain (not all memories every time):
   - Architecture questions: `architecture-overview`
   - Backend work: `backend-patterns`, `domain-model`
   - Frontend/website work: `website-patterns`, `frontend-standards`
   - Infrastructure/deployment: `deployment-context`

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
- New patterns discovered: update `backend-patterns` or `website-patterns`
- Architecture changes: update `architecture-overview`
- New domain concepts: update `domain-model`

Memories persist between sessions and across `/clear` commands -- they are the long-term knowledge store.
