---
description: "Force full re-index: rebuild knowledge graph from scratch and refresh project memory"
---

# CodeIndex Force — Full Rebuild

Force a complete re-index of the codebase, rebuilding the knowledge graph from scratch and refreshing all project memory.

## Workflow

### Phase 1: Verify Project

1. Call `mcp__codeindex__list_repos` to confirm this project is indexed
2. If NOT indexed, tell the user to run `/codeindex setup` first and stop

### Phase 2: Force Rebuild Knowledge Graph

```bash
npx codeindex analyze --force
```

Wait for completion and report results.

### Phase 3: Refresh Project Memory

Analyze the last 30 days of git history (same as setup):

1. `git log --oneline --since="30 days ago" --no-merges`
2. `git log --format="%s" --since="30 days ago" --no-merges | sort | head -80`
3. `git shortlog -sn --since="30 days ago" --no-merges`
4. `git log --since="30 days ago" --no-merges --pretty=format: --name-only | sort | uniq -c | sort -rn | head -20`
5. `git tag --sort=-creatordate | head -5`
6. Read key project files: README.md, CLAUDE.md, package manifest
7. `git branch -a --sort=-committerdate | head -15`

### Phase 4: Update Observations

Use `recall()` to find existing observations, then update or create new ones:

1. **Project overview** (type: `note`) — what it does, tech stack
2. **Architecture patterns** (type: `pattern`) — directory structure, organization
3. **Active development areas** (type: `note`) — what's being worked on
4. **Development conventions** (type: `do`/`dont`) — commit style, branching
5. **Recent major changes** (type: `note`) — features, migrations, refactors
6. **Build and tooling** (type: `note`) — how to build, test, run

Keep observations concise (max 200 words), use `repo` scope, add `tags` and `refs`.

### Phase 5: Summary Report

Show: graph stats (before vs after if possible), observations updated/created.

## Important

- NEVER start a server
- Use `remember()` (CodeIndex MCP) for all memory
- Respond in the user's language
