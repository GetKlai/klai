---
description: "First-time project setup: index codebase, build knowledge graph, and fill project memory"
argument-hint: "[project-name]"
---

# CodeIndex Setup — First-Time Project Init

You are running the first-time CodeIndex setup for this project. This indexes the codebase, builds a knowledge graph, and populates project memory from git history.

## What is CodeIndex?

CodeIndex analyzes your codebase and builds a knowledge graph of all symbols (functions, classes, modules), their relationships (calls, imports, extends), and execution flows. This graph is available via MCP tools (`query`, `context`, `impact`) so the LLM understands your codebase deeply — not just individual files, but how everything connects.

The `remember()` tool stores observations (decisions, patterns, architecture notes) that persist across sessions. Combined, every future conversation starts with full project context.

## Workflow

### Phase 1: Check Current State

1. Call `mcp__codeindex__list_repos` to check if this project is already indexed
2. Determine the project name: use the argument if provided, otherwise `git remote get-url origin 2>/dev/null | xargs basename | sed 's/\.git$//'` or `basename $(pwd)`

**If NOT indexed:**
- Tell the user what CodeIndex will do for them
- Confirm the project name (use suggestion from argument or auto-detected name)
- Proceed to Phase 2

**If ALREADY indexed:**
- Tell the user: "This project is already indexed! Use `/codeindex` to update, or `/codeindex force` to rebuild from scratch."
- Show current stats and stop here

### Phase 2: Build Knowledge Graph

```bash
npx codeindex analyze
```

Wait for completion and report results (symbol count, relationship count, etc.).

### Phase 3: Gather Project Knowledge from Git History

Analyze the last 30 days of git history:

1. **Recent commits**: `git log --oneline --since="30 days ago" --no-merges`
2. **Commit themes**: `git log --format="%s" --since="30 days ago" --no-merges | sort | head -80`
3. **Active contributors**: `git shortlog -sn --since="30 days ago" --no-merges`
4. **Most changed files**: `git log --since="30 days ago" --no-merges --pretty=format: --name-only | sort | uniq -c | sort -rn | head -20`
5. **Recent tags/releases**: `git tag --sort=-creatordate | head -5`
6. **Key project files** (read if they exist): README.md, CLAUDE.md, package.json, pyproject.toml, Cargo.toml, go.mod, build.gradle
7. **Branch structure**: `git branch -a --sort=-committerdate | head -15`

### Phase 4: Analyze and Store Knowledge

Analyze ALL gathered information and create `remember()` observations. Extract meaningful insights — don't dump raw data.

**Create observations for (if relevant info exists):**

1. **Project overview** (type: `note`) — what it does, tech stack, key dependencies
2. **Architecture patterns** (type: `pattern`) — directory structure, code organization
3. **Active development areas** (type: `note`) — what's actively being worked on
4. **Development conventions** (type: `do` or `dont`) — commit style, branching strategy
5. **Recent major changes** (type: `note`) — significant features, migrations, refactors
6. **Build and tooling** (type: `note`) — how to build, test, run the project

**Rules:**
- Keep each observation concise (max 200 words)
- Use `repo` scope, specify the repo name
- Add relevant `tags` and `refs`
- Use `recall()` first to avoid duplicates
- Extract INSIGHTS, not raw data

### Phase 5: Summary Report

Show: graph stats, observations created, what the user can do next.

## Important

- NEVER start a server
- Use `remember()` (CodeIndex MCP) for all memory, NOT file-based or mem0
- Respond in the user's language
