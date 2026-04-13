/**
 * Memory LadybugDB Schema
 *
 * Defines the graph schema for the memory database.
 * This is a separate LadybugDB from the code knowledge graph,
 * opened in read-write mode so the MCP server can write observations.
 *
 * Node types: Observation
 * Relation types: OBSERVES (→ code symbol ref), SUPERSEDES (→ older observation)
 * Embeddings: 384-dim vectors for semantic search (same model as code graph)
 */

// ─── Observation Node ────────────────────────────────────────────────

export const OBSERVATION_SCHEMA = `
CREATE NODE TABLE Observation (
  uid STRING,
  name STRING,
  type STRING,
  content STRING,
  tags STRING,
  project STRING,
  createdAt STRING,
  updatedAt STRING,
  sessionId STRING,
  archived BOOLEAN,
  PRIMARY KEY (uid)
)`;

// ─── Observation References ──────────────────────────────────────────
// Links observations to code symbols (by storing the ref info as a node)
// This is simpler than cross-database edges since code graph is separate

export const OBSERVATION_REF_SCHEMA = `
CREATE NODE TABLE ObservationRef (
  id STRING,
  observationUid STRING,
  refType STRING,
  refId STRING,
  refName STRING,
  PRIMARY KEY (id)
)`;

// ─── Relations ───────────────────────────────────────────────────────

export const OBSERVATION_REL_SCHEMA = `
CREATE REL TABLE ObservationRelation (
  FROM Observation TO Observation,
  FROM Observation TO ObservationRef,
  type STRING
)`;

// ─── Embedding Table ─────────────────────────────────────────────────

export const MEMORY_EMBEDDING_SCHEMA = `
CREATE NODE TABLE MemoryEmbedding (
  nodeId STRING,
  embedding FLOAT[384],
  PRIMARY KEY (nodeId)
)`;

// ─── FTS Index ───────────────────────────────────────────────────────
// BM25 full-text search over observation titles and content

export const MEMORY_FTS_SETUP = [
  `CALL CREATE_FTS_INDEX('Observation', 'obs_fts_idx', ['name', 'content', 'tags'])`,
];

// ─── Vector Index ────────────────────────────────────────────────────

export const MEMORY_VECTOR_INDEX = `
CALL CREATE_VECTOR_INDEX('MemoryEmbedding', 'memory_embedding_idx', 'embedding', metric := 'cosine')
`;

// ─── All Schema Queries (in order) ──────────────────────────────────

export const MEMORY_SCHEMA_QUERIES = [
  OBSERVATION_SCHEMA,
  OBSERVATION_REF_SCHEMA,
  OBSERVATION_REL_SCHEMA,
  MEMORY_EMBEDDING_SCHEMA,
];
