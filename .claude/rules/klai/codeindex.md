# CodeIndex Integration

> Graph-powered code intelligence with Klai-specific enrichments.
> Setup: `npm install -g klai-private/tools/codeindex-1.3.56.tgz && codeindex setup && codeindex analyze`

## Tool Selection: Serena vs CodeIndex

Serena and CodeIndex serve different purposes. Using the wrong tool wastes time.

**Serena** (LSP-based, real-time, always accurate):
- Symbol lookup, definition, references (1-hop)
- Code editing: `replace_symbol_body`, `insert_after_symbol`
- Type-safe rename refactoring
- File structure: `get_symbols_overview`

**CodeIndex** (graph-based, pre-computed, needs refresh):
- Blast radius / impact analysis (multi-hop call chains)
- Semantic search ("how does authentication work?")
- Architecture understanding (communities, execution flows)
- Enrichment queries (git hotspots, SPEC links, test coverage, PageRank)

**Decision tree:**
1. Editing code? → Serena
2. "What calls X?" (direct callers only) → Serena `find_referencing_symbols`
3. "What breaks if I change X?" (full impact) → CodeIndex `impact`
4. "How does X work?" (architecture) → CodeIndex `query`
5. "Is X tested / how often does it change / which SPEC?" → CodeIndex cypher

## Enrichment Queries

The enrichment layer adds four data types to the `description` field of Function/Method nodes.
Query via `codeindex cypher`:

```cypher
-- Riskiest code: high churn + no tests (exclude test files)
MATCH (n:Function)
WHERE n.description CONTAINS 'git_hotspot'
  AND NOT n.description CONTAINS '_tested_by'
  AND NOT n.filePath CONTAINS 'test'
RETURN n.name, n.filePath, n.description

-- Functions implementing a specific SPEC
MATCH (n:Function)
WHERE n.description CONTAINS 'SPEC-KB-ANALYZE-001'
RETURN n.name, n.filePath

-- Most central functions (PageRank)
MATCH (n:Function)
WHERE n.description CONTAINS '_pagerank'
RETURN n.name, n.filePath, n.description

-- Functions tested by a specific test file
MATCH (n:Function)
WHERE n.description CONTAINS 'test_search.py'
RETURN n.name, n.filePath
```

## Keeping the Index Fresh

The index is a snapshot. After code changes it becomes stale.

| Situation | Command |
|---|---|
| After a feature branch | `codeindex update && node scripts/codeindex-enrich.mjs` |
| Full re-index (force) | `./scripts/codeindex-analyze-and-enrich.sh --force` |
| Check freshness | `codeindex status` |

The SessionStart hook warns when the index is behind HEAD.

## Hooks (project-local)

Defined in `.claude/settings.local.json` (not committed, Klai-only):
- **PreToolUse** (Grep|Glob|Bash): augments search results with graph context
- **UserPromptSubmit**: injects project stats and memory context
- **SessionStart**: checks index freshness

## Setup for New Team Members

```bash
# 1. Install CodeIndex (from klai-private)
npm install -g klai-private/tools/codeindex-1.3.56.tgz

# 2. Configure MCP + hooks + skills
codeindex setup

# 3. Index the codebase
codeindex analyze

# 4. Run enrichment
node scripts/codeindex-enrich.mjs

# 5. Restart Claude Code
```

## File Locations

| What | Where | Committed |
|---|---|---|
| KuzuDB graph | `~/.codeindex/klai/kuzu` | No (per-machine) |
| Enrichment sidecar | `~/.codeindex/klai/enrichment.json` | No |
| CodeIndex hooks | `~/.claude/hooks/codeindex/` | No (installed by setup) |
| CodeIndex skills | `.claude/skills/codeindex/` | Yes |
| Enrichment script | `scripts/codeindex-enrich.mjs` | Yes |
| Wrapper script | `scripts/codeindex-analyze-and-enrich.sh` | Yes |
| Compiled package | `klai-private/tools/codeindex-1.3.56.tgz` | Private repo only |
| Source code | `codeindex-src/` | Gitignored |
