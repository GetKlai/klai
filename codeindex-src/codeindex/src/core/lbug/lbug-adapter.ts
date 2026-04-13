import fs from 'fs/promises';
import { createReadStream } from 'fs';
import { createInterface } from 'readline';
import path from 'path';
import lbug from '@ladybugdb/core';
import { KnowledgeGraph, GraphNode, GraphRelationship, NodeLabel, RelationshipType } from '../graph/types.js';
import { createKnowledgeGraph } from '../graph/graph.js';
import {
  NODE_TABLES,
  REL_TABLE_NAME,
  SCHEMA_QUERIES,
  EMBEDDING_TABLE_NAME,
  NodeTableName,
} from './schema.js';
import { streamAllCSVsToDisk, escapeCSVField, escapeCSVNumber } from './csv-generator.js';

let db: lbug.Database | null = null;
let conn: lbug.Connection | null = null;
let currentDbPath: string | null = null;
let ftsLoaded = false;
let dbReadOnly = false;

// Global session lock for operations that touch module-level lbug globals.
// This guarantees no DB switch can happen while an operation is running.
let sessionLock: Promise<void> = Promise.resolve();

const runWithSessionLock = async <T>(operation: () => Promise<T>): Promise<T> => {
  const previous = sessionLock;
  let release: (() => void) | null = null;
  sessionLock = new Promise<void>(resolve => {
    release = resolve;
  });

  await previous;
  try {
    return await operation();
  } finally {
    release?.();
  }
};

const normalizeCopyPath = (filePath: string): string => filePath.replace(/\\/g, '/');

export const initKuzu = async (dbPath: string, options?: { readOnly?: boolean }) => {
  return runWithSessionLock(() => ensureKuzuInitialized(dbPath, options?.readOnly));
};

/**
 * Execute multiple queries against one repo DB atomically.
 * While the callback runs, no other request can switch the active DB.
 */
export const withKuzuDb = async <T>(dbPath: string, operation: () => Promise<T>, options?: { readOnly?: boolean }): Promise<T> => {
  return runWithSessionLock(async () => {
    await ensureKuzuInitialized(dbPath, options?.readOnly);
    return operation();
  });
};

const ensureKuzuInitialized = async (dbPath: string, readOnly?: boolean) => {
  if (conn && currentDbPath === dbPath) {
    return { db, conn };
  }
  await doInitKuzu(dbPath, readOnly);
  return { db, conn };
};

const isLockError = (msg: string): boolean =>
  msg.toLowerCase().includes('could not set lock') || msg.toLowerCase().includes('lock on file');

const doInitKuzu = async (dbPath: string, readOnly?: boolean) => {
  // Different database requested — close the old one first
  if (conn || db) {
    try { if (conn) await conn.close(); } catch {}
    try { if (db) await db.close(); } catch {}
    conn = null;
    db = null;
    currentDbPath = null;
    ftsLoaded = false;
    dbReadOnly = false;
  }

  // LadybugDB stores the database as a single file (not a directory).
  // If the path already exists, it must be a valid LadybugDB database file.
  // Remove stale empty directories or files from older versions.
  if (!readOnly) {
    try {
      const stat = await fs.stat(dbPath);
      if (stat.isDirectory()) {
        const files = await fs.readdir(dbPath);
        if (files.length === 0) {
          await fs.rmdir(dbPath);
        } else {
          await fs.rm(dbPath, { recursive: true, force: true });
        }
      }
    } catch {
      // Path doesn't exist, which is what LadybugDB wants for a new database
    }
  }

  // Ensure parent directory exists
  const parentDir = path.dirname(dbPath);
  await fs.mkdir(parentDir, { recursive: true });

  if (readOnly) {
    db = new lbug.Database(
      dbPath,
      0,     // bufferManagerSize (default)
      false, // enableCompression (default)
      true,  // readOnly
    );
    conn = new lbug.Connection(db);
    dbReadOnly = true;
    currentDbPath = dbPath;
    return { db, conn };
  }

  db = new lbug.Database(dbPath);
  conn = new lbug.Connection(db);
  dbReadOnly = false;

  let lockErrorDetected = false;
  const schemaWarnings = new Map<string, number>();
  for (const schemaQuery of SCHEMA_QUERIES) {
    try {
      await conn.query(schemaQuery);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      if (msg.includes('already exists')) continue;
      if (isLockError(msg)) {
        lockErrorDetected = true;
        break;
      }
      const key = msg.slice(0, 120);
      schemaWarnings.set(key, (schemaWarnings.get(key) || 0) + 1);
    }
  }
  if (lockErrorDetected) {
    // Close the write connection and throw — caller should retry or abort
    try { await conn.close(); } catch {}
    try { await db.close(); } catch {}
    conn = null;
    db = null;
    const error = new Error(
      'DATABASE_LOCKED: Another process (probably the MCP server) has the database open. ' +
      'Retrying...'
    );
    (error as any).code = 'DATABASE_LOCKED';
    throw error;
  }
  for (const [msg, count] of schemaWarnings) {
    console.warn(`⚠️ Schema warning: ${msg}${count > 1 ? ` (×${count})` : ''}`);
  }

  // ── Format compatibility check ──────────────────────────────────
  // Detect databases created by an older/incompatible kuzu version.
  // If the file is large (>1MB) but contains 0 nodes, the format is
  // incompatible and needs re-indexing.
  try {
    const stat = await fs.stat(dbPath);
    if (stat.isFile() && stat.size > 1_000_000) {
      const countResult = await conn.query('MATCH (n) RETURN count(n) AS cnt');
      const rows = Array.isArray(countResult) ? await countResult[0].getAll() : await countResult.getAll();
      const nodeCount = rows[0]?.cnt ?? 0;
      if (nodeCount === 0) {
        // Large file but empty graph = incompatible format from older kuzu version
        console.warn(
          `⚠️  Incompatible database format detected at ${dbPath} ` +
          `(${(stat.size / 1_000_000).toFixed(1)}MB file, 0 nodes). ` +
          `This database was created by an older version and needs re-indexing.`
        );
        // Close and remove the incompatible file so a fresh one can be created
        try { await conn.close(); } catch {}
        try { await db.close(); } catch {}
        conn = null;
        db = null;
        await fs.unlink(dbPath);
        const error = new Error(
          `INCOMPATIBLE_DB_FORMAT: Database at ${dbPath} uses an older format. ` +
          `Re-index with: codeindex analyze <ProjectName> <repoPath>`
        );
        (error as any).code = 'INCOMPATIBLE_DB_FORMAT';
        (error as any).dbPath = dbPath;
        throw error;
      }
    }
  } catch (err) {
    if ((err as any)?.code === 'INCOMPATIBLE_DB_FORMAT') throw err;
    // Non-fatal: stat or count query failed, continue normally
  }

  currentDbPath = dbPath;
  return { db, conn };
};

export type KuzuProgressCallback = (message: string) => void;

export const loadGraphToKuzu = async (
  graph: KnowledgeGraph,
  repoPath: string,
  storagePath: string,
  onProgress?: KuzuProgressCallback
) => {
  if (!conn) {
    throw new Error('LadybugDB not initialized. Call initKuzu first.');
  }

  const log = onProgress || (() => {});

  const csvDir = path.join(storagePath, 'csv');

  log('Streaming CSVs to disk...');
  const csvResult = await streamAllCSVsToDisk(graph, repoPath, csvDir);

  const validTables = new Set<string>(NODE_TABLES as readonly string[]);
  const getNodeLabel = (nodeId: string): string => {
    if (nodeId.startsWith('comm_')) return 'Community';
    if (nodeId.startsWith('proc_')) return 'Process';
    return nodeId.split(':')[0];
  };

  // Bulk COPY all node CSVs (sequential — LadybugDB allows only one write txn at a time)
  const nodeFiles = [...csvResult.nodeFiles.entries()];
  const totalSteps = nodeFiles.length + 1; // +1 for relationships
  let stepsDone = 0;

  for (const [table, { csvPath, rows }] of nodeFiles) {
    stepsDone++;
    log(`Loading nodes ${stepsDone}/${totalSteps}: ${table} (${rows.toLocaleString()} rows)`);

    const normalizedPath = normalizeCopyPath(csvPath);
    const copyQuery = getCopyQuery(table, normalizedPath);

    try {
      await conn.query(copyQuery);
    } catch (err: any) {
      const errMsg = err instanceof Error ? err.message : String(err);
      if (isLockError(errMsg)) {
        const lockErr = new Error('DATABASE_LOCKED: Cannot write — database is locked by another process.');
        (lockErr as any).code = 'DATABASE_LOCKED';
        throw lockErr;
      }
      try {
        const retryQuery = copyQuery.replace('auto_detect=false)', 'auto_detect=false, IGNORE_ERRORS=true)');
        await conn.query(retryQuery);
      } catch (retryErr) {
        const retryMsg = retryErr instanceof Error ? retryErr.message : String(retryErr);
        if (isLockError(retryMsg)) {
          const lockErr = new Error('DATABASE_LOCKED: Cannot write — database is locked by another process.');
          (lockErr as any).code = 'DATABASE_LOCKED';
          throw lockErr;
        }
        throw new Error(`COPY failed for ${table}: ${retryMsg.slice(0, 200)}`);
      }
    }
  }

  // Bulk COPY relationships — split by FROM→TO label pair (LadybugDB requires it)
  // Stream-read the relation CSV line by line to avoid exceeding V8 max string length
  let relHeader = '';
  const relsByPair = new Map<string, string[]>();
  let skippedRels = 0;
  let totalValidRels = 0;

  await new Promise<void>((resolve, reject) => {
    const rl = createInterface({ input: createReadStream(csvResult.relCsvPath, 'utf-8'), crlfDelay: Infinity });
    let isFirst = true;
    rl.on('line', (line) => {
      if (isFirst) { relHeader = line; isFirst = false; return; }
      if (!line.trim()) return;
      const match = line.match(/"([^"]*)","([^"]*)"/);
      if (!match) { skippedRels++; return; }
      const fromLabel = getNodeLabel(match[1]);
      const toLabel = getNodeLabel(match[2]);
      if (!validTables.has(fromLabel) || !validTables.has(toLabel)) {
        skippedRels++;
        return;
      }
      const pairKey = `${fromLabel}|${toLabel}`;
      let list = relsByPair.get(pairKey);
      if (!list) { list = []; relsByPair.set(pairKey, list); }
      list.push(line);
      totalValidRels++;
    });
    rl.on('close', resolve);
    rl.on('error', reject);
  });

  const insertedRels = totalValidRels;
  const warnings: string[] = [];
  if (insertedRels > 0) {

    log(`Loading edges: ${insertedRels.toLocaleString()} across ${relsByPair.size} types`);

    let pairIdx = 0;
    let failedPairEdges = 0;
    const failedPairLines: string[] = [];

    for (const [pairKey, lines] of relsByPair) {
      pairIdx++;
      const [fromLabel, toLabel] = pairKey.split('|');
      const pairCsvPath = path.join(csvDir, `rel_${fromLabel}_${toLabel}.csv`);
      await fs.writeFile(pairCsvPath, relHeader + '\n' + lines.join('\n'), 'utf-8');
      const normalizedPath = normalizeCopyPath(pairCsvPath);
      const copyQuery = `COPY ${REL_TABLE_NAME} FROM "${normalizedPath}" (from="${fromLabel}", to="${toLabel}", HEADER=true, ESCAPE='"', DELIM=',', QUOTE='"', PARALLEL=false, auto_detect=false)`;

      if (pairIdx % 5 === 0 || lines.length > 1000) {
        log(`Loading edges: ${pairIdx}/${relsByPair.size} types (${fromLabel} -> ${toLabel})`);
      }

      try {
        await conn.query(copyQuery);
      } catch (err) {
        try {
          const retryQuery = copyQuery.replace('auto_detect=false)', 'auto_detect=false, IGNORE_ERRORS=true)');
          await conn.query(retryQuery);
        } catch (retryErr) {
          const retryMsg = retryErr instanceof Error ? retryErr.message : String(retryErr);
          warnings.push(`${fromLabel}->${toLabel} (${lines.length} edges): ${retryMsg.slice(0, 80)}`);
          failedPairEdges += lines.length;
          failedPairLines.push(...lines);
        }
      }
      try { await fs.unlink(pairCsvPath); } catch {}
    }

    if (failedPairLines.length > 0) {
      log(`Inserting ${failedPairEdges} edges individually (missing schema pairs)`);
      await fallbackRelationshipInserts([relHeader, ...failedPairLines], validTables, getNodeLabel);
    }
  }

  // Cleanup all CSVs
  try { await fs.unlink(csvResult.relCsvPath); } catch {}
  for (const [, { csvPath }] of csvResult.nodeFiles) {
    try { await fs.unlink(csvPath); } catch {}
  }
  try {
    const remaining = await fs.readdir(csvDir);
    for (const f of remaining) {
      try { await fs.unlink(path.join(csvDir, f)); } catch {}
    }
  } catch {}
  try { await fs.rmdir(csvDir); } catch {}

  return { success: true, insertedRels, skippedRels, warnings };
};

// LadybugDB default ESCAPE is '\' (backslash), but our CSV uses RFC 4180 escaping ("" for literal quotes).
// Source code content is full of backslashes which confuse the auto-detection.
// We MUST explicitly set ESCAPE='"' to use RFC 4180 escaping, and disable auto_detect to prevent
// LadybugDB from overriding our settings based on sample rows.
const COPY_CSV_OPTS = `(HEADER=true, ESCAPE='"', DELIM=',', QUOTE='"', PARALLEL=false, auto_detect=false)`;

// Multi-language table names that were created with backticks in CODE_ELEMENT_BASE
// and must always be referenced with backticks in queries
const BACKTICK_TABLES = new Set([
  'Struct', 'Enum', 'Macro', 'Typedef', 'Union', 'Namespace', 'Trait', 'Impl',
  'TypeAlias', 'Const', 'Static', 'Property', 'Record', 'Delegate', 'Annotation',
  'Constructor', 'Template', 'Module',
]);

const escapeTableName = (table: string): string => {
  return BACKTICK_TABLES.has(table) ? `\`${table}\`` : table;
};

/** Fallback: insert relationships one-by-one if COPY fails */
const fallbackRelationshipInserts = async (
  validRelLines: string[],
  validTables: Set<string>,
  getNodeLabel: (id: string) => string
) => {
  if (!conn) return;
  const escapeLabel = (label: string): string => {
    return BACKTICK_TABLES.has(label) ? `\`${label}\`` : label;
  };

  for (let i = 1; i < validRelLines.length; i++) {
    const line = validRelLines[i];
    try {
      const match = line.match(/"([^"]*)","([^"]*)","([^"]*)",([0-9.]+),"([^"]*)",([0-9-]+)/);
      if (!match) continue;
      const [, fromId, toId, relType, confidenceStr, reason, stepStr] = match;
      const fromLabel = getNodeLabel(fromId);
      const toLabel = getNodeLabel(toId);
      if (!validTables.has(fromLabel) || !validTables.has(toLabel)) continue;

      const confidence = parseFloat(confidenceStr) || 1.0;
      const step = parseInt(stepStr) || 0;

      await conn.query(`
        MATCH (a:${escapeLabel(fromLabel)} {id: '${fromId.replace(/'/g, "''")}' }),
              (b:${escapeLabel(toLabel)} {id: '${toId.replace(/'/g, "''")}' })
        CREATE (a)-[:${REL_TABLE_NAME} {type: '${relType}', confidence: ${confidence}, reason: '${reason.replace(/'/g, "''")}', step: ${step}}]->(b)
      `);
    } catch {
      // skip
    }
  }
};

/** Tables with isExported column (TypeScript/JS-native types) */
const TABLES_WITH_EXPORTED = new Set<string>(['Function', 'Class', 'Interface', 'Method', 'CodeElement']);

const getCopyQuery = (table: NodeTableName, filePath: string): string => {
  const t = escapeTableName(table);
  if (table === 'File') {
    return `COPY ${t}(id, name, filePath, content) FROM "${filePath}" ${COPY_CSV_OPTS}`;
  }
  if (table === 'Folder') {
    return `COPY ${t}(id, name, filePath) FROM "${filePath}" ${COPY_CSV_OPTS}`;
  }
  if (table === 'Community') {
    return `COPY ${t}(id, label, heuristicLabel, keywords, description, enrichedBy, cohesion, symbolCount) FROM "${filePath}" ${COPY_CSV_OPTS}`;
  }
  if (table === 'Process') {
    return `COPY ${t}(id, label, heuristicLabel, processType, stepCount, communities, entryPointId, terminalId) FROM "${filePath}" ${COPY_CSV_OPTS}`;
  }
  // TypeScript/JS code element tables have isExported; multi-language tables do not
  if (TABLES_WITH_EXPORTED.has(table)) {
    return `COPY ${t}(id, name, filePath, startLine, endLine, isExported, content, description) FROM "${filePath}" ${COPY_CSV_OPTS}`;
  }
  // Multi-language tables (Struct, Impl, Trait, Macro, etc.)
  return `COPY ${t}(id, name, filePath, startLine, endLine, content, description) FROM "${filePath}" ${COPY_CSV_OPTS}`;
};

/**
 * Insert a single node to LadybugDB
 * @param label - Node type (File, Function, Class, etc.)
 * @param properties - Node properties
 * @param dbPath - Path to LadybugDB database (optional if already initialized)
 */
export const insertNodeToKuzu = async (
  label: string,
  properties: Record<string, any>,
  dbPath?: string
): Promise<boolean> => {
  // Use provided dbPath or fall back to module-level db
  const targetDbPath = dbPath || (db ? undefined : null);
  if (!targetDbPath && !db) {
    throw new Error('LadybugDB not initialized. Provide dbPath or call initKuzu first.');
  }

  try {
    const escapeValue = (v: any): string => {
      if (v === null || v === undefined) return 'NULL';
      if (typeof v === 'number') return String(v);
      // Escape backslashes first (for Windows paths), then single quotes
      return `'${String(v).replace(/\\/g, '\\\\').replace(/'/g, "''")}'`;
    };

    // Build INSERT query based on node type
    const t = escapeTableName(label);
    let query: string;

    if (label === 'File') {
      query = `CREATE (n:File {id: ${escapeValue(properties.id)}, name: ${escapeValue(properties.name)}, filePath: ${escapeValue(properties.filePath)}, content: ${escapeValue(properties.content || '')}})`;
    } else if (label === 'Folder') {
      query = `CREATE (n:Folder {id: ${escapeValue(properties.id)}, name: ${escapeValue(properties.name)}, filePath: ${escapeValue(properties.filePath)}})`;
    } else if (TABLES_WITH_EXPORTED.has(label)) {
      const descPart = properties.description ? `, description: ${escapeValue(properties.description)}` : '';
      query = `CREATE (n:${t} {id: ${escapeValue(properties.id)}, name: ${escapeValue(properties.name)}, filePath: ${escapeValue(properties.filePath)}, startLine: ${properties.startLine || 0}, endLine: ${properties.endLine || 0}, isExported: ${!!properties.isExported}, content: ${escapeValue(properties.content || '')}${descPart}})`;
    } else {
      // Multi-language tables (Struct, Impl, Trait, Macro, etc.) — no isExported
      const descPart = properties.description ? `, description: ${escapeValue(properties.description)}` : '';
      query = `CREATE (n:${t} {id: ${escapeValue(properties.id)}, name: ${escapeValue(properties.name)}, filePath: ${escapeValue(properties.filePath)}, startLine: ${properties.startLine || 0}, endLine: ${properties.endLine || 0}, content: ${escapeValue(properties.content || '')}${descPart}})`;
    }
    
    // Use per-query connection if dbPath provided (avoids lock conflicts)
    if (targetDbPath) {
      const tempDb = new lbug.Database(targetDbPath);
      const tempConn = new lbug.Connection(tempDb);
      try {
        await tempConn.query(query);
        return true;
      } finally {
        try { await tempConn.close(); } catch {}
        try { await tempDb.close(); } catch {}
      }
    } else if (conn) {
      // Use existing persistent connection (when called from analyze)
      await conn.query(query);
      return true;
    }
    
    return false;
  } catch (e: any) {
    // Node may already exist or other error
    console.error(`Failed to insert ${label} node:`, e.message);
    return false;
  }
};

/**
 * Batch insert multiple nodes to LadybugDB using a single connection
 * @param nodes - Array of {label, properties} to insert
 * @param dbPath - Path to LadybugDB database
 * @returns Object with success count and error count
 */
export const batchInsertNodesToKuzu = async (
  nodes: Array<{ label: string; properties: Record<string, any> }>,
  dbPath: string
): Promise<{ inserted: number; failed: number }> => {
  if (nodes.length === 0) return { inserted: 0, failed: 0 };

  const escapeValue = (v: any): string => {
    if (v === null || v === undefined) return 'NULL';
    if (typeof v === 'number') return String(v);
    // Escape backslashes first (for Windows paths), then single quotes
    return `'${String(v).replace(/\\/g, '\\\\').replace(/'/g, "''")}'`;
  };

  // Use the global connection if available — opening a second connection to the
  // same LadybugDB file corrupts the first and causes native segfaults.
  const useGlobal = conn !== null && currentDbPath === dbPath;
  let tempDb: lbug.Database | null = null;
  let tempConn: lbug.Connection | null = null;
  const targetConn = useGlobal ? conn! : (() => {
    tempDb = new lbug.Database(dbPath);
    tempConn = new lbug.Connection(tempDb);
    return tempConn;
  })();

  let inserted = 0;
  let failed = 0;

  try {
    for (const { label, properties } of nodes) {
      try {
        let query: string;

        // Use MERGE instead of CREATE for upsert behavior (handles duplicates gracefully)
        const t = escapeTableName(label);
        if (label === 'File') {
          query = `MERGE (n:File {id: ${escapeValue(properties.id)}}) SET n.name = ${escapeValue(properties.name)}, n.filePath = ${escapeValue(properties.filePath)}, n.content = ${escapeValue(properties.content || '')}`;
        } else if (label === 'Folder') {
          query = `MERGE (n:Folder {id: ${escapeValue(properties.id)}}) SET n.name = ${escapeValue(properties.name)}, n.filePath = ${escapeValue(properties.filePath)}`;
        } else if (TABLES_WITH_EXPORTED.has(label)) {
          const descPart = properties.description ? `, n.description = ${escapeValue(properties.description)}` : '';
          query = `MERGE (n:${t} {id: ${escapeValue(properties.id)}}) SET n.name = ${escapeValue(properties.name)}, n.filePath = ${escapeValue(properties.filePath)}, n.startLine = ${properties.startLine || 0}, n.endLine = ${properties.endLine || 0}, n.isExported = ${!!properties.isExported}, n.content = ${escapeValue(properties.content || '')}${descPart}`;
        } else {
          const descPart = properties.description ? `, n.description = ${escapeValue(properties.description)}` : '';
          query = `MERGE (n:${t} {id: ${escapeValue(properties.id)}}) SET n.name = ${escapeValue(properties.name)}, n.filePath = ${escapeValue(properties.filePath)}, n.startLine = ${properties.startLine || 0}, n.endLine = ${properties.endLine || 0}, n.content = ${escapeValue(properties.content || '')}${descPart}`;
        }

        await targetConn.query(query);
        inserted++;
      } catch (e: any) {
        // Don't console.error here - it corrupts MCP JSON-RPC on stderr
        failed++;
      }
    }
  } finally {
    // Only close temp connections — never close the global one
    if (tempConn) {
      try { await tempConn.close(); } catch {}
    }
    if (tempDb) {
      try { await tempDb.close(); } catch {}
    }
  }

  return { inserted, failed };
};

export const executeQuery = async (cypher: string): Promise<any[]> => {
  if (!conn) {
    throw new Error('LadybugDB not initialized. Call initKuzu first.');
  }

  const queryResult = await conn.query(cypher);
  // LadybugDB uses getAll() instead of hasNext()/getNext()
  // Query returns QueryResult for single queries, QueryResult[] for multi-statement
  const result = Array.isArray(queryResult) ? queryResult[0] : queryResult;
  const rows = await result.getAll();
  return rows;
};

export const executeWithReusedStatement = async (
  cypher: string,
  paramsList: Array<Record<string, any>>
): Promise<void> => {
  if (!conn) {
    throw new Error('LadybugDB not initialized. Call initKuzu first.');
  }
  if (paramsList.length === 0) return;

  const SUB_BATCH_SIZE = 4;
  for (let i = 0; i < paramsList.length; i += SUB_BATCH_SIZE) {
    const subBatch = paramsList.slice(i, i + SUB_BATCH_SIZE);
    const stmt = await conn.prepare(cypher);
    if (!stmt.isSuccess()) {
      const errMsg = await stmt.getErrorMessage();
      throw new Error(`Prepare failed: ${errMsg}`);
    }
    try {
      for (const params of subBatch) {
        await conn.execute(stmt, params);
      }
    } catch (e) {
      // Log the error and continue with next batch
      console.warn('Batch execution error:', e);
    }
    // Note: LadybugDB PreparedStatement doesn't require explicit close()
  }
};

export const getKuzuStats = async (): Promise<{ nodes: number; edges: number }> => {
  if (!conn) return { nodes: 0, edges: 0 };

  let totalNodes = 0;
  for (const tableName of NODE_TABLES) {
    try {
      const queryResult = await conn.query(`MATCH (n:${escapeTableName(tableName)}) RETURN count(n) AS cnt`);
      const nodeResult = Array.isArray(queryResult) ? queryResult[0] : queryResult;
      const nodeRows = await nodeResult.getAll();
      if (nodeRows.length > 0) {
        totalNodes += Number(nodeRows[0]?.cnt ?? nodeRows[0]?.[0] ?? 0);
      }
    } catch {
      // ignore
    }
  }

  let totalEdges = 0;
  try {
    const queryResult = await conn.query(`MATCH ()-[r:${REL_TABLE_NAME}]->() RETURN count(r) AS cnt`);
    const edgeResult = Array.isArray(queryResult) ? queryResult[0] : queryResult;
    const edgeRows = await edgeResult.getAll();
    if (edgeRows.length > 0) {
      totalEdges = Number(edgeRows[0]?.cnt ?? edgeRows[0]?.[0] ?? 0);
    }
  } catch {
    // ignore
  }

  return { nodes: totalNodes, edges: totalEdges };
};

/**
 * Load cached embeddings from LadybugDB before a rebuild.
 * Returns all embedding vectors so they can be re-inserted after the graph is reloaded,
 * avoiding expensive re-embedding of unchanged nodes.
 */
export const loadCachedEmbeddings = async (): Promise<{
  embeddingNodeIds: Set<string>;
  embeddings: Array<{ nodeId: string; embedding: number[] }>;
}> => {
  if (!conn) {
    return { embeddingNodeIds: new Set(), embeddings: [] };
  }

  const embeddingNodeIds = new Set<string>();
  const embeddings: Array<{ nodeId: string; embedding: number[] }> = [];
  try {
    const rows = await conn.query(`MATCH (e:${EMBEDDING_TABLE_NAME}) RETURN e.nodeId AS nodeId, e.embedding AS embedding`);
    const result = Array.isArray(rows) ? rows[0] : rows;
    for (const row of await result.getAll()) {
      const nodeId = String(row.nodeId ?? row[0] ?? '');
      if (!nodeId) continue;
      embeddingNodeIds.add(nodeId);
      const embedding = row.embedding ?? row[1];
      if (embedding) {
        embeddings.push({
          nodeId,
          embedding: Array.isArray(embedding) ? embedding.map(Number) : Array.from(embedding as any).map(Number),
        });
      }
    }
  } catch { /* embedding table may not exist */ }

  return { embeddingNodeIds, embeddings };
};

/**
 * Lightweight query: get only the nodeIds that already have embeddings.
 * Unlike loadCachedEmbeddings(), this does NOT load the actual vectors,
 * making it ~100x faster for incremental updates (ms instead of seconds).
 */
export const queryEmbeddingNodeIds = async (): Promise<Set<string>> => {
  if (!conn) return new Set();
  try {
    const queryResult = await conn.query(
      `MATCH (e:${EMBEDDING_TABLE_NAME}) RETURN e.nodeId AS nodeId`
    );
    const result = Array.isArray(queryResult) ? queryResult[0] : queryResult;
    const rows = await result.getAll();
    return new Set(rows.map((r: any) => String(r.nodeId ?? r[0] ?? '')));
  } catch {
    return new Set();
  }
};

export const closeKuzu = async (): Promise<void> => {
  if (conn) {
    try {
      await conn.close();
    } catch {}
    conn = null;
  }
  if (db) {
    try {
      await db.close();
    } catch {}
    db = null;
  }
  currentDbPath = null;
  ftsLoaded = false;
};

export const isKuzuReady = (): boolean => conn !== null && db !== null;

/**
 * Delete all nodes (and their relationships) for a specific file from LadybugDB
 * @param filePath - The file path to delete nodes for
 * @param dbPath - Optional path to LadybugDB for per-query connection
 * @returns Object with counts of deleted nodes
 */
export const deleteNodesForFile = async (filePath: string, dbPath?: string): Promise<{ deletedNodes: number }> => {
  const usePerQuery = !!dbPath;
  
  // Set up connection (either use existing or create per-query)
  let tempDb: lbug.Database | null = null;
  let tempConn: lbug.Connection | null = null;
  let targetConn: lbug.Connection | null = conn;
  
  if (usePerQuery) {
    tempDb = new lbug.Database(dbPath);
    tempConn = new lbug.Connection(tempDb);
    targetConn = tempConn;
  } else if (!conn) {
    throw new Error('LadybugDB not initialized. Provide dbPath or call initKuzu first.');
  }
  
  try {
    let deletedNodes = 0;
    const escapedPath = filePath.replace(/'/g, "''");

    // Collect node IDs before deletion so we can clean up embeddings
    const nodeIdsToDelete: string[] = [];

    // Delete nodes from each table that has filePath
    // DETACH DELETE removes the node and all its relationships
    for (const tableName of NODE_TABLES) {
      // Skip tables that don't have filePath (Community, Process)
      if (tableName === 'Community' || tableName === 'Process') continue;

      try {
        const tn = escapeTableName(tableName);
        // Collect node IDs first (needed for embedding cleanup)
        const idResult = await targetConn!.query(
          `MATCH (n:${tn}) WHERE n.filePath = '${escapedPath}' RETURN n.id AS id`
        );
        const idQueryResult = Array.isArray(idResult) ? idResult[0] : idResult;
        const idRows = await idQueryResult.getAll();
        const count = idRows.length;

        if (count > 0) {
          for (const row of idRows as any[]) {
            const id = String(row.id ?? row[0] ?? '');
            if (id) nodeIdsToDelete.push(id);
          }
          // Delete nodes (and implicitly their relationships via DETACH)
          await targetConn!.query(
            `MATCH (n:${tn}) WHERE n.filePath = '${escapedPath}' DETACH DELETE n`
          );
          deletedNodes += count;
        }
      } catch (e) {
        // Some tables may not support this query, skip
      }
    }

    // Delete embeddings by exact nodeId match (nodeIds are "Label:filePath:name")
    if (nodeIdsToDelete.length > 0) {
      for (const nodeId of nodeIdsToDelete) {
        try {
          const escapedNodeId = nodeId.replace(/'/g, "''");
          await targetConn!.query(
            `MATCH (e:${EMBEDDING_TABLE_NAME}) WHERE e.nodeId = '${escapedNodeId}' DELETE e`
          );
        } catch {
          // Embedding may not exist for this node
        }
      }
    }

    return { deletedNodes };
  } finally {
    // Close per-query connection if used
    if (tempConn) {
      try { await tempConn.close(); } catch {}
    }
    if (tempDb) {
      try { await tempDb.close(); } catch {}
    }
  }
};

/**
 * Batch delete all nodes (and their relationships) for multiple files from LadybugDB.
 * Uses a single query per table with WHERE filePath IN [...] instead of per-file queries.
 * For 182 files this reduces ~6500 queries to ~18 queries.
 */
export const deleteNodesForFiles = async (filePaths: string[]): Promise<{ deletedNodes: number }> => {
  if (!conn) throw new Error('LadybugDB not initialized. Call initKuzu first.');
  if (filePaths.length === 0) return { deletedNodes: 0 };

  let deletedNodes = 0;
  const nodeIdsToDelete: string[] = [];

  // Build the IN list once — LadybugDB uses list syntax for IN
  const escapedPaths = filePaths.map(p => `'${p.replace(/'/g, "''")}'`);
  const pathList = `[${escapedPaths.join(',')}]`;

  // Delete nodes from each table that has filePath
  for (const tableName of NODE_TABLES) {
    if (tableName === 'Community' || tableName === 'Process') continue;

    try {
      const tn = escapeTableName(tableName);

      // Collect node IDs first (for embedding cleanup)
      const idResult = await conn.query(
        `MATCH (n:${tn}) WHERE n.filePath IN ${pathList} RETURN n.id AS id`
      );
      const idQueryResult = Array.isArray(idResult) ? idResult[0] : idResult;
      const idRows = await idQueryResult.getAll();
      const count = idRows.length;

      if (count > 0) {
        for (const row of idRows as any[]) {
          const id = String(row.id ?? row[0] ?? '');
          if (id) nodeIdsToDelete.push(id);
        }
        await conn.query(
          `MATCH (n:${tn}) WHERE n.filePath IN ${pathList} DETACH DELETE n`
        );
        deletedNodes += count;
      }
    } catch {
      // Table may not exist or be empty
    }
  }

  // Batch delete embeddings for removed nodes
  if (nodeIdsToDelete.length > 0) {
    // LadybugDB IN with large lists can be slow, so chunk to avoid query size limits
    const CHUNK = 500;
    for (let i = 0; i < nodeIdsToDelete.length; i += CHUNK) {
      const chunk = nodeIdsToDelete.slice(i, i + CHUNK);
      const idList = `[${chunk.map(id => `'${id.replace(/'/g, "''")}'`).join(',')}]`;
      try {
        await conn.query(
          `MATCH (e:${EMBEDDING_TABLE_NAME}) WHERE e.nodeId IN ${idList} DELETE e`
        );
      } catch {
        // Embedding may not exist
      }
    }
  }

  return { deletedNodes };
};

/**
 * Batch insert nodes via CSV COPY instead of individual MERGE queries.
 * Groups nodes by label, writes temp CSVs, and uses COPY FROM for bulk loading.
 * For 3019 nodes this reduces 3019 queries to ~10 COPY operations.
 */
export const batchInsertNodesViaCSV = async (
  nodes: Array<{ label: string; properties: Record<string, any> }>,
  csvDir: string,
): Promise<{ inserted: number; failed: number }> => {
  if (!conn) throw new Error('LadybugDB not initialized. Call initKuzu first.');
  if (nodes.length === 0) return { inserted: 0, failed: 0 };

  await fs.mkdir(csvDir, { recursive: true });

  // Group nodes by label
  const byLabel = new Map<string, Array<Record<string, any>>>();
  for (const { label, properties } of nodes) {
    let group = byLabel.get(label);
    if (!group) { group = []; byLabel.set(label, group); }
    group.push(properties);
  }

  let inserted = 0;
  let failed = 0;

  for (const [label, props] of byLabel) {
    const csvPath = path.join(csvDir, `inc_${label.toLowerCase()}.csv`);

    try {
      // Build CSV content based on label type
      let header: string;
      let rowFn: (p: Record<string, any>) => string;

      if (label === 'File') {
        header = 'id,name,filePath,content';
        rowFn = (p) => [
          escapeCSVField(p.id), escapeCSVField(p.name),
          escapeCSVField(p.filePath), escapeCSVField(p.content || ''),
        ].join(',');
      } else if (label === 'Folder') {
        header = 'id,name,filePath';
        rowFn = (p) => [
          escapeCSVField(p.id), escapeCSVField(p.name), escapeCSVField(p.filePath),
        ].join(',');
      } else if (TABLES_WITH_EXPORTED.has(label)) {
        header = 'id,name,filePath,startLine,endLine,isExported,content,description';
        rowFn = (p) => [
          escapeCSVField(p.id), escapeCSVField(p.name), escapeCSVField(p.filePath),
          escapeCSVNumber(p.startLine, 0), escapeCSVNumber(p.endLine, 0),
          p.isExported ? 'true' : 'false',
          escapeCSVField(p.content || ''), escapeCSVField(p.description || ''),
        ].join(',');
      } else {
        // Multi-language tables (no isExported)
        header = 'id,name,filePath,startLine,endLine,content,description';
        rowFn = (p) => [
          escapeCSVField(p.id), escapeCSVField(p.name), escapeCSVField(p.filePath),
          escapeCSVNumber(p.startLine, 0), escapeCSVNumber(p.endLine, 0),
          escapeCSVField(p.content || ''), escapeCSVField(p.description || ''),
        ].join(',');
      }

      // Write CSV
      const rows = props.map(rowFn);
      await fs.writeFile(csvPath, header + '\n' + rows.join('\n'), 'utf-8');

      // COPY FROM
      const normalizedPath = csvPath.replace(/\\/g, '/');
      const t = escapeTableName(label);
      const copyOpts = `(HEADER=true, ESCAPE='"', DELIM=',', QUOTE='"', PARALLEL=false, auto_detect=false)`;

      let copyQuery: string;
      if (label === 'File') {
        copyQuery = `COPY ${t}(id, name, filePath, content) FROM "${normalizedPath}" ${copyOpts}`;
      } else if (label === 'Folder') {
        copyQuery = `COPY ${t}(id, name, filePath) FROM "${normalizedPath}" ${copyOpts}`;
      } else if (TABLES_WITH_EXPORTED.has(label)) {
        copyQuery = `COPY ${t}(id, name, filePath, startLine, endLine, isExported, content, description) FROM "${normalizedPath}" ${copyOpts}`;
      } else {
        copyQuery = `COPY ${t}(id, name, filePath, startLine, endLine, content, description) FROM "${normalizedPath}" ${copyOpts}`;
      }

      try {
        await conn.query(copyQuery);
        inserted += props.length;
      } catch (copyErr: any) {
        const copyMsg = copyErr instanceof Error ? copyErr.message : String(copyErr);
        if (isLockError(copyMsg)) {
          const err = new Error('DATABASE_LOCKED: Cannot write — database is locked by another process.');
          (err as any).code = 'DATABASE_LOCKED';
          throw err;
        }
        // Retry with IGNORE_ERRORS
        try {
          const retryQuery = copyQuery.replace('auto_detect=false)', 'auto_detect=false, IGNORE_ERRORS=true)');
          await conn.query(retryQuery);
          inserted += props.length; // approximate — some may have been skipped
        } catch (retryErr: any) {
          const retryMsg = retryErr instanceof Error ? retryErr.message : String(retryErr);
          if (isLockError(retryMsg)) {
            const err = new Error('DATABASE_LOCKED: Cannot write — database is locked by another process.');
            (err as any).code = 'DATABASE_LOCKED';
            throw err;
          }
          // Fall back to individual inserts for this label group
          for (const p of props) {
            try {
              await insertNodeToKuzu(label, p);
              inserted++;
            } catch {
              failed++;
            }
          }
        }
      }
    } finally {
      try { await fs.unlink(csvPath); } catch {}
    }
  }

  return { inserted, failed };
};

/**
 * Batch insert relationships via CSV COPY instead of individual INSERT queries.
 * Groups by (fromLabel, toLabel) pair, writes temp CSVs, and uses COPY FROM.
 * For 6416 edges this reduces 6416 queries to ~30 COPY operations.
 */
export const batchInsertRelationshipsViaCSV = async (
  rels: RelationshipInput[],
  csvDir: string,
): Promise<{ inserted: number; failed: number }> => {
  if (!conn) throw new Error('LadybugDB not initialized. Call initKuzu first.');
  if (rels.length === 0) return { inserted: 0, failed: 0 };

  await fs.mkdir(csvDir, { recursive: true });

  // Group by (fromLabel, toLabel) — LadybugDB COPY requires this
  const byPair = new Map<string, RelationshipInput[]>();
  for (const rel of rels) {
    const key = `${rel.fromLabel}|${rel.toLabel}`;
    let group = byPair.get(key);
    if (!group) { group = []; byPair.set(key, group); }
    group.push(rel);
  }

  let inserted = 0;
  let failed = 0;
  const header = 'from,to,type,confidence,reason,step';

  for (const [pairKey, pairRels] of byPair) {
    const [fromLabel, toLabel] = pairKey.split('|');
    const csvPath = path.join(csvDir, `inc_rel_${fromLabel}_${toLabel}.csv`);

    try {
      const rows = pairRels.map(rel => [
        escapeCSVField(rel.fromId),
        escapeCSVField(rel.toId),
        escapeCSVField(rel.type),
        escapeCSVNumber(rel.confidence, 1.0),
        escapeCSVField(rel.reason || ''),
        escapeCSVNumber(rel.step, 0),
      ].join(','));

      await fs.writeFile(csvPath, header + '\n' + rows.join('\n'), 'utf-8');

      const normalizedPath = csvPath.replace(/\\/g, '/');
      const copyQuery = `COPY ${REL_TABLE_NAME} FROM "${normalizedPath}" (from="${fromLabel}", to="${toLabel}", HEADER=true, ESCAPE='"', DELIM=',', QUOTE='"', PARALLEL=false, auto_detect=false)`;

      try {
        await conn.query(copyQuery);
        inserted += pairRels.length;
      } catch {
        // Retry with IGNORE_ERRORS
        try {
          const retryQuery = copyQuery.replace('auto_detect=false)', 'auto_detect=false, IGNORE_ERRORS=true)');
          await conn.query(retryQuery);
          inserted += pairRels.length;
        } catch {
          // Fall back to individual inserts for this pair
          for (const rel of pairRels) {
            const ok = await insertRelationship(
              rel.fromId, rel.fromLabel, rel.toId, rel.toLabel,
              rel.type, rel.confidence, rel.reason, rel.step,
            );
            if (ok) inserted++;
            else failed++;
          }
        }
      }
    } finally {
      try { await fs.unlink(csvPath); } catch {}
    }
  }

  return { inserted, failed };
};

export const getEmbeddingTableName = (): string => EMBEDDING_TABLE_NAME;

// ============================================================================
// Full-Text Search (FTS) Functions
// ============================================================================

/**
 * Load the FTS extension (required before using FTS functions).
 * Safe to call multiple times — tracks loaded state.
 */
export const loadFTSExtension = async (): Promise<void> => {
  if (ftsLoaded) return;
  if (!conn) {
    throw new Error('LadybugDB not initialized. Call initKuzu first.');
  }
  if (ftsLoaded) return;
  try {
    await conn.query('INSTALL fts');
    await conn.query('LOAD EXTENSION fts');
    ftsLoaded = true;
  } catch {
    // Extension may already be loaded
    ftsLoaded = true;
  }
  try {
    await conn.query('INSTALL vector');
    await conn.query('LOAD EXTENSION vector');
  } catch {
    // Vector extension may already be loaded
  }
  ftsLoaded = true;
};

/**
 * Create a full-text search index on a table
 * @param tableName - The node table name (e.g., 'File', 'CodeSymbol')
 * @param indexName - Name for the FTS index
 * @param properties - List of properties to index (e.g., ['name', 'code'])
 * @param stemmer - Stemming algorithm (default: 'porter')
 */
export const createFTSIndex = async (
  tableName: string,
  indexName: string,
  properties: string[],
  stemmer: string = 'porter'
): Promise<void> => {
  if (!conn) {
    throw new Error('LadybugDB not initialized. Call initKuzu first.');
  }

  await loadFTSExtension();

  const propList = properties.map(p => `'${p}'`).join(', ');
  const query = `CALL CREATE_FTS_INDEX('${tableName}', '${indexName}', [${propList}], stemmer := '${stemmer}')`;

  try {
    await conn.query(query);
  } catch (e: any) {
    if (!e.message?.includes('already exists')) {
      throw e;
    }
  }
};

/**
 * Query a full-text search index
 * @param tableName - The node table name
 * @param indexName - FTS index name
 * @param query - Search query string
 * @param limit - Maximum results
 * @param conjunctive - If true, all terms must match (AND); if false, any term matches (OR)
 * @returns Array of { node properties, score }
 */
export const queryFTS = async (
  tableName: string,
  indexName: string,
  query: string,
  limit: number = 20,
  conjunctive: boolean = false
): Promise<Array<{ nodeId: string; name: string; filePath: string; score: number; [key: string]: any }>> => {
  if (!conn) {
    throw new Error('LadybugDB not initialized. Call initKuzu first.');
  }
  
  // Escape single quotes in query
  const escapedQuery = query.replace(/'/g, "''");
  
  const cypher = `
    CALL QUERY_FTS_INDEX('${tableName}', '${indexName}', '${escapedQuery}', conjunctive := ${conjunctive})
    RETURN node, score
    ORDER BY score DESC
    LIMIT ${limit}
  `;
  
  try {
    const queryResult = await conn.query(cypher);
    const result = Array.isArray(queryResult) ? queryResult[0] : queryResult;
    const rows = await result.getAll();
    
    return rows.map((row: any) => {
      const node = row.node || row[0] || {};
      const score = row.score ?? row[1] ?? 0;
      return {
        nodeId: node.nodeId || node.id || '',
        name: node.name || '',
        filePath: node.filePath || '',
        score: typeof score === 'number' ? score : parseFloat(score) || 0,
        ...node,
      };
    });
  } catch (e: any) {
    // Return empty if index doesn't exist yet
    if (e.message?.includes('does not exist')) {
      return [];
    }
    throw e;
  }
};

/**
 * Drop an FTS index
 */
export const dropFTSIndex = async (tableName: string, indexName: string): Promise<void> => {
  if (!conn) {
    throw new Error('LadybugDB not initialized. Call initKuzu first.');
  }

  try {
    await conn.query(`CALL DROP_FTS_INDEX('${tableName}', '${indexName}')`);
  } catch {
    // Index may not exist
  }
};

// ============================================================================
// Incremental Update Helpers
// ============================================================================

export interface SymbolRow {
  id: string;
  name: string;
  filePath: string;
  label: string;
}

/**
 * Query all symbols from LadybugDB for symbol table reconstruction.
 * Returns (id, name, filePath, label) tuples from all code element tables.
 * Skips File, Folder, Community, Process tables (not code symbols).
 */
export const queryAllSymbols = async (): Promise<SymbolRow[]> => {
  if (!conn) throw new Error('LadybugDB not initialized. Call initKuzu first.');

  const symbolTables = NODE_TABLES.filter(
    t => t !== 'File' && t !== 'Folder' && t !== 'Community' && t !== 'Process'
  );

  const rows: SymbolRow[] = [];
  for (const table of symbolTables) {
    try {
      const tn = escapeTableName(table);
      const queryResult = await conn.query(
        `MATCH (n:${tn}) RETURN n.id AS id, n.name AS name, n.filePath AS filePath`
      );
      const result = Array.isArray(queryResult) ? queryResult[0] : queryResult;
      const tableRows = await result.getAll();
      for (const row of tableRows as any[]) {
        rows.push({
          id: String(row.id ?? row[0] ?? ''),
          name: String(row.name ?? row[1] ?? ''),
          filePath: String(row.filePath ?? row[2] ?? ''),
          label: table,
        });
      }
    } catch {
      // Table may not exist or be empty
    }
  }

  return rows;
};

export interface ImportEdgeRow {
  sourceId: string;
  targetId: string;
  reason: string;
}

/**
 * Query all IMPORTS relationship edges from LadybugDB.
 * Used to reconstruct the import map for incremental updates.
 */
export const queryImportEdges = async (): Promise<ImportEdgeRow[]> => {
  if (!conn) throw new Error('LadybugDB not initialized. Call initKuzu first.');

  const rows: ImportEdgeRow[] = [];

  // Query the single CodeRelation table filtering by type = 'IMPORTS'
  // We need to try all source→target table combos that can have IMPORTS
  const importSourceTables = NODE_TABLES.filter(t => t !== 'Community' && t !== 'Process');

  for (const fromTable of importSourceTables) {
    for (const toTable of importSourceTables) {
      try {
        const ft = escapeTableName(fromTable);
        const tt = escapeTableName(toTable);
        const queryResult = await conn.query(
          `MATCH (s:${ft})-[r:${REL_TABLE_NAME} {type: 'IMPORTS'}]->(t:${tt}) RETURN s.id AS sourceId, t.id AS targetId, r.reason AS reason`
        );
        const result = Array.isArray(queryResult) ? queryResult[0] : queryResult;
        const edgeRows = await result.getAll();
        for (const row of edgeRows as any[]) {
          rows.push({
            sourceId: String(row.sourceId ?? row[0] ?? ''),
            targetId: String(row.targetId ?? row[1] ?? ''),
            reason: String(row.reason ?? row[2] ?? ''),
          });
        }
      } catch {
        // Not all table combos have IMPORTS edges
      }
    }
  }

  return rows;
};

/**
 * Query all indexed file paths from LadybugDB.
 * Used to build import resolution context for incremental updates.
 */
export const queryAllFilePaths = async (): Promise<string[]> => {
  if (!conn) throw new Error('LadybugDB not initialized. Call initKuzu first.');

  try {
    const queryResult = await conn.query(
      `MATCH (f:File) RETURN f.filePath AS filePath`
    );
    const result = Array.isArray(queryResult) ? queryResult[0] : queryResult;
    const fileRows = await result.getAll();
    return fileRows.map((row: any) => row.filePath ?? row[0] ?? '');
  } catch {
    return [];
  }
};

/**
 * Insert a single relationship edge into LadybugDB.
 * Uses CREATE (not MERGE) since we delete old data first.
 */
export const insertRelationship = async (
  fromId: string,
  fromLabel: string,
  toId: string,
  toLabel: string,
  type: string,
  confidence: number,
  reason: string,
  step?: number,
): Promise<boolean> => {
  if (!conn) throw new Error('LadybugDB not initialized. Call initKuzu first.');

  const ft = escapeTableName(fromLabel);
  const tt = escapeTableName(toLabel);
  const escapedReason = reason.replace(/'/g, "''");
  const stepPart = step !== undefined ? `, step: ${step}` : '';

  try {
    await conn.query(
      `MATCH (s:${ft} {id: '${fromId.replace(/'/g, "''")}'}), (t:${tt} {id: '${toId.replace(/'/g, "''")}'})`
      + ` CREATE (s)-[:${REL_TABLE_NAME} {type: '${type}', confidence: ${confidence}, reason: '${escapedReason}'${stepPart}}]->(t)`
    );
    return true;
  } catch {
    return false;
  }
};

export interface RelationshipInput {
  fromId: string;
  fromLabel: string;
  toId: string;
  toLabel: string;
  type: string;
  confidence: number;
  reason: string;
  step?: number;
}

/**
 * Insert multiple relationships efficiently.
 * Groups by (fromLabel, toLabel) to batch queries where possible.
 */
export const batchInsertRelationships = async (
  rels: RelationshipInput[],
): Promise<{ inserted: number; failed: number }> => {
  if (!conn) throw new Error('LadybugDB not initialized. Call initKuzu first.');
  if (rels.length === 0) return { inserted: 0, failed: 0 };

  let inserted = 0;
  let failed = 0;

  for (const rel of rels) {
    const ok = await insertRelationship(
      rel.fromId, rel.fromLabel, rel.toId, rel.toLabel,
      rel.type, rel.confidence, rel.reason, rel.step,
    );
    if (ok) inserted++;
    else failed++;
  }

  return { inserted, failed };
};

/**
 * Delete all Community and Process nodes (and their edges) from LadybugDB.
 * Used before re-running community/process detection on the full graph.
 */
export const deleteCommunitiesAndProcesses = async (): Promise<{ deleted: number }> => {
  if (!conn) throw new Error('LadybugDB not initialized. Call initKuzu first.');

  let deleted = 0;

  for (const table of ['Community', 'Process']) {
    try {
      const countResult = await conn.query(
        `MATCH (n:${table}) RETURN count(n) AS cnt`
      );
      const result = Array.isArray(countResult) ? countResult[0] : countResult;
      const rows = await result.getAll();
      const count = Number(rows[0]?.cnt ?? rows[0]?.[0] ?? 0);

      if (count > 0) {
        await conn.query(`MATCH (n:${table}) DETACH DELETE n`);
        deleted += count;
      }
    } catch {
      // Table may not exist
    }
  }

  return { deleted };
};

/**
 * Build a lightweight in-memory KnowledgeGraph from LadybugDB.
 * Reads only symbol nodes (Function, Class, Method, etc.) and edges —
 * no file content or embeddings. Used for community/process recomputation
 * during incremental updates without a full pipeline rebuild.
 */
export const buildGraphFromKuzu = async (): Promise<KnowledgeGraph> => {
  if (!conn) throw new Error('LadybugDB not initialized. Call initKuzu first.');

  const graph = createKnowledgeGraph();

  // Load all nodes (skip Community/Process — we're about to regenerate those)
  const nodeTables = NODE_TABLES.filter(t => t !== 'Community' && t !== 'Process');
  for (const table of nodeTables) {
    try {
      const tn = escapeTableName(table);
      const queryResult = await conn.query(
        `MATCH (n:${tn}) RETURN n.id AS id, n.name AS name, n.filePath AS filePath`
      );
      const result = Array.isArray(queryResult) ? queryResult[0] : queryResult;
      const rows = await result.getAll();
      for (const row of rows as any[]) {
        graph.addNode({
          id: String(row.id ?? row[0] ?? ''),
          label: table as NodeLabel,
          properties: {
            name: String(row.name ?? row[1] ?? ''),
            filePath: String(row.filePath ?? row[2] ?? ''),
          },
        });
      }
    } catch {
      // Table may not exist or be empty
    }
  }

  // Load ALL edges with a single query (replaces 676 N×N table-combo queries).
  // Source/target labels are derived from node IDs (format: "Label:path:name").
  try {
    // LadybugDB requires typed endpoints for relationship queries, so we still need
    // per-table-pair queries. But we only query pairs that actually have edges.
    // First, get all distinct (fromLabel, toLabel) pairs from the graph nodes.
    const nodeLabelsInGraph = new Set<string>();
    graph.forEachNode(n => nodeLabelsInGraph.add(n.label));

    const tablePairs: Array<[string, string]> = [];
    for (const ft of nodeLabelsInGraph) {
      for (const tt of nodeLabelsInGraph) {
        tablePairs.push([ft, tt]);
      }
    }

    for (const [fromTable, toTable] of tablePairs) {
      try {
        const ft = escapeTableName(fromTable);
        const tt = escapeTableName(toTable);
        const queryResult = await conn.query(
          `MATCH (s:${ft})-[r:${REL_TABLE_NAME}]->(t:${tt}) RETURN s.id AS sid, t.id AS tid, r.type AS type, r.confidence AS conf, r.reason AS reason, r.step AS step`
        );
        const result = Array.isArray(queryResult) ? queryResult[0] : queryResult;
        const rows = await result.getAll();
        for (const row of rows as any[]) {
          const sid = String(row.sid ?? row[0] ?? '');
          const tid = String(row.tid ?? row[1] ?? '');
          const type = String(row.type ?? row[2] ?? 'CALLS') as RelationshipType;
          const conf = Number(row.conf ?? row[3] ?? 1.0);
          const reason = String(row.reason ?? row[4] ?? '');
          const step = row.step ?? row[5];
          graph.addRelationship({
            id: `${sid}_${type}_${tid}`,
            sourceId: sid,
            targetId: tid,
            type,
            confidence: conf,
            reason,
            ...(step !== undefined && step !== null && step !== -1 ? { step: Number(step) } : {}),
          });
        }
      } catch {
        // Not all table combos have edges
      }
    }
  } catch {
    // Edge loading failed — return graph with nodes only
  }

  return graph;
};
