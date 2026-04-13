/**
 * Observation Store
 *
 * CRUD operations for observations in the memory KuzuDB.
 * Handles creating, searching, linking, archiving, and deleting observations.
 */

import { randomUUID } from 'crypto';
import { memoryQuery, initMemoryDb, isMemoryDbReady } from './memory-adapter.js';
import type {
  Observation,
  ObservationRef,
  ObservationType,
  ObservationSearchResult,
  OBSERVATION_TYPES,
} from './types.js';
import { GLOBAL_PROJECT_NAME } from './types.js';

// ─── Helpers ─────────────────────────────────────────────────────────

/** Escape single quotes for Cypher string literals */
function esc(str: string): string {
  return str.replace(/\\/g, '\\\\').replace(/'/g, "\\'");
}

/** Format tags array as JSON string for storage */
function tagsToString(tags: string[]): string {
  return JSON.stringify(tags);
}

/** Parse tags JSON string back to array */
function parseTags(raw: string | null): string[] {
  if (!raw) return [];
  try { return JSON.parse(raw); } catch { return []; }
}

/** Convert a KuzuDB row to an Observation */
function rowToObservation(row: any): Observation {
  // KuzuDB returns node properties in a nested structure
  const o = row.o || row;
  return {
    uid: o.uid,
    name: o.name,
    type: o.type as ObservationType,
    content: o.content,
    tags: parseTags(o.tags),
    project: o.project,
    createdAt: o.createdAt,
    updatedAt: o.updatedAt,
    sessionId: o.sessionId || undefined,
    archived: o.archived === true,
  };
}

/** Convert a KuzuDB row to an ObservationRef */
function rowToRef(row: any): ObservationRef {
  const r = row.r || row;
  return {
    observationUid: r.observationUid,
    refType: r.refType as ObservationRef['refType'],
    refId: r.refId,
    refName: r.refName,
  };
}

// ─── Create ──────────────────────────────────────────────────────────

export interface CreateObservationInput {
  name: string;
  type: ObservationType;
  content: string;
  tags?: string[];
  project: string;
  sessionId?: string;
  refs?: Array<{ refType: ObservationRef['refType']; refId: string; refName: string }>;
}

/**
 * Create a new observation in the memory graph.
 * Optionally creates refs and links them to the observation.
 */
export async function createObservation(
  dbKey: string,
  input: CreateObservationInput,
): Promise<Observation> {
  const uid = randomUUID();
  const now = new Date().toISOString();
  const tags = tagsToString(input.tags || []);

  // Create the Observation node
  await memoryQuery(dbKey, `
    CREATE (o:Observation {
      uid: '${esc(uid)}',
      name: '${esc(input.name)}',
      type: '${esc(input.type)}',
      content: '${esc(input.content)}',
      tags: '${esc(tags)}',
      project: '${esc(input.project)}',
      createdAt: '${esc(now)}',
      updatedAt: '${esc(now)}',
      sessionId: '${esc(input.sessionId || '')}',
      archived: false
    })
  `);

  // Create refs if provided
  if (input.refs && input.refs.length > 0) {
    for (const ref of input.refs) {
      const refId = randomUUID();
      await memoryQuery(dbKey, `
        CREATE (r:ObservationRef {
          id: '${esc(refId)}',
          observationUid: '${esc(uid)}',
          refType: '${esc(ref.refType)}',
          refId: '${esc(ref.refId)}',
          refName: '${esc(ref.refName)}'
        })
      `);

      // Create edge from Observation to ObservationRef
      await memoryQuery(dbKey, `
        MATCH (o:Observation {uid: '${esc(uid)}'})
        MATCH (r:ObservationRef {id: '${esc(refId)}'})
        CREATE (o)-[:ObservationRelation {type: 'OBSERVES'}]->(r)
      `);
    }
  }

  return {
    uid,
    name: input.name,
    type: input.type,
    content: input.content,
    tags: input.tags || [],
    project: input.project,
    createdAt: now,
    updatedAt: now,
    sessionId: input.sessionId,
    archived: false,
  };
}

// ─── Search ──────────────────────────────────────────────────────────

export interface SearchObservationsInput {
  query?: string;
  type?: ObservationType;
  project?: string;       // Filter by specific project
  days?: number;          // Recency filter
  limit?: number;
  includeArchived?: boolean;
}

/**
 * Search observations by text match and filters.
 * Uses simple CONTAINS matching on name and content.
 */
export async function searchObservations(
  dbKey: string,
  input: SearchObservationsInput,
): Promise<ObservationSearchResult[]> {
  const limit = input.limit || 10;
  const conditions: string[] = [];

  if (!input.includeArchived) {
    conditions.push('o.archived = false');
  }

  if (input.type) {
    conditions.push(`o.type = '${esc(input.type)}'`);
  }

  if (input.project) {
    conditions.push(`o.project = '${esc(input.project)}'`);
  }

  if (input.days) {
    const cutoff = new Date();
    cutoff.setDate(cutoff.getDate() - input.days);
    conditions.push(`o.createdAt >= '${cutoff.toISOString()}'`);
  }

  if (input.query) {
    const q = esc(input.query.toLowerCase());
    conditions.push(`(lower(o.name) CONTAINS '${q}' OR lower(o.content) CONTAINS '${q}' OR lower(o.tags) CONTAINS '${q}')`);
  }

  const where = conditions.length > 0 ? `WHERE ${conditions.join(' AND ')}` : '';

  const rows = await memoryQuery(dbKey, `
    MATCH (o:Observation) ${where}
    RETURN o
    ORDER BY o.createdAt DESC
    LIMIT ${limit}
  `);

  const results: ObservationSearchResult[] = [];
  for (const row of rows) {
    const obs = rowToObservation(row);
    const refs = await getObservationRefs(dbKey, obs.uid);
    results.push({
      observation: obs,
      refs,
      source: obs.project === GLOBAL_PROJECT_NAME ? 'global' : obs.project,
    });
  }

  return results;
}

// ─── Get by ID ───────────────────────────────────────────────────────

/**
 * Get a single observation by UID
 */
export async function getObservation(
  dbKey: string,
  uid: string,
): Promise<ObservationSearchResult | null> {
  const rows = await memoryQuery(dbKey, `
    MATCH (o:Observation {uid: '${esc(uid)}'})
    RETURN o
  `);

  if (rows.length === 0) return null;

  const obs = rowToObservation(rows[0]);
  const refs = await getObservationRefs(dbKey, uid);
  return {
    observation: obs,
    refs,
    source: obs.project === GLOBAL_PROJECT_NAME ? 'global' : obs.project,
  };
}

// ─── Get Refs ────────────────────────────────────────────────────────

/**
 * Get all refs linked to an observation
 */
export async function getObservationRefs(
  dbKey: string,
  observationUid: string,
): Promise<ObservationRef[]> {
  const rows = await memoryQuery(dbKey, `
    MATCH (r:ObservationRef {observationUid: '${esc(observationUid)}'})
    RETURN r
  `);

  return rows.map(rowToRef);
}

// ─── List Recent ─────────────────────────────────────────────────────

/**
 * List recent observations (for context injection in hooks)
 */
export async function listRecentObservations(
  dbKey: string,
  opts?: { limit?: number; project?: string; days?: number },
): Promise<Observation[]> {
  const limit = opts?.limit || 5;
  const conditions: string[] = ['o.archived = false'];

  if (opts?.project) {
    conditions.push(`o.project = '${esc(opts.project)}'`);
  }

  if (opts?.days) {
    const cutoff = new Date();
    cutoff.setDate(cutoff.getDate() - opts.days);
    conditions.push(`o.createdAt >= '${cutoff.toISOString()}'`);
  }

  const where = conditions.length > 0 ? `WHERE ${conditions.join(' AND ')}` : '';

  const rows = await memoryQuery(dbKey, `
    MATCH (o:Observation) ${where}
    RETURN o
    ORDER BY o.createdAt DESC
    LIMIT ${limit}
  `);

  return rows.map(rowToObservation);
}

// ─── List by Type ────────────────────────────────────────────────────

/**
 * List observations filtered by type (for CLI commands like `codeindex learnings`)
 */
export async function listByType(
  dbKey: string,
  type: ObservationType,
  opts?: { project?: string; limit?: number },
): Promise<ObservationSearchResult[]> {
  return searchObservations(dbKey, {
    type,
    project: opts?.project,
    limit: opts?.limit || 20,
  });
}

// ─── Archive (soft delete) ───────────────────────────────────────────

/**
 * Archive an observation (soft delete)
 */
export async function archiveObservation(
  dbKey: string,
  uid: string,
): Promise<boolean> {
  const rows = await memoryQuery(dbKey, `
    MATCH (o:Observation {uid: '${esc(uid)}'})
    SET o.archived = true, o.updatedAt = '${new Date().toISOString()}'
    RETURN o.uid
  `);
  return rows.length > 0;
}

// ─── Delete (permanent) ─────────────────────────────────────────────

/**
 * Permanently delete an observation and its refs
 */
export async function deleteObservation(
  dbKey: string,
  uid: string,
): Promise<boolean> {
  // Delete refs first (KuzuDB requires deleting edges before nodes)
  await memoryQuery(dbKey, `
    MATCH (o:Observation {uid: '${esc(uid)}'})-[rel:ObservationRelation]->(r:ObservationRef)
    DELETE rel
  `);

  await memoryQuery(dbKey, `
    MATCH (r:ObservationRef {observationUid: '${esc(uid)}'})
    DELETE r
  `);

  // Delete supersedes edges
  await memoryQuery(dbKey, `
    MATCH (o:Observation {uid: '${esc(uid)}'})-[rel:ObservationRelation]->()
    DELETE rel
  `);

  await memoryQuery(dbKey, `
    MATCH ()-[rel:ObservationRelation]->(o:Observation {uid: '${esc(uid)}'})
    DELETE rel
  `);

  // Delete the observation node
  const rows = await memoryQuery(dbKey, `
    MATCH (o:Observation {uid: '${esc(uid)}'})
    DELETE o
    RETURN count(o) as deleted
  `);

  return rows.length > 0;
}

// ─── Supersede ───────────────────────────────────────────────────────

/**
 * Mark an observation as superseded by a newer one
 */
export async function supersedeObservation(
  dbKey: string,
  oldUid: string,
  newUid: string,
): Promise<void> {
  await memoryQuery(dbKey, `
    MATCH (old:Observation {uid: '${esc(oldUid)}'})
    MATCH (new:Observation {uid: '${esc(newUid)}'})
    CREATE (new)-[:ObservationRelation {type: 'SUPERSEDES'}]->(old)
  `);

  // Archive the old observation
  await archiveObservation(dbKey, oldUid);
}

// ─── Count ───────────────────────────────────────────────────────────

/**
 * Count observations in a memory database
 */
export async function countObservations(
  dbKey: string,
  opts?: { project?: string; type?: ObservationType },
): Promise<number> {
  const conditions: string[] = ['o.archived = false'];
  if (opts?.project) conditions.push(`o.project = '${esc(opts.project)}'`);
  if (opts?.type) conditions.push(`o.type = '${esc(opts.type)}'`);

  const where = conditions.length > 0 ? `WHERE ${conditions.join(' AND ')}` : '';
  const rows = await memoryQuery(dbKey, `
    MATCH (o:Observation) ${where}
    RETURN count(o) as cnt
  `);

  return rows[0]?.cnt || 0;
}

// ─── Hybrid Search (Text + Semantic) ─────────────────────────────────

const RRF_K = 60;

/**
 * Hybrid search combining text CONTAINS matching with semantic vector search.
 * Uses Reciprocal Rank Fusion (RRF) to merge rankings.
 * Falls back to text-only if embedder is not available.
 */
export async function hybridSearchObservations(
  dbKey: string,
  input: SearchObservationsInput,
): Promise<ObservationSearchResult[]> {
  const limit = input.limit || 10;
  const query = input.query;

  if (!query) {
    // No query text — just use regular search (recent observations)
    return searchObservations(dbKey, input);
  }

  // 1. Text search (CONTAINS)
  const textResults = await searchObservations(dbKey, { ...input, limit: limit * 2 });

  // 2. Semantic search (vector)
  let semanticUids: Array<{ uid: string; score: number }> = [];
  try {
    const { semanticSearchObservations } = await import('./observation-embedder.js');
    semanticUids = await semanticSearchObservations(dbKey, query, limit * 2);
  } catch {
    // Embedder not available — return text-only results
    return textResults;
  }

  if (semanticUids.length === 0) {
    return textResults;
  }

  // 3. RRF merge
  const merged = new Map<string, { result: ObservationSearchResult; score: number }>();

  // Add text results with RRF scores
  for (let i = 0; i < textResults.length; i++) {
    const uid = textResults[i].observation.uid;
    merged.set(uid, {
      result: textResults[i],
      score: 1 / (RRF_K + i + 1),
    });
  }

  // Add semantic results with RRF scores
  for (let i = 0; i < semanticUids.length; i++) {
    const uid = semanticUids[i].uid;
    const existing = merged.get(uid);
    const rrfScore = 1 / (RRF_K + i + 1);

    if (existing) {
      // Found by both methods — boost score
      existing.score += rrfScore;
    } else {
      // Only found by semantic — need to fetch the observation
      try {
        const obs = await getObservation(dbKey, uid);
        if (obs) {
          merged.set(uid, { result: obs, score: rrfScore });
        }
      } catch {
        // Skip if fetch fails
      }
    }
  }

  // Sort by combined RRF score and return top results
  return Array.from(merged.values())
    .sort((a, b) => b.score - a.score)
    .slice(0, limit)
    .map(entry => {
      entry.result.score = entry.score;
      return entry.result;
    });
}
