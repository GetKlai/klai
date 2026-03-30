# Context7 MCP Usage

When to use context7 and how.

## When to Use

Use context7 whenever working with external libraries or frameworks:
- Implementing integrations with a library (React, Next.js, FastAPI, LiteLLM, etc.)
- Plan Phase research when the SPEC involves external dependencies
- Any time official API docs are needed — training data may be outdated
- Prefer context7 over WebSearch for library documentation

Do NOT use context7 for internal code, business logic, or project-specific patterns.

## How to Use

MCP tools are deferred — ToolSearch must be called first:

1. `ToolSearch("context7")` — loads `mcp__context7__resolve-library-id` and `mcp__context7__get-library-docs`
2. `resolve-library-id` with `libraryName` — returns the Context7 library ID
3. `get-library-docs` with the ID and a `topic` — returns current documentation

Example: looking up Next.js App Router file conventions
- resolve-library-id: `libraryName: "Next.js"` → `/vercel/next.js`
- get-library-docs: `context7CompatibleLibraryID: "/vercel/next.js"`, `topic: "App Router file conventions"`

## During Plan Phase Research

If the SPEC involves external libraries, run context7 during the Research sub-phase:
- Retrieve current API docs before writing acceptance criteria
- Include library version constraints and relevant API surface in `research.md`
