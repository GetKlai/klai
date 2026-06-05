---
name: codeindex-cli
description: "Use only when the user explicitly asks to run CodeIndex CLI commands like analyze/index a repo, check status, clean the index, generate a wiki, or list indexed repos. Do not use this for normal code navigation, stale-index warnings, or missing MCP tools. Examples: \"Index this repo\", \"Reanalyze the codebase\", \"Generate a wiki\""
---

# CodeIndex CLI Commands

Use these commands only for explicit index/setup/maintenance requests. For normal agent work, use the MCP tools (`query`, `context`, `impact`) and verify stale results against source files.

## Commands

### analyze — Build or refresh the index

```bash
codeindex analyze
```

Run from the project root. This parses all source files, builds the knowledge graph, writes it to `~/.codeindex/<ProjectName>/` (global cache — NEVER inside the working copy), and generates CLAUDE.md / AGENTS.md context files in the project root.

| Flag           | Effect                                                           |
| -------------- | ---------------------------------------------------------------- |
| `--force`      | Force full re-index even if up to date                           |
| `--embeddings` | Enable embedding generation for semantic search (off by default) |

**When to run:** First time in a project or when the user explicitly asks to
rebuild the index. Do not run this automatically just because
`codeindex://repo/{name}/context` reports stale data.

For Klai in Conductor, do **not** run ad hoc analyze/update commands from a
feature worktree. That warning can be caused by a worktree overlay or by the
registered checkout not being on main. Run `scripts/codeindex-health.sh` first;
only if it reports the shared base index is stale, run
`scripts/codeindex-health.sh --repair`.

### update — Refresh an existing index

```bash
codeindex update
```

Use this only when the user explicitly asks to refresh a stale index. For Klai
Conductor worktrees, prefer `scripts/codeindex-health.sh --repair` for the
shared main index.

### status — Check index freshness

```bash
codeindex status
```

Shows whether the current repo has a CodeIndex index, when it was last updated, and symbol/relationship counts. Use this to check if re-indexing is needed.

### clean — Delete the index

```bash
codeindex clean
```

Deletes the repo's entry under `~/.codeindex/<ProjectName>/` and unregisters it from the global registry. Use before re-indexing if the index is corrupt or after removing CodeIndex from a project. Never deletes anything inside the working copy.

> If you find a stray `.codeindex/` directory inside a project root it is leftover from an older CodeIndex version. Safe to delete — the current version never writes there.

| Flag      | Effect                                            |
| --------- | ------------------------------------------------- |
| `--force` | Skip confirmation prompt                          |
| `--all`   | Clean all indexed repos, not just the current one |

### wiki — Generate documentation from the graph

```bash
codeindex wiki
```

Generates repository documentation from the knowledge graph using an LLM. Requires an API key (saved to `~/.codeindex/config.json` on first use).

| Flag                | Effect                                    |
| ------------------- | ----------------------------------------- |
| `--force`           | Force full regeneration                   |
| `--model <model>`   | LLM model (default: minimax/minimax-m2.5) |
| `--base-url <url>`  | LLM API base URL                          |
| `--api-key <key>`   | LLM API key                               |
| `--concurrency <n>` | Parallel LLM calls (default: 3)           |
| `--gist`            | Publish wiki as a public GitHub Gist      |

### list — Show all indexed repos

```bash
codeindex list
```

Lists all repositories registered in `~/.codeindex/registry.json`. The MCP `list_repos` tool provides the same information.

## After Indexing

1. **Read `codeindex://repo/{name}/context`** to verify the index loaded
2. Use the other CodeIndex skills (`exploring`, `debugging`, `impact-analysis`, `refactoring`) for your task

## Troubleshooting

- **"Not inside a git repository"**: Run from a directory inside a git repo
- **Index is stale during a code task**: In Conductor, run `scripts/codeindex-health.sh`; if the shared main index is healthy, treat CodeIndex results as advisory and verify branch-local code with source files
- **Embeddings slow**: Omit `--embeddings` (it's off by default) or set `OPENAI_API_KEY` for faster API-based embedding
