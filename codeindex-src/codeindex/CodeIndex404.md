# CodeIndex 404 — Complete Technical Reference

> **Version 1.3.13** | Graph-powered code intelligence + persistent developer memory for AI agents.

CodeIndex builds a knowledge graph over any codebase — call chains, blast radius, execution flows, semantic search — and adds persistent memory for decisions, bugs, patterns, and preferences. One tool that knows both how your code works and what you've learned about it.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Storage Layout](#storage-layout)
3. [Graph Schema](#graph-schema)
4. [Search System](#search-system)
5. [Embedding System](#embedding-system)
6. [Memory System](#memory-system)
7. [MCP Tools](#mcp-tools)
8. [MCP Resources](#mcp-resources)
9. [CLI Commands](#cli-commands)
10. [Hook System](#hook-system)
11. [Augmentation Engine](#augmentation-engine)
12. [MCP Server](#mcp-server)
13. [Setup & Configuration](#setup--configuration)
14. [Data Flow](#data-flow)
15. [Key Files Reference](#key-files-reference)

---

## Architecture Overview

CodeIndex has two core systems:

### Code Intelligence (Read-Only at Runtime)
- **Graph database** (KuzuDB) storing symbols, relationships, execution flows, and clusters
- **Hybrid search** combining BM25 full-text search with semantic vector search (384-dim embeddings)
- **Augmentation engine** that enriches IDE searches with graph context in <500ms
- **Process detection** that traces execution flows through the call graph

### Persistent Memory (Read-Write)
- **Observation store** for decisions, bugs, patterns, preferences, and learnings
- **Dual scope**: project-specific memory linked to code symbols + global cross-project memory
- **Semantic recall** via the same embedding model as the code graph
- **Hook injection** that surfaces relevant observations at session start

### How They Connect

```
User Query → Hybrid Search (BM25 + Semantic)
                ↓
         Code Symbols + Execution Flows
                ↓
         + Linked Observations (decisions, bugs, patterns)
                ↓
         Ranked Results with Full Context
```

Memory observations can reference code symbols. When you `context()` a symbol, linked observations (bugs, decisions) appear alongside callers and callees. When you `recall()`, hybrid search finds observations by both text matching and semantic similarity.

---

## Storage Layout

All data lives under `~/.codeindex/`:

```
~/.codeindex/
├── registry.json                    # Global registry of all indexed repos
├── dismissed.json                   # Repos where user declined indexing
├── config.json                      # CLI config (apiKey, model, baseUrl)
├── _global/
│   └── memory/                      # Global memory KuzuDB (read-write)
│       ├── nodes.csv
│       ├── ...
├── {ProjectName}/
│   ├── kuzu/                        # Code knowledge graph (read-only at runtime)
│   │   ├── nodes.csv
│   │   ├── ...
│   ├── memory/                      # Project memory KuzuDB (read-write)
│   │   ├── nodes.csv
│   │   ├── ...
│   └── meta.json                    # Metadata: repoPath, lastCommit, stats, indexedAt
└── {AnotherProject}/
    └── ...
```

### Registry Entry

```json
{
  "name": "my-project",
  "path": "/Users/dev/my-project",
  "storagePath": "/Users/dev/.codeindex/my-project",
  "indexedAt": "2026-03-09T21:00:00.000Z",
  "lastCommit": "abc1234",
  "stats": {
    "files": 142,
    "nodes": 1348,
    "edges": 3469,
    "communities": 15,
    "processes": 104,
    "embeddings": 1200
  }
}
```

### Worktree Support

CodeIndex resolves git worktrees to the main repo automatically:
- Uses `git rev-parse --git-common-dir` → `dirname` to find the main repo root
- Falls back to `git rev-parse --show-toplevel`
- All registry lookups use the resolved main repo path

---

## Graph Schema

The code knowledge graph uses KuzuDB with a single relation table design.

### Node Types (24 total)

**Core nodes** (all codebases):

| Node | Key Properties |
|------|---------------|
| `File` | id, name, filePath, startLine, endLine, content |
| `Folder` | id, name, filePath |
| `Function` | id, name, filePath, startLine, endLine, isExported, content, description |
| `Class` | id, name, filePath, startLine, endLine, isExported, content, description |
| `Interface` | id, name, filePath, startLine, endLine, isExported, content, description |
| `Method` | id, name, filePath, startLine, endLine, content, description |
| `CodeElement` | id, name, filePath, startLine, endLine, content, description |

**Multi-language nodes** (use backticks in Cypher):

| Node | Languages |
|------|-----------|
| `` `Struct` `` | Rust, Go, C, Swift |
| `` `Enum` `` | TypeScript, Rust, Swift, Java |
| `` `Trait` `` | Rust |
| `` `Impl` `` | Rust |
| `` `TypeAlias` `` | TypeScript, Rust |
| `` `Const` `` | JavaScript/TypeScript, Rust |
| `` `Static` `` | Rust |
| `` `Macro` `` | Rust, C |
| `` `Typedef` `` | C |
| `` `Union` `` | C |
| `` `Namespace` `` | C++, C# |
| `` `Property` `` | TypeScript, C# |
| `` `Record` `` | C#, Java |
| `` `Delegate` `` | C# |
| `` `Annotation` `` | Java |
| `` `Constructor` `` | Java, TypeScript |
| `` `Template` `` | C++ |
| `` `Module` `` | Python, Rust |

**Analysis nodes:**

| Node | Properties |
|------|-----------|
| `Community` | id, label, heuristicLabel, keywords[], description, enrichedBy, cohesion (DOUBLE), symbolCount (INT32) |
| `Process` | id, label, heuristicLabel, processType, stepCount (INT32), communities[], entryPointId, terminalId |

### Edge Types (via CodeRelation)

All relationships use a single `CodeRelation` table with a `type` property:

| Edge Type | Meaning | Example |
|-----------|---------|---------|
| `CONTAINS` | Folder → File, File → symbols | File contains Function |
| `DEFINES` | File → symbol definition | File defines Class |
| `CALLS` | Symbol → Symbol | Function calls Function |
| `IMPORTS` | File → File or Symbol → Symbol | Module imports module |
| `EXTENDS` | Class/Interface → Class/Interface | Class extends BaseClass |
| `IMPLEMENTS` | Class → Interface | Class implements Interface |
| `MEMBER_OF` | Symbol → Community | Function belongs to cluster |
| `STEP_IN_PROCESS` | Symbol → Process | Function is step 3 in flow |

**Edge properties:**
- `type` (STRING) — one of the above
- `confidence` (DOUBLE) — 0.0 to 1.0
- `reason` (STRING) — why this relation exists
- `step` (INT32) — step number for STEP_IN_PROCESS

### Embedding Table

```sql
CREATE NODE TABLE CodeEmbedding (
  nodeId STRING PRIMARY KEY,
  embedding FLOAT[384]
)
```

HNSW vector index with cosine similarity for semantic search.

### Cypher Examples

```cypher
-- Find callers of a function
MATCH (a)-[:CodeRelation {type: 'CALLS'}]->(b:Function {name: "validateUser"})
RETURN a.name, a.filePath

-- Find community members
MATCH (f)-[:CodeRelation {type: 'MEMBER_OF'}]->(c:Community)
WHERE c.heuristicLabel = "Auth"
RETURN f.name

-- Trace execution flow steps
MATCH (s)-[r:CodeRelation {type: 'STEP_IN_PROCESS'}]->(p:Process)
WHERE p.heuristicLabel = "UserLogin"
RETURN s.name, r.step ORDER BY r.step

-- Find what a class extends
MATCH (c:Class)-[:CodeRelation {type: 'EXTENDS'}]->(parent)
WHERE c.name = "AdminService"
RETURN parent.name, parent.filePath
```

---

## Search System

### Hybrid Search (BM25 + Semantic)

CodeIndex uses Reciprocal Rank Fusion (RRF) to merge two search strategies:

1. **BM25 Full-Text Search** — KuzuDB FTS index over symbol names, content, and descriptions
2. **Semantic Vector Search** — 384-dimensional cosine similarity over snowflake-arctic-embed-xs embeddings

**RRF Merging Algorithm:**

```
RRF_K = 60  (standard literature value)

For each result from BM25 at rank i:
  score += 1 / (K + i + 1)

For each result from Semantic at rank i:
  score += 1 / (K + i + 1)

Results found by BOTH methods get scores summed → boosted ranking.
Sort by combined score descending.
```

**HybridSearchResult:**

```typescript
{
  filePath: string;
  score: number;                        // RRF combined score
  rank: number;                         // Final rank
  sources: ('bm25' | 'semantic')[];     // Which methods found it
  nodeId?: string;
  name?: string;
  label?: string;
  startLine?: number;
  endLine?: number;
  bm25Score?: number;                   // Original BM25 score
  semanticScore?: number;               // 1 - distance
}
```

### Process-Grouped Results

The `query` tool doesn't just return symbols — it groups them by execution flow:

1. Run hybrid search → get ranked symbols
2. For each symbol, find which Processes it participates in (STEP_IN_PROCESS)
3. Group symbols by process, rank processes by aggregate score
4. Return: `processes[]`, `process_symbols[]`, `definitions[]`

This means a query like "authentication" returns *execution flows* (e.g., "UserLogin → validateCredentials → createSession → setToken") rather than isolated functions.

---

## Embedding System

### Model: snowflake-arctic-embed-xs

| Property | Value |
|----------|-------|
| Parameters | 22M |
| Dimensions | 384 |
| Download size | ~90MB |
| Framework | transformers.js (ONNX Runtime) |
| Similarity | Cosine |

### Device Support

| Platform | Accelerator | Fallback |
|----------|-------------|----------|
| Windows | DirectML (DirectX12 GPU) | CPU |
| Linux | CUDA | CPU |
| macOS | CPU | WASM |

**CUDA Detection:** Checks `ldconfig -p` cache, then `CUDA_PATH` and `LD_LIBRARY_PATH` env vars, probes for `libcublasLt.so.12`.

### Key Functions

```typescript
initEmbedder(onProgress?, config?, forceDevice?)  // Singleton, loads model once
embedText(text: string): Float32Array              // Single text → 384-dim vector
embedBatch(texts: string[]): Float32Array[]        // Batch (more efficient)
isEmbedderReady(): boolean                         // Check init status
disposeEmbedder(): void                            // Free memory
getCurrentDevice(): string | null                  // Active device
```

### Usage in Memory

Observations are embedded as `"{title}. {content}"` and stored in a separate `MemoryEmbedding` table with the same 384-dim vectors and HNSW cosine index.

---

## Memory System

### Overview

The memory system stores persistent observations — decisions, bugs, patterns, preferences, and learnings — in a separate KuzuDB database alongside the code graph.

**Two scopes:**
- **Project memory** (`~/.codeindex/{ProjectName}/memory/`) — observations linked to code symbols, specific to one codebase
- **Global memory** (`~/.codeindex/_global/memory/`) — cross-project knowledge (preferences, learnings, dos/donts)

### Observation Categories

| Type | Description | Typical Scope |
|------|-------------|---------------|
| `learning` | Something learned during development | project or global |
| `preference` | Developer preference (e.g., "always use strict TypeScript") | global |
| `do` | Best practice — something you SHOULD do | global or project |
| `dont` | Anti-pattern — something you should NOT do | global or project |
| `decision` | Architecture/design decision with rationale | project |
| `bug` | Bug found + how it was resolved | project |
| `pattern` | Recurring code pattern | project |
| `note` | Freeform note | both |

### Observation Schema

```sql
CREATE NODE TABLE Observation (
  uid STRING PRIMARY KEY,
  name STRING,            -- Title (< 120 chars)
  type STRING,            -- Category (see above)
  content STRING,         -- Description (max ~200 words)
  tags STRING,            -- JSON array of tags
  project STRING,         -- Project name or "_global"
  createdAt STRING,       -- ISO 8601
  updatedAt STRING,       -- ISO 8601
  sessionId STRING,       -- Session that created it
  archived BOOLEAN        -- Soft-delete flag
)

CREATE NODE TABLE ObservationRef (
  id STRING PRIMARY KEY,
  observationUid STRING,
  refType STRING,         -- 'symbol' | 'file' | 'process' | 'cluster'
  refId STRING,           -- Symbol UID, file path, or label
  refName STRING          -- Human-readable name
)

CREATE REL TABLE ObservationRelation (
  FROM Observation TO Observation,     -- SUPERSEDES
  FROM Observation TO ObservationRef,  -- OBSERVES
  type STRING
)

CREATE NODE TABLE MemoryEmbedding (
  nodeId STRING PRIMARY KEY,
  embedding FLOAT[384]
)
```

### Connection Pool

The memory adapter maintains a pool of up to 6 simultaneous KuzuDB connections with LRU eviction:

```typescript
initMemoryDb(key, dbPath)     // Initialize or get memory database
memoryQuery(key, cypher)      // Execute query, updates LRU timestamp
isMemoryDbReady(key)          // Check if DB is initialized
closeMemoryDb(key?)           // Close one or all connections
```

**Key design decisions:**
- Memory databases are opened in **read-write** mode (unlike the code graph which is read-only at runtime)
- Schema creation is **idempotent** — handles "already exists" errors gracefully
- **stdout silencing** during KuzuDB operations (native module can corrupt MCP stdio)

### Hybrid Search in Recall

When `recall()` is called with a query, it uses the same RRF approach as code search:

1. **Text search** — CONTAINS matching on name, content, and tags
2. **Semantic search** — Vector similarity on MemoryEmbedding table
3. **RRF merge** with K=60, same as code search
4. **Fallback** — If embedder is unavailable (model not downloaded), text-only search

### Observations in Context

When you use the `context()` tool on a symbol, CodeIndex searches memory for observations that reference that symbol and includes them in the output. This means a `context("initKuzu")` might show:

```
Observations:
- [decision] KuzuDB single-file format chosen (3d ago)
  Multi-file had lock issues during concurrent hook access
- [bug] Race condition in connection pool (1d ago)
  Fixed by adding session lock around DB initialization
```

### "None" Mode

For repos where you don't want code indexing:
- Run `codeindex dismiss` or type "none" when prompted
- No code graph, no `query`/`context`/`impact` tools
- Memory tools (`remember`, `recall`, `forget`) still work via global scope
- The hook still injects global preferences and learnings

---

## MCP Tools

CodeIndex exposes 10 tools via MCP (Model Context Protocol).

### Code Intelligence Tools

#### `list_repos`

List all indexed repositories.

```json
// Input: (no params)
// Output: [{name, path, indexedAt, lastCommit, stats}]
```

**When to use:** First step when multiple repos are indexed. After this: READ `codeindex://repo/{name}/context`.

#### `query`

Search execution flows related to a concept. Returns processes (call chains) ranked by relevance.

```json
{
  "query": "authentication flow",           // Required
  "task_context": "adding OAuth support",   // Optional, helps ranking
  "goal": "find existing auth validation",  // Optional, helps ranking
  "limit": 5,                               // Max processes (default: 5)
  "max_symbols": 8,                         // Max symbols per process (default: 8)
  "include_content": false,                 // Include source code signatures
  "repo": "my-project"                      // Omit if only one repo
}
```

**Returns:**
- `processes[]` — Ranked execution flows with relevance priority
- `process_symbols[]` — All symbols in those flows with file locations and module
- `definitions[]` — Standalone types/interfaces not in any process

**When to use:** Understanding how code works together. Complements grep/IDE search by returning execution flows, not just file matches.

#### `context`

360-degree view of a single code symbol.

```json
{
  "name": "validateUser",     // Symbol name
  "uid": "abc-123",           // Or direct UID from prior results
  "file_path": "src/auth.ts", // Disambiguate common names
  "include_content": false,
  "repo": "my-project"
}
```

**Returns:** Categorized incoming/outgoing references (calls, imports, extends, implements), process participation, cluster membership, linked observations (bugs, decisions).

**Handles disambiguation:** If multiple symbols share the same name, returns candidates for you to pick from.

#### `impact`

Blast radius analysis — what breaks if you change a symbol.

```json
{
  "target": "AuthService",          // Required
  "direction": "upstream",          // Required: "upstream" (dependants) or "downstream" (dependencies)
  "maxDepth": 3,                    // Default: 3
  "relationTypes": ["CALLS"],       // Filter: CALLS, IMPORTS, EXTENDS, IMPLEMENTS
  "includeTests": false,            // Default: false
  "minConfidence": 0.7,             // Default: 0.7
  "repo": "my-project"
}
```

**Returns:**
- `risk`: LOW / MEDIUM / HIGH / CRITICAL
- `summary`: direct callers, processes affected, modules affected
- `affected_processes`: which execution flows break and at which step
- `affected_modules`: which functional areas are hit (direct vs indirect)
- `byDepth`: affected symbols grouped by traversal depth
  - d=1: **WILL BREAK** (direct callers/importers)
  - d=2: **LIKELY AFFECTED** (indirect)
  - d=3: **MAY NEED TESTING** (transitive)

#### `detect_changes`

Analyze uncommitted git changes and find affected execution flows.

```json
{
  "scope": "unstaged",           // "unstaged" | "staged" | "all" | "compare"
  "base_ref": "main",           // Branch for "compare" scope
  "save_observation": true,      // Auto-save a change observation to memory
  "repo": "my-project"
}
```

**Returns:** Changed symbols, affected processes, risk summary.

When `save_observation: true`, automatically creates a `note` observation with change details.

#### `rename`

Multi-file coordinated rename using graph + text search.

```json
{
  "symbol_name": "oldName",      // Current name
  "symbol_uid": "abc-123",       // Or direct UID
  "new_name": "newName",         // Required
  "file_path": "src/auth.ts",   // Disambiguate
  "dry_run": true,               // Preview by default
  "repo": "my-project"
}
```

**Returns:** Edits tagged with confidence:
- `"graph"` — Found via knowledge graph (high confidence, safe to accept)
- `"text_search"` — Found via regex (lower confidence, review carefully)

#### `cypher`

Execute raw Cypher queries against the code knowledge graph.

```json
{
  "query": "MATCH (f:Function) RETURN f.name LIMIT 10",
  "repo": "my-project"
}
```

**Returns:** `{ markdown, row_count }` — Results formatted as a Markdown table.

**Always read `codeindex://repo/{name}/schema` first** to understand the available schema.

### Memory Tools

#### `remember`

Save an observation to persistent memory.

```json
{
  "title": "Always use strict TypeScript",      // Required, < 120 chars
  "content": "strictNullChecks and noImplicitAny must always be enabled",  // Required
  "type": "preference",                         // Required (see categories above)
  "scope": "global",                            // "repo" (default) or "global"
  "tags": ["typescript", "config"],             // Optional
  "refs": ["tsconfig.json", "initProject"],     // Optional: symbol names or file paths
  "repo": "my-project"                          // For repo scope with multiple repos
}
```

**Behavior:**
- Creates observation in the appropriate memory database
- Auto-embeds for semantic search (best-effort, non-blocking)
- Resolves ref names to graph symbol IDs (if code graph exists)
- Returns: `{status: 'saved', uid, title, type, scope}`

#### `recall`

Search persistent memory for observations.

```json
{
  "query": "database choice",      // Text + semantic search
  "type": "decision",              // Filter by category
  "scope": "all",                  // "all" (default), "global", or "repo"
  "days": 30,                      // Recency filter
  "limit": 10,                     // Max results (default: 10)
  "repo": "my-project"
}
```

**Behavior:**
- If `query` provided: uses hybrid search (text + semantic RRF)
- If no `query`: returns recent observations sorted by date
- `scope: "all"` searches both global and project memory
- Returns: `{observations[], count, total_available}`

#### `forget`

Archive or permanently delete an observation.

```json
{
  "id": "uuid-here",       // Required
  "permanent": false        // false = archive (default), true = permanent delete
}
```

**Behavior:** Searches global + all project memories to find the observation by UID.

---

## MCP Resources

Lightweight reads (~100-500 tokens) for navigation and context.

### Static Resources

| URI | Description |
|-----|-------------|
| `codeindex://repos` | All indexed repos with stats |
| `codeindex://setup` | AGENTS.md content for all repos |
| `codeindex://memory/global` | Global cross-project memory |

### Dynamic Resource Templates

| URI Template | Description |
|--------------|-------------|
| `codeindex://repo/{name}/context` | Stats, staleness check, available tools |
| `codeindex://repo/{name}/clusters` | All Leiden-detected functional areas (top 20) |
| `codeindex://repo/{name}/processes` | All execution flows (top 20) |
| `codeindex://repo/{name}/schema` | Graph schema for Cypher queries |
| `codeindex://repo/{name}/cluster/{clusterName}` | Module members + cohesion score |
| `codeindex://repo/{name}/process/{processName}` | Step-by-step execution trace |
| `codeindex://repo/{name}/memory` | Project-specific memory (recent observations) |

### Resource Content Examples

**`codeindex://repo/my-project/context`:**
```yaml
project: my-project
path: /Users/dev/my-project
indexed_at: 2026-03-09T21:00:00.000Z
last_commit: abc1234
stats:
  files: 142
  symbols: 1348
  relationships: 3469
  communities: 15
  processes: 104
staleness: up-to-date  # or "5 commits behind HEAD"
```

**`codeindex://repo/my-project/clusters`:**
```yaml
clusters:
  - name: Authentication
    cohesion: 0.85
    symbols: 23
    keywords: [auth, token, session, validate]
  - name: Database
    cohesion: 0.78
    symbols: 18
    keywords: [query, connection, schema, migrate]
```

**`codeindex://memory/global`:**
```yaml
scope: global
total: 12
recent:
  - name: "Always use strict TypeScript"
    type: preference
    age: 2 days ago
    tags: [typescript]
  - name: "Never use any in public APIs"
    type: dont
    age: 5 days ago
    tags: [typescript, api]
```

---

## CLI Commands

### Core Commands

```bash
codeindex setup              # One-time setup: MCP config, hooks, skills
codeindex setup --uninstall  # Remove all CodeIndex integrations

codeindex analyze [name] [path]  # Index a repo (first time: name+path, updates: auto-detect)
codeindex update             # Re-index current repo
codeindex update --force     # Force full re-index
codeindex update --no-embeddings  # Skip embedding generation

codeindex list               # List all indexed repos
codeindex status             # Show index status for current repo
codeindex clean              # Delete index for current repo
codeindex clean --all        # Delete all indexes
codeindex clean -f           # Force without confirmation

codeindex rename <old> <new> # Rename project in registry
codeindex dismiss            # Suppress indexing suggestions for this repo
codeindex dismiss --undo     # Re-enable suggestions
```

### Direct Tool Commands (No MCP Overhead)

```bash
codeindex query "authentication flow" -l 5 --content
codeindex context validateUser -f src/auth.ts
codeindex impact AuthService -d upstream --depth 3
codeindex cypher "MATCH (f:Function) RETURN f.name LIMIT 10"
```

### Memory Commands

```bash
codeindex memory                    # List recent observations (all scopes)
codeindex memory -s "typescript"    # Search observations
codeindex memory -p my-project      # Filter by project

codeindex note "Always validate input" -t do -s global --tags "security,validation"
codeindex note "KuzuDB chosen for graph" -t decision -c "Embedded, no server needed"

codeindex learnings                 # All learnings (global + current project)
codeindex learnings my-project      # Learnings for a specific project
codeindex dos                       # All "do" rules
codeindex donts                     # All "dont" rules
codeindex preferences               # All developer preferences
codeindex decisions my-project      # Architecture decisions
codeindex bugs my-project           # Known bugs + resolutions
codeindex patterns                  # Recurring code patterns
```

### Advanced Commands

```bash
codeindex serve -p 3000     # HTTP server for web UI
codeindex mcp               # Start MCP server on stdio
codeindex augment <pattern> # Augment search with graph context (used by hooks)
codeindex memory-context [project]  # Output recent memory for hook injection (stderr)
codeindex wiki [path]       # Generate documentation wiki from graph
codeindex eval-server       # Lightweight HTTP server for SWE-bench evaluation
```

### Bare Command Behavior

Running `codeindex` with no subcommand:
1. If in an indexed repo → auto-runs `analyze` (update)
2. If in a git repo but not indexed → prompts for project name
   - Type a name → indexes the repo
   - Type "none" or "skip" → dismisses repo, enables memory-only mode
3. If not a git repo → shows help

---

## Hook System

CodeIndex installs two Claude Code hooks that fire automatically.

### UserPromptSubmit Hook (`codeindex-prompt-hook.cjs`)

**Fires on:** Every user prompt submission.

**Four paths:**

| Path | Condition | Behavior |
|------|-----------|----------|
| 1 | Indexed repo | Inject stats + staleness warning + tool guidance + memory context |
| 2 | Non-indexed git repo (not dismissed) | Suggest indexing + inject global memory |
| 2b | Dismissed repo | Inject global memory only |
| 3 | Not a git repo (but repos exist) | List indexed repos |
| 4 | No repos at all | Silent |

**Path 1 output example:**
```
[CodeIndex] Project "my-project" is indexed (1348 symbols, 3469 relationships, 104 execution flows, 15 clusters). Last indexed today.

Before starting any code task, use CodeIndex to understand the codebase:
- Use `query` to find execution flows related to your task
- Use `context` for a 360-degree view of any symbol
- Use `impact` to check blast radius before changing code
- Read `codeindex://repo/my-project/clusters` for an overview of all functional areas

Do NOT skip this step. CodeIndex gives you the call graph — use it.

[CodeIndex Memory]
- [preference] Always use strict TypeScript (global, 2d ago)
- [decision] KuzuDB single-file format (my-project, 3d ago)
```

**Staleness detection:**
- Counts commits between `lastCommit` and HEAD using `git rev-list --count`
- If >0 commits behind: instructs Claude to run `codeindex update` first

**Memory injection:**
- Calls `codeindex memory-context {projectName}` via `spawnSync` (3s timeout)
- Reads from stderr (KuzuDB stdout safety)
- Outputs recent observations in compact format

### PreToolUse Hook (`codeindex-hook.cjs`)

**Fires on:** Grep, Glob, Bash tool calls (search operations).

**Purpose:** Enriches search results with graph context — callers, callees, and execution flows.

**Pattern extraction:**
- **Grep** → `toolInput.pattern`
- **Glob** → keyword from glob pattern (regex extraction)
- **Bash** → parses `rg` or `grep` commands, extracts search pattern

**Workflow:**
1. Check if current directory is in an indexed repo
2. Extract search pattern from tool input
3. Run `codeindex augment {pattern}` (8s timeout)
4. Inject result as `additionalContext`

**Output format:**
```
[CodeIndex] 3 related symbols found:

validateUser (src/auth/validator.ts)
  Called by: loginHandler, registerHandler
  Calls: checkPassword, verifyEmail
  Flows: UserLogin (step 2/5), Registration (step 3/7)
```

---

## Augmentation Engine

Fast-path enrichment for search patterns. Designed for <500ms cold start, <200ms warm.

### Design

- Uses **BM25 only** (no semantic search — too slow for per-search augmentation)
- Clusters used internally for ranking, NOT exposed in output
- Output: pure relationships (callers, callees, process participation)
- **Graceful failure:** any error → empty string (never breaks the original search)

### Workflow

1. Find indexed repo for current working directory
2. BM25 search (top 10 results)
3. Map files to symbols (up to 5 files × 3 symbols)
4. For each symbol (up to 5):
   - Query callers (CALLS, limit 3)
   - Query callees (CALLS, limit 3)
   - Query process participation (STEP_IN_PROCESS)
   - Query cluster membership (MEMBER_OF → Community)
5. Rank by cluster cohesion (internal)
6. Format as structured text

---

## MCP Server

### Configuration

- **Protocol version:** MCP 1.1.9
- **Transport:** StdioServerTransport (child process stdin/stdout)
- **Capabilities:** tools, resources, prompts

### Next-Step Hints

After each tool call, the server appends a next-step hint:

| After | Hint |
|-------|------|
| `list_repos` | READ `codeindex://repo/{name}/context` |
| `query` | Use `context({name})` for deep dive |
| `context` | Use `impact()` for blast radius |
| `impact` | Review d=1 items first (WILL BREAK) |
| `detect_changes` | Review affected processes |
| `rename` | Run `detect_changes()` to verify |
| `cypher` | Use `context()` on result symbols |
| `remember` | Saved. Use `recall()` to verify |
| `recall` | Use `context()` on referenced symbols |
| `forget` | Observation archived/deleted |

### Built-in Prompts

**`detect_impact`** — Guided workflow for change impact analysis:
1. Run `detect_changes(scope, base_ref)`
2. For each changed symbol: `context({name})`
3. For high-risk items: `impact({target, direction: "upstream"})`
4. Summarize: changes, affected processes, risk level

**`generate_map`** — Guided workflow for architecture documentation:
1. READ `codeindex://repo/{name}/context`
2. READ `codeindex://repo/{name}/clusters`
3. READ `codeindex://repo/{name}/processes`
4. For top 5 processes: READ process detail
5. Generate mermaid architecture diagram
6. Write ARCHITECTURE.md

### Graceful Shutdown

Handles SIGINT/SIGTERM → `await backend.disconnect()` + `server.close()`.

---

## Setup & Configuration

### Installation

```bash
npx codeindex setup
```

This installs:
- MCP server configuration (for Claude Code, Cursor, etc.)
- Claude Code hooks (UserPromptSubmit + PreToolUse)
- Skill files for guided workflows

### Indexing a Repo

```bash
cd /path/to/your/repo
npx codeindex analyze my-project
```

Or just run `npx codeindex` and follow the prompt.

### MCP Configuration

The setup command writes MCP config for the active editor. Example for Claude Code (`.mcp.json`):

```json
{
  "mcpServers": {
    "codeindex": {
      "command": "npx",
      "args": ["--yes", "codeindex@latest", "mcp"]
    }
  }
}
```

### Hook Configuration

Hooks are installed at `~/.claude/hooks/`:

```json
{
  "hooks": {
    "UserPromptSubmit": [{
      "command": "node /path/to/codeindex-prompt-hook.cjs"
    }],
    "PreToolUse": [{
      "command": "node /path/to/codeindex-hook.cjs"
    }]
  }
}
```

### Environment Variables

| Variable | Purpose |
|----------|---------|
| `ORT_LOG_LEVEL=3` | Suppress ONNX Runtime warnings |
| `NODE_OPTIONS=--max-old-space-size=8192` | Default heap size (auto-set) |

---

## Data Flow

### Indexing Flow

```
Source Code
    ↓
Static Analysis (per-language parsers)
    ↓
Symbols + Relationships extracted
    ↓
KuzuDB Graph populated (nodes + edges)
    ↓
Leiden Algorithm → Communities (functional areas)
    ↓
BFS Process Detection → Execution Flows
    ↓
snowflake-arctic-embed-xs → 384-dim vectors
    ↓
HNSW Vector Index created
    ↓
Registry + meta.json updated
```

### Query Flow

```
User Query ("authentication flow")
    ↓
BM25 FTS Search → ranked symbols
Semantic Vector Search → ranked symbols
    ↓
RRF Merge (K=60) → combined ranking
    ↓
Process Grouping (STEP_IN_PROCESS)
    ↓
Community Assignment (MEMBER_OF)
    ↓
Memory Search (linked observations)
    ↓
Formatted Response:
  - Execution flows with symbols
  - File locations
  - Linked observations (decisions, bugs)
```

### Memory Flow

```
remember("KuzuDB chosen", "Embedded, no server", type: "decision")
    ↓
Create Observation node in project memory KuzuDB
    ↓
Resolve refs → link to code graph symbols
    ↓
Embed "{title}. {content}" → 384-dim vector
    ↓
Store in MemoryEmbedding table
    ↓
recall("database choice")
    ↓
Text CONTAINS search + Semantic vector search
    ↓
RRF Merge → ranked observations
    ↓
Include in hook injection + context() output
```

### Hook Flow

```
User types prompt in Claude Code
    ↓
UserPromptSubmit hook fires
    ↓
Check: indexed repo? → inject stats + guidance + memory
Check: non-indexed? → suggest indexing + inject global memory
Check: dismissed? → inject global memory only
    ↓
User's prompt is sent to Claude with injected context
    ↓
Claude uses Grep/Glob/Bash to search
    ↓
PreToolUse hook fires
    ↓
Extract search pattern → run BM25 augmentation
    ↓
Inject callers/callees/flows into search results
```

---

## Key Files Reference

### Code Intelligence

| File | Purpose |
|------|---------|
| `src/core/kuzu/schema.ts` | Graph schema definition (24 node types, edges, embeddings) |
| `src/core/search/hybrid-search.ts` | RRF-merged BM25 + semantic search |
| `src/core/embeddings/embedder.ts` | snowflake-arctic-embed-xs embedding model |
| `src/core/augmentation/engine.ts` | Fast-path search augmentation (<500ms) |
| `src/mcp/local/local-backend.ts` | All tool implementations (query, context, impact, etc.) |
| `src/mcp/tools.ts` | MCP tool definitions (schemas + descriptions) |
| `src/mcp/resources.ts` | MCP resource definitions + handlers |
| `src/mcp/server.ts` | MCP server setup, dispatch, next-step hints |
| `src/storage/repo-manager.ts` | Registry, metadata, storage path management |

### Memory System

| File | Purpose |
|------|---------|
| `src/core/memory/types.ts` | Observation interfaces, category types, scope types |
| `src/core/memory/schema.ts` | Memory KuzuDB schema (Observation, ObservationRef, MemoryEmbedding) |
| `src/core/memory/memory-adapter.ts` | Connection pool (max 6, LRU eviction, read-write) |
| `src/core/memory/observation-store.ts` | CRUD + hybrid search for observations |
| `src/core/memory/observation-embedder.ts` | Embed observations for semantic recall |
| `src/core/memory/global-store.ts` | Global memory initialization + path management |

### CLI

| File | Purpose |
|------|---------|
| `src/cli/index.ts` | All CLI command registration + routing |
| `src/cli/memory.ts` | Memory CLI commands (memory, note, learnings, dos, etc.) |
| `src/cli/memory-context.ts` | Fast-path memory output for hook injection |
| `src/cli/prompt-utils.ts` | Interactive prompts (project name, "none" option) |

### Hooks

| File | Purpose |
|------|---------|
| `hooks/claude/codeindex-prompt-hook.cjs` | UserPromptSubmit: context + memory injection |
| `hooks/claude/codeindex-hook.cjs` | PreToolUse: search augmentation |

---

## For AI Coding Sessions

### Quick Start Checklist

1. **Read `codeindex://repo/{name}/context`** — check if index is fresh
2. If stale: run `codeindex update` in terminal
3. **Use `query`** to find relevant execution flows before writing code
4. **Use `context`** for 360-degree view of symbols you'll modify
5. **Use `impact`** to check blast radius before refactoring
6. **Use `recall`** to check for past decisions, known bugs, or preferences
7. **Use `remember`** to save important decisions and learnings

### Tool Sequence Patterns

**Understanding code:**
```
query("concept") → context("symbol") → READ process/{name}
```

**Before making changes:**
```
impact("target", upstream) → review d=1 (WILL BREAK) → context(each affected symbol)
```

**After making changes:**
```
detect_changes(scope: "all") → review affected processes
```

**Refactoring:**
```
rename("old", "new", dry_run: true) → review edits → rename(dry_run: false) → detect_changes()
```

**Saving knowledge:**
```
remember(decision/bug/pattern) → recall(verify it's saved) → continue working
```

### What NOT to Do

- Don't skip CodeIndex and go straight to grep — you'll miss call chains and execution flows
- Don't change shared code without running `impact()` first
- Don't ignore stale index warnings — run `codeindex update`
- Don't use `cypher` without reading `codeindex://repo/{name}/schema` first
- Don't store large content in `remember` — keep it concise (max ~200 words)
