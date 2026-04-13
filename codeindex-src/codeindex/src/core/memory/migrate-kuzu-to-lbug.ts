/**
 * Memory Database Migration: kuzu 0.11.x → LadybugDB 0.15.x
 *
 * Reads observations from old kuzu-format memory databases using CSV export,
 * then imports them into new LadybugDB-format databases.
 *
 * Run automatically on first app launch after update, or manually:
 *   node -e "require('./migrate-kuzu-to-lbug').migrateAllMemories()"
 */

import fs from 'fs/promises';
import { existsSync, mkdirSync, writeFileSync, unlinkSync } from 'fs';
import path from 'path';
import os from 'os';
import lbug from '@ladybugdb/core';
import { MEMORY_SCHEMA_QUERIES } from './schema.js';

interface Observation {
  uid: string;
  name: string;
  type: string;
  content: string;
  tags: string;
  project: string;
  createdAt: string;
  updatedAt: string;
  sessionId?: string;
  archived?: boolean;
}

function escCSV(s: string | undefined | null): string {
  if (!s) return '""';
  return '"' + String(s).replace(/"/g, '""') + '"';
}

/**
 * Check if a memory file needs migration (old kuzu format).
 * Returns true if the file exists but LadybugDB can't read it.
 */
export async function needsMigration(memoryPath: string): Promise<boolean> {
  try {
    await fs.stat(memoryPath);
  } catch {
    return false; // File doesn't exist, no migration needed
  }

  try {
    const db = new lbug.Database(memoryPath, 0, false, true);
    const conn = new lbug.Connection(db);
    const result = await conn.query('MATCH (o:Observation) RETURN count(o) AS cnt');
    const rows = Array.isArray(result) ? await result[0].getAll() : await result.getAll();
    conn.close();
    db.close();
    // If we can read it, no migration needed
    return false;
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    if (msg.includes('not a valid') || msg.includes('Unable to open')) {
      return true; // Incompatible format, needs migration
    }
    return false; // Other error (e.g., lock), don't migrate
  }
}

/**
 * Extract observations from old kuzu-format memory database.
 * Uses the kuzu npm package (0.11.x) which must be available.
 */
async function extractFromKuzu(memoryPath: string): Promise<Observation[]> {
  let kuzu: any;
  try {
    kuzu = require('kuzu');
  } catch {
    console.warn('kuzu package not available — cannot extract old memories');
    return [];
  }

  try {
    const db = new kuzu.Database(memoryPath, 0, false, true);
    const conn = new kuzu.Connection(db);
    const result = await conn.query(
      'MATCH (o:Observation) RETURN o.uid AS uid, o.name AS name, o.type AS type, ' +
      'o.content AS content, o.tags AS tags, o.project AS project, ' +
      'o.createdAt AS createdAt, o.updatedAt AS updatedAt ORDER BY o.createdAt DESC'
    );
    const rows = await result.getAll();
    conn.close();
    db.close();
    return rows as Observation[];
  } catch (err) {
    console.warn(`Failed to extract from ${memoryPath}: ${(err as Error).message}`);
    return [];
  }
}

/**
 * Import observations into new LadybugDB-format memory database.
 */
async function importToLbug(memoryPath: string, observations: Observation[]): Promise<number> {
  if (observations.length === 0) return 0;

  // Backup old file
  const backupPath = memoryPath + '.kuzu-backup';
  try {
    await fs.copyFile(memoryPath, backupPath);
  } catch {}

  // Remove stale WAL/lock files from old kuzu version
  for (const ext of ['.wal', '.lock']) {
    try { await fs.unlink(memoryPath + ext); } catch {}
  }

  // Remove old file and create new
  try { await fs.unlink(memoryPath); } catch {}

  const db = new lbug.Database(memoryPath);
  const conn = new lbug.Connection(db);

  // Apply schema
  for (const query of MEMORY_SCHEMA_QUERIES) {
    try { await conn.query(query); } catch {}
  }

  // Write CSV for bulk import
  const csvPath = memoryPath + '.migration.csv';
  const header = 'uid,name,type,content,tags,project,createdAt,updatedAt,sessionId,archived';
  const rows = observations.map(o => [
    escCSV(o.uid), escCSV(o.name), escCSV(o.type), escCSV(o.content),
    escCSV(o.tags || '[]'), escCSV(o.project || ''),
    escCSV(o.createdAt), escCSV(o.updatedAt || o.createdAt),
    escCSV(o.sessionId || ''), 'false',
  ].join(','));

  writeFileSync(csvPath, header + '\n' + rows.join('\n'), 'utf-8');

  let imported = 0;
  try {
    const normalizedPath = csvPath.replace(/\\/g, '/');
    await conn.query(
      `COPY Observation FROM "${normalizedPath}" (HEADER=true, ESCAPE='"', DELIM=',', QUOTE='"', auto_detect=false)`
    );
    imported = observations.length;
  } catch (err) {
    console.warn(`CSV import failed, falling back to individual inserts: ${(err as Error).message}`);
    // Fallback: insert one by one
    for (const o of observations) {
      const esc = (s: string) => s ? s.replace(/\\/g, '\\\\').replace(/'/g, "''").replace(/\n/g, ' ') : '';
      try {
        await conn.query(`CREATE (n:Observation {
          uid: '${esc(o.uid)}', name: '${esc(o.name)}', type: '${esc(o.type)}',
          content: '${esc(o.content)}', tags: '${esc(o.tags || '[]')}',
          project: '${esc(o.project || '')}', createdAt: '${esc(o.createdAt)}',
          updatedAt: '${esc(o.updatedAt || o.createdAt)}', sessionId: '', archived: false
        })`);
        imported++;
      } catch {}
    }
  }

  try { unlinkSync(csvPath); } catch {}
  conn.close();
  db.close();

  // Remove backup if successful
  if (imported === observations.length) {
    try { await fs.unlink(backupPath); } catch {}
  }

  return imported;
}

/**
 * Migrate a single project's memory database.
 * Returns { migrated: boolean, count: number }
 */
export async function migrateMemory(
  memoryPath: string,
  onProgress?: (msg: string) => void
): Promise<{ migrated: boolean; count: number }> {
  const log = onProgress || console.log;

  if (!(await needsMigration(memoryPath))) {
    return { migrated: false, count: 0 };
  }

  log(`Migrating memory: ${memoryPath}`);
  const observations = await extractFromKuzu(memoryPath);
  if (observations.length === 0) {
    log('No observations found in old database');
    return { migrated: true, count: 0 };
  }

  log(`Found ${observations.length} observations, importing...`);
  const imported = await importToLbug(memoryPath, observations);
  log(`Migrated ${imported}/${observations.length} observations`);

  return { migrated: true, count: imported };
}

/**
 * Migrate all project memory databases under ~/.codeindex/.
 * Call this on first app launch after update.
 */
export async function migrateAllMemories(
  onProgress?: (msg: string) => void
): Promise<{ total: number; migrated: number }> {
  const log = onProgress || console.log;
  const codeindexDir = path.join(os.homedir(), '.codeindex');

  let entries: string[];
  try {
    entries = await fs.readdir(codeindexDir);
  } catch {
    return { total: 0, migrated: 0 };
  }

  let total = 0;
  let migrated = 0;

  for (const entry of entries) {
    const memoryPath = path.join(codeindexDir, entry, 'memory');
    try {
      const result = await migrateMemory(memoryPath, onProgress);
      if (result.migrated) {
        total++;
        migrated += result.count;
      }
    } catch (err) {
      log(`Failed to migrate ${entry}: ${(err as Error).message}`);
    }
  }

  if (total > 0) {
    log(`Memory migration complete: ${migrated} observations across ${total} projects`);
  }

  return { total, migrated };
}
