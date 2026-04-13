---
description: "Update knowledge graph and refresh project memory from recent git history"
argument-hint: "[days, e.g. '90 days' or '2 weeks']"
---

# CodeIndex Update — Refresh Graph & Memory

Update the knowledge graph and refresh project memory from recent git history. Quick command for daily use.

**Subcommands:** `/codeindex setup` (first-time init) | `/codeindex force` (full rebuild)

## Workflow

### Phase 1: Check State

1. Call `mcp__codeindex__list_repos` to verify this project is indexed
2. If NOT indexed, tell the user: "Project not yet indexed. Run `/codeindex setup` first." — then stop

### Phase 2: Update Knowledge Graph

```bash
npx codeindex analyze
```

This is incremental — only processes changed files. Report results.

### Phase 3: Gather Recent Git History

Parse the argument for a time period. If provided (e.g. "90 days", "2 weeks"), use that. Default: 30 days.

1. **Recent commits**: `git log --oneline --since="<period> ago" --no-merges`
2. **Commit themes**: `git log --format="%s" --since="<period> ago" --no-merges | sort | head -80`
3. **Active contributors**: `git shortlog -sn --since="<period> ago" --no-merges`
4. **Most changed files**: `git log --since="<period> ago" --no-merges --pretty=format: --name-only | sort | uniq -c | sort -rn | head -20`
5. **Recent tags/releases**: `git tag --sort=-creatordate | head -5`
6. **Key project files** (read if they exist): README.md, CLAUDE.md, package manifest
7. **Branch structure**: `git branch -a --sort=-committerdate | head -15`

### Phase 4: Update Project Memory

Use `recall()` to find existing observations first. Then update or create new ones:

1. **Project overview** (type: `note`) — what it does, tech stack
2. **Architecture patterns** (type: `pattern`) — directory structure, organization
3. **Active development areas** (type: `note`) — what's being worked on now
4. **Development conventions** (type: `do`/`dont`) — commit style, branching
5. **Recent major changes** (type: `note`) — features, migrations, refactors
6. **Build and tooling** (type: `note`) — how to build, test, run

**Rules:**
- Keep each observation concise (max 200 words)
- Use `repo` scope, specify the repo name
- Add relevant `tags` and `refs`
- Don't create duplicates — update existing observations when possible
- Extract INSIGHTS, not raw data

### Phase 5: Summary Report

Show: graph stats, observations created/updated, topics covered.

## Important

- NEVER start a server
- Use `remember()` (CodeIndex MCP) for all memory, NOT file-based or mem0
- Respond in the user's language
