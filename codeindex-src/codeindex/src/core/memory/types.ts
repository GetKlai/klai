/**
 * Memory Module Types
 *
 * Defines the observation model for CodeIndex's persistent memory system.
 * Observations are stored as nodes in a separate KuzuDB (read-write),
 * alongside the code knowledge graph (read-only).
 */

// ─── Observation Categories ──────────────────────────────────────────

export const OBSERVATION_TYPES = [
  'learning',    // Something learned during development
  'preference',  // Developer preference
  'do',          // Something you SHOULD do
  'dont',        // Something you should NOT do
  'decision',    // Architecture/design decision
  'bug',         // Bug found + resolution
  'pattern',     // Recurring pattern
  'note',        // Freeform note
] as const;

export type ObservationType = typeof OBSERVATION_TYPES[number];

// ─── Scope ───────────────────────────────────────────────────────────

export type ObservationScope = 'global' | 'repo';
export type RecallScope = 'all' | 'global' | 'repo';

// ─── Observation ─────────────────────────────────────────────────────

export interface Observation {
  uid: string;
  name: string;           // Short title (< 120 chars)
  type: ObservationType;
  content: string;        // Concise description (max ~200 words)
  tags: string[];
  project: string;        // Project name or "_global"
  createdAt: string;      // ISO 8601
  updatedAt: string;      // ISO 8601
  sessionId?: string;
  archived: boolean;
}

// ─── Observation References (links to code symbols) ──────────────────

export interface ObservationRef {
  observationUid: string;
  refType: 'symbol' | 'file' | 'process' | 'cluster';
  refId: string;          // Symbol UID, file path, process/cluster label
  refName: string;        // Human-readable name
}

// ─── Tool Input Types ────────────────────────────────────────────────

export interface RememberInput {
  title: string;
  content: string;
  type: ObservationType;
  scope?: ObservationScope;  // default: 'repo'
  tags?: string[];
  refs?: string[];           // Symbol names or file paths to link
  repo?: string;
}

export interface RecallInput {
  query?: string;
  type?: ObservationType;
  scope?: RecallScope;       // default: 'all'
  days?: number;             // Recency filter
  limit?: number;            // default: 10
  repo?: string;
}

export interface ForgetInput {
  id: string;
  permanent?: boolean;       // default: false (soft delete)
  repo?: string;
}

// ─── Search Result ───────────────────────────────────────────────────

export interface ObservationSearchResult {
  observation: Observation;
  refs: ObservationRef[];
  score?: number;           // Search relevance score
  source: 'global' | string; // '_global' or project name
}

// ─── Global Store Constants ──────────────────────────────────────────

export const GLOBAL_PROJECT_NAME = '_global';
export const MEMORY_DB_DIR = 'memory';
