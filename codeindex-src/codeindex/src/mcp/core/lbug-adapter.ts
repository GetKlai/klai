/**
 * LadybugDB Adapter (Connection Pool)
 *
 * Manages a pool of LadybugDB databases keyed by repoId, each with
 * multiple Connection objects for safe concurrent query execution.
 *
 * LadybugDB Connections are NOT thread-safe — a single Connection
 * segfaults if concurrent .query() calls hit it simultaneously.
 * This adapter provides a checkout/return connection pool so each
 * concurrent query gets its own Connection from the same Database.
 *
 * Multiple Connections from the same Database is the officially
 * supported concurrency pattern.
 */

import fs from 'fs/promises';
import lbug from '@ladybugdb/core';

/** Per-repo pool: one Database, many Connections */
interface PoolEntry {
  db: lbug.Database;
  /** Available connections ready for checkout */
  available: lbug.Connection[];
  /** Number of connections currently checked out */
  checkedOut: number;
  /** Queued waiters for when all connections are busy */
  waiters: Array<(conn: lbug.Connection) => void>;
  lastUsed: number;
  dbPath: string;
}

const pool = new Map<string, PoolEntry>();

/** Max repos in the pool (LRU eviction) */
const MAX_POOL_SIZE = 5;
/** Idle timeout before closing a repo's connections */
const IDLE_TIMEOUT_MS = 5 * 60 * 1000; // 5 minutes
/** Max connections per repo (caps concurrent queries per repo) */
const MAX_CONNS_PER_REPO = 8;

let idleTimer: ReturnType<typeof setInterval> | null = null;

/** Saved real stdout.write — used to silence LadybugDB native output without race conditions */
export const realStdoutWrite = process.stdout.write.bind(process.stdout);
let stdoutSilenceCount = 0;
/** True while pre-warming connections — prevents watchdog from prematurely restoring stdout */
let preWarmActive = false;

/**
 * Start the idle cleanup timer (runs every 60s)
 */
function ensureIdleTimer(): void {
  if (idleTimer) return;
  idleTimer = setInterval(() => {
    const now = Date.now();
    for (const [repoId, entry] of pool) {
      if (now - entry.lastUsed > IDLE_TIMEOUT_MS) {
        closeOne(repoId);
      }
    }
  }, 60_000);
  if (idleTimer && typeof idleTimer === 'object' && 'unref' in idleTimer) {
    (idleTimer as NodeJS.Timeout).unref();
  }
}

/**
 * Evict the least-recently-used repo if pool is at capacity
 */
function evictLRU(): void {
  if (pool.size < MAX_POOL_SIZE) return;

  let oldestId: string | null = null;
  let oldestTime = Infinity;
  for (const [id, entry] of pool) {
    if (entry.lastUsed < oldestTime) {
      oldestTime = entry.lastUsed;
      oldestId = id;
    }
  }
  if (oldestId) {
    closeOne(oldestId);
  }
}

/**
 * Close all connections for a repo and remove it from the pool
 */
function closeOne(repoId: string): void {
  const entry = pool.get(repoId);
  if (!entry) return;
  for (const conn of entry.available) {
    try { conn.close(); } catch {}
  }
  try { entry.db.close(); } catch {}
  pool.delete(repoId);
}

function silenceStdout(): void {
  stdoutSilenceCount++;
  if (stdoutSilenceCount === 1) {
    process.stdout.write = (() => true) as any;
  }
}

function restoreStdout(): void {
  stdoutSilenceCount--;
  if (stdoutSilenceCount <= 0) {
    stdoutSilenceCount = 0;
    process.stdout.write = realStdoutWrite;
  }
}

// Safety watchdog: restore stdout if it gets stuck silenced (e.g. native crash
// inside createConnection before restoreStdout runs).
setInterval(() => {
  if (stdoutSilenceCount > 0 && !preWarmActive) {
    stdoutSilenceCount = 0;
    process.stdout.write = realStdoutWrite;
  }
}, 1000).unref();

/**
 * Create a new Connection from a repo's Database.
 * Silences stdout to prevent native module output from corrupting MCP stdio.
 */
function createConnection(db: lbug.Database): lbug.Connection {
  silenceStdout();
  try {
    return new lbug.Connection(db);
  } finally {
    restoreStdout();
  }
}

const LOCK_RETRY_ATTEMPTS = 3;
const LOCK_RETRY_DELAY_MS = 2000;

/** Deduplicates concurrent initKuzu calls for the same repoId */
const initPromises = new Map<string, Promise<void>>();

/**
 * Initialize (or reuse) a Database + connection pool for a specific repo.
 * Retries on lock errors (e.g., when `codeindex analyze` is running).
 *
 * Concurrent calls for the same repoId are deduplicated — the second caller
 * awaits the first's in-progress init rather than starting a redundant one.
 */
export const initKuzu = async (repoId: string, dbPath: string): Promise<void> => {
  const existing = pool.get(repoId);
  if (existing) {
    existing.lastUsed = Date.now();
    return;
  }

  // Deduplicate concurrent init calls for the same repoId
  const pending = initPromises.get(repoId);
  if (pending) return pending;

  const promise = doInitKuzu(repoId, dbPath);
  initPromises.set(repoId, promise);
  try {
    await promise;
  } finally {
    initPromises.delete(repoId);
  }
};

/**
 * Internal init — creates DB, pre-warms connections, loads FTS, then registers pool.
 * Pool entry is registered LAST so concurrent executeQuery calls see either
 * "not initialized" (and throw) or a fully ready pool — never a half-built one.
 */
async function doInitKuzu(repoId: string, dbPath: string): Promise<void> {
  // Check if database exists
  try {
    await fs.stat(dbPath);
  } catch {
    throw new Error(`LadybugDB not found at ${dbPath}. Run: codeindex analyze`);
  }

  evictLRU();

  // Open in read-only mode — MCP server never writes to the database.
  // This allows multiple MCP server instances to read concurrently, and
  // avoids lock conflicts when `codeindex analyze` is writing.
  let lastError: Error | null = null;
  let db: lbug.Database | null = null;
  for (let attempt = 1; attempt <= LOCK_RETRY_ATTEMPTS; attempt++) {
    silenceStdout();
    try {
      db = new lbug.Database(
        dbPath,
        0,     // bufferManagerSize (default)
        false, // enableCompression (default)
        true,  // readOnly
      );
      restoreStdout();
      break;
    } catch (err: any) {
      restoreStdout();
      lastError = err instanceof Error ? err : new Error(String(err));
      const isLockError = lastError.message.includes('Could not set lock')
        || lastError.message.includes('lock');
      if (!isLockError || attempt === LOCK_RETRY_ATTEMPTS) break;
      await new Promise(resolve => setTimeout(resolve, LOCK_RETRY_DELAY_MS * attempt));
    }
  }

  if (!db) {
    throw new Error(
      `LadybugDB unavailable for ${repoId}. Another process may be rebuilding the index. ` +
      `Retry later. (${lastError?.message || 'unknown error'})`
    );
  }

  // Pre-create the full pool upfront so createConnection() (which silences
  // stdout) is never called lazily during active query execution.
  preWarmActive = true;
  const available: lbug.Connection[] = [];
  try {
    for (let i = 0; i < MAX_CONNS_PER_REPO; i++) {
      available.push(createConnection(db));
    }
  } finally {
    preWarmActive = false;
  }

  // Load FTS extension once per Database.
  // Done BEFORE pool registration so no concurrent checkout can grab
  // the connection while the async FTS load is in progress.
  try {
    await available[0].query('LOAD EXTENSION fts');
  } catch {
    // Extension may not be installed — FTS queries will fail gracefully
  }

  // Register pool entry only after all connections are pre-warmed and FTS is
  // loaded. Concurrent executeQuery calls see either "not initialized"
  // (and throw cleanly) or a fully ready pool — never a half-built one.
  pool.set(repoId, { db, available, checkedOut: 0, waiters: [], lastUsed: Date.now(), dbPath });
  ensureIdleTimer();
}

/**
 * Checkout a connection from the pool.
 * Returns an available connection, or creates a new one if under the cap.
 * If all connections are busy and at cap, queues the caller until one is returned.
 */
function checkout(entry: PoolEntry): Promise<lbug.Connection> {
  // Fast path: grab an available connection
  if (entry.available.length > 0) {
    entry.checkedOut++;
    return Promise.resolve(entry.available.pop()!);
  }

  // Pool was pre-warmed to MAX_CONNS_PER_REPO during init. If we're here
  // with fewer total connections, something leaked — surface the bug rather
  // than silently creating a connection (which would silence stdout mid-query).
  const totalConns = entry.available.length + entry.checkedOut;
  if (totalConns < MAX_CONNS_PER_REPO) {
    throw new Error(
      `Connection pool integrity error: expected ${MAX_CONNS_PER_REPO} ` +
      `connections but found ${totalConns} (${entry.available.length} available, ` +
      `${entry.checkedOut} checked out)`
    );
  }

  // At capacity — queue the caller with a timeout. checkin() will resolve
  // this when a connection is returned, handing it directly to the next waiter.
  return new Promise<lbug.Connection>(resolve => {
    entry.waiters.push(resolve);
  });
}

/**
 * Return a connection to the pool after use.
 * If there are queued waiters, hand the connection directly to the next one
 * instead of putting it back in the available array (avoids race conditions).
 */
function checkin(entry: PoolEntry, conn: lbug.Connection): void {
  if (entry.waiters.length > 0) {
    // Hand directly to the next waiter — no intermediate available state
    const waiter = entry.waiters.shift()!;
    waiter(conn);
  } else {
    entry.checkedOut--;
    entry.available.push(conn);
  }
}

/**
 * Execute a query on a specific repo's connection pool.
 * Automatically checks out a connection, runs the query, and returns it.
 */
export const executeQuery = async (repoId: string, cypher: string): Promise<any[]> => {
  const entry = pool.get(repoId);
  if (!entry) {
    throw new Error(`LadybugDB not initialized for repo "${repoId}". Call initKuzu first.`);
  }

  entry.lastUsed = Date.now();

  const conn = await checkout(entry);
  try {
    const queryResult = await conn.query(cypher);
    const result = Array.isArray(queryResult) ? queryResult[0] : queryResult;
    const rows = await result.getAll();
    return rows;
  } finally {
    checkin(entry, conn);
  }
};

/**
 * Close one or all repo pools.
 * If repoId is provided, close only that repo's connections.
 * If omitted, close all repos.
 */
export const closeKuzu = async (repoId?: string): Promise<void> => {
  if (repoId) {
    closeOne(repoId);
    return;
  }

  for (const id of [...pool.keys()]) {
    closeOne(id);
  }

  if (idleTimer) {
    clearInterval(idleTimer);
    idleTimer = null;
  }
};

/**
 * Check if a specific repo's pool is active
 */
export const isKuzuReady = (repoId: string): boolean => pool.has(repoId);
