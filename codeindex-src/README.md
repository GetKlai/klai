# CodeIndex

Graph-powered code intelligence for AI agents. Index any codebase into a knowledge graph, then query relationships, execution flows, and symbol context via CLI or MCP.

Fork of [CodeIndex](https://github.com/abhigyanpatwari/GitNexus) with centralized storage and git worktree support.

## What it does

CodeIndex analyzes your codebase and builds a knowledge graph (KuzuDB) containing:

- **Symbols**: functions, classes, methods, interfaces
- **Relationships**: who calls what, imports, inheritance
- **Execution flows**: multi-step processes through your code
- **Communities**: clusters of related symbols

This graph is then available to AI agents (Claude Code, Cursor, etc.) via:
- **MCP server** with query, context, and impact tools
- **Claude Code hook** that enriches Grep/Glob/Bash with graph context automatically
- **CLI** for direct queries and management

## Key features (fork additions)

- **Centralized storage**: databases stored under `~/.codeindex/{ProjectName}/` instead of in-repo
- **Git worktree support**: all worktrees of a repo share the same index automatically
- **Named projects**: `codeindex analyze ParrotKey ~/repos/myapp`
- **Auto-detect updates**: run `codeindex analyze` from any worktree to refresh
- **Rename support**: `codeindex rename OldName NewName`

## Prerequisites

- Node.js 20+ (tested with v24)
- Git
- npm

## Installation

```bash
# Clone this repo
git clone <repo-url> ~/Development/codeindex
cd ~/Development/codeindex/codeindex

# Install dependencies
npm install --ignore-scripts

# Build
npx tsc

# Install globally
npm install -g .
```

After installation, the `codeindex` command is available system-wide.

## Quick start

### 1. Index a repository

First time - provide a project name and path:

```bash
codeindex analyze ParrotKey ~/conductor/repos/yobert-v1
```

This creates the knowledge graph at `~/.codeindex/ParrotKey/`.

### 2. Update the index

From anywhere inside the repo or any of its worktrees:

```bash
codeindex analyze
```

CodeIndex auto-detects the project via git worktree resolution and only re-indexes when the HEAD commit has changed. Use `--force` to force a full re-index.

### 3. Query the graph

```bash
# Search for execution flows
codeindex query "authentication flow"

# Get full context of a symbol
codeindex context handleLogin

# Blast radius analysis
codeindex impact UserService --direction upstream
```

## CLI commands

| Command | Description |
|---|---|
| `codeindex analyze [name] [path]` | Index a repo. Omit args to auto-detect from worktree |
| `codeindex analyze --force` | Force full re-index |
| `codeindex analyze --embeddings` | Include semantic embeddings |
| `codeindex list` | Show all indexed projects |
| `codeindex status` | Show index status for current repo |
| `codeindex rename <old> <new>` | Rename a project |
| `codeindex clean` | Remove index for current repo |
| `codeindex clean --all --force` | Remove all indexes |
| `codeindex query <search>` | Search execution flows |
| `codeindex context <symbol>` | 360-degree view of a symbol |
| `codeindex impact <symbol>` | Blast radius analysis |
| `codeindex cypher <query>` | Raw Cypher query |
| `codeindex mcp` | Start MCP server (stdio) |
| `codeindex serve` | Start HTTP server for web UI |
| `codeindex wiki` | Generate documentation from graph |
| `codeindex setup` | Configure MCP for Cursor/Claude Code |

## MCP server setup

### Claude Code

Add to `~/.claude/settings.json` under `mcpServers`:

```json
{
  "mcpServers": {
    "codeindex": {
      "command": "codeindex",
      "args": ["mcp"]
    }
  }
}
```

### Claude Code hook (automatic context enrichment)

The hook intercepts Grep/Glob/Bash calls and enriches them with graph context. Add to `~/.claude/settings.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Grep|Glob|Bash",
        "hooks": [
          {
            "type": "command",
            "command": "node \"<path-to-codeindex>/codeindex/hooks/claude/codeindex-hook.cjs\"",
            "timeout": 8000,
            "statusMessage": "Enriching with CodeIndex graph context..."
          }
        ]
      }
    ]
  }
}
```

### Cursor

```json
{
  "mcpServers": {
    "codeindex": {
      "command": "codeindex",
      "args": ["mcp"]
    }
  }
}
```

## Storage layout

```
~/.codeindex/
  registry.json          # Global project registry
  config.json            # CLI config (API keys for wiki generation)
  ParrotKey/             # Per-project storage
    meta.json            # Index metadata
    kuzu/                # KuzuDB graph database
```

## Git worktree support

CodeIndex resolves worktrees to their main repository automatically using `git rev-parse --git-common-dir`. This means:

- Index the main repo once: `codeindex analyze MyProject ~/repos/main`
- All worktrees of that repo share the same index
- Run `codeindex analyze` from any worktree to update
- The hook enriches searches from any worktree

## Rebuilding after code changes

If you modify the CodeIndex source code:

```bash
cd ~/Development/codeindex/codeindex
npx tsc                  # Rebuild TypeScript
npm install -g .         # Reinstall globally
```

The hook and MCP server will automatically use the new version on next invocation.

## Merging upstream changes

This is a fork of CodeIndex. To merge upstream improvements:

```bash
cd ~/Development/codeindex
git remote add upstream https://github.com/abhigyanpatwari/CodeIndex.git
git fetch upstream
git merge upstream/main
# Resolve any conflicts, rebuild, reinstall
```

## License

PolyForm Noncommercial 1.0.0 (inherited from CodeIndex)
