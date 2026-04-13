/**
 * Memory LadybugDB Adapter (Read-Write)
 *
 * Manages LadybugDB instances for memory storage.
 * Unlike the MCP code graph adapter (read-only), this opens in read-write mode
 * so the MCP server can create/update/delete observations.
 *
 * Supports multiple databases:
 * - One per indexed project (stored at ~/.codeindex/{ProjectName}/memory)
 * - One global (stored at ~/.codeindex/_global/memory)
 */

import fs from 'fs/promises';
import path from 'path';
import lbug from '@ladybugdb/core';
import { MEMORY_SCHEMA_QUERIES } from './schema.js';

interface MemoryDbEntry {
  db: lbug.Database;
  conn: lbug.Connection;
  dbPath: string;
  lastUsed: number;
}

const pool = new Map<string, MemoryDbEntry>();

/** Max memory databases open at once */
const MAX_POOL_SIZE = 6;

/**
 * Silence stdout during lbug operations.
 * LadybugDB's native module writes to stdout which corrupts MCP stdio.
 */
function silenced<T>(fn: () => T): T {
  const origWrite = process.stdout.write;
  process.stdout.write = (() => true) as any;
  try {
    return fn();
  } finally {
    process.stdout.write = origWrite;
  }
}

/**
 * Close a single memory database
 */
function closeOne(key: string): void {
  const entry = pool.get(key);
  if (!entry) return;
  try { entry.conn.close(); } catch {}
  try { entry.db.close(); } catch {}
  pool.delete(key);
}

/**
 * Evict least-recently-used entry if at capacity
 */
function evictLRU(): void {
  if (pool.size < MAX_POOL_SIZE) return;
  let oldestKey: string | null = null;
  let oldestTime = Infinity;
  for (const [key, entry] of pool) {
    if (entry.lastUsed < oldestTime) {
      oldestTime = entry.lastUsed;
      oldestKey = key;
    }
  }
  if (oldestKey) closeOne(oldestKey);
}

/**
 * Initialize or get an existing memory database.
 * Creates the database and schema if it doesn't exist yet.
 *
 * @param key - Unique identifier (project name or '_global')
 * @param dbPath - Path to the LadybugDB database file
 */
export async function initMemoryDb(key: string, dbPath: string): Promise<void> {
  const existing = pool.get(key);
  if (existing) {
    existing.lastUsed = Date.now();
    return;
  }

  evictLRU();

  // Ensure parent directory exists
  const parentDir = path.dirname(dbPath);
  await fs.mkdir(parentDir, { recursive: true });

  // Handle stale directory from older LadybugDB versions
  try {
    const stat = await fs.stat(dbPath);
    if (stat.isDirectory()) {
      await fs.rm(dbPath, { recursive: true, force: true });
    }
  } catch {
    // Path doesn't exist — fine, LadybugDB will create it
  }

  // ALWAYS remove WAL/lock files before opening — stale WAL files from previous
  // sessions or incompatible kuzu versions cause native crashes that bypass try/catch.
  for (const ext of ['.wal', '.lock']) {
    try { await fs.unlink(dbPath + ext); } catch {}
  }

  // Check magic bytes BEFORE opening — LadybugDB native module crashes the process
  // on incompatible files with an uncaught exception that bypasses try/catch.
  // Valid LadybugDB 0.15+ files start with "LBUG" (0x4C 0x42 0x55 0x47).
  try {
    const stat = await fs.stat(dbPath);
    if (stat.isFile() && stat.size > 0) {
      const fd = await fs.open(dbPath, 'r');
      const buf = Buffer.alloc(4);
      await fd.read(buf, 0, 4, 0);
      await fd.close();
      const magic = buf.toString('ascii');
      if (magic !== 'LBUG') {
        console.warn(`Memory DB at ${dbPath} has wrong magic "${magic}" (expected "LBUG"), deleting...`);
        await fs.unlink(dbPath);
      }
    }
  } catch {
    // File doesn't exist — LadybugDB will create it
  }

  // Open database with retry — if WAL/wal corruption is detected during schema
  // setup, close everything, nuke WAL files, and retry once from scratch.
  let db: lbug.Database;
  let conn: lbug.Connection;
  let fatalError = false;

  const openAndSetup = async (): Promise<boolean> => {
    db = silenced(() => new lbug.Database(dbPath));
    conn = silenced(() => new lbug.Connection(db));

    for (const query of MEMORY_SCHEMA_QUERIES) {
      try {
        await conn.query(query);
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        if (msg.includes('already exists')) continue;
        if (msg.includes('wal') || msg.includes('WAL') || msg.includes('Mmap') || msg.includes('Corrupted')) {
          return false; // signal retry needed
        }
        // Non-fatal schema warning — continue
      }
    }
    return true;
  };

  let ok = await openAndSetup();
  if (!ok) {
    // Close broken database, nuke WAL, retry once
    try { conn!.close(); } catch {}
    try { db!.close(); } catch {}
    for (const ext of ['.wal', '.lock']) {
      try { await fs.unlink(dbPath + ext); } catch {}
    }
    ok = await openAndSetup();
    if (!ok) {
      fatalError = true;
      console.error(`Memory DB for "${key}" has persistent WAL corruption — skipping`);
      try { conn!.close(); } catch {}
      try { db!.close(); } catch {}
      return;
    }
  }

  pool.set(key, { db: db!, conn: conn!, dbPath, lastUsed: Date.now() });
}

/**
 * Execute a query on a memory database.
 * Automatically initializes the DB if not open yet.
 */
export async function memoryQuery(key: string, cypher: string, params?: Record<string, any>): Promise<any[]> {
  const entry = pool.get(key);
  if (!entry) {
    throw new Error(`Memory DB not initialized for "${key}". Call initMemoryDb first.`);
  }
  entry.lastUsed = Date.now();

  try {
    const queryResult = await entry.conn.query(cypher);
    const result = Array.isArray(queryResult) ? queryResult[0] : queryResult;
    const rows = await result.getAll();
    return rows;
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    // Rethrow with context
    throw new Error(`Memory query failed [${key}]: ${msg}\nQuery: ${cypher.slice(0, 200)}`);
  }
}

/**
 * Check if a memory database is initialized
 */
export function isMemoryDbReady(key: string): boolean {
  return pool.has(key);
}

/**
 * Get the database path for a memory database key
 */
export function getMemoryDbPath(key: string): string | undefined {
  return pool.get(key)?.dbPath;
}

/**
 * Close a specific memory database or all of them
 */
export async function closeMemoryDb(key?: string): Promise<void> {
  if (key) {
    closeOne(key);
    return;
  }
  for (const id of [...pool.keys()]) {
    closeOne(id);
  }
}
