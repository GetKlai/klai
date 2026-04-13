/**
 * Global Memory Store
 *
 * Manages the global memory database at ~/.codeindex/_global/memory.
 * Also provides helpers to get memory DB paths for project-level stores.
 */

import path from 'path';
import { getGlobalDir } from '../../storage/repo-manager.js';
import { initMemoryDb, isMemoryDbReady } from './memory-adapter.js';
import { GLOBAL_PROJECT_NAME, MEMORY_DB_DIR } from './types.js';

/**
 * Get the path to a memory database file.
 * Memory DBs live alongside the code graph: ~/.codeindex/{name}/memory
 */
export function getMemoryPath(projectName: string): string {
  return path.join(getGlobalDir(), projectName, MEMORY_DB_DIR);
}

/**
 * Get the global memory database path
 */
export function getGlobalMemoryPath(): string {
  return getMemoryPath(GLOBAL_PROJECT_NAME);
}

/**
 * Ensure the global memory database is initialized.
 * Call this at MCP server startup or when global memory is first needed.
 */
export async function ensureGlobalMemory(): Promise<void> {
  if (isMemoryDbReady(GLOBAL_PROJECT_NAME)) return;
  const dbPath = getGlobalMemoryPath();
  await initMemoryDb(GLOBAL_PROJECT_NAME, dbPath);
}

/**
 * Ensure a project's memory database is initialized.
 */
export async function ensureProjectMemory(projectName: string): Promise<void> {
  if (isMemoryDbReady(projectName)) return;
  const dbPath = getMemoryPath(projectName);
  await initMemoryDb(projectName, dbPath);
}

/**
 * Ensure both global and project memory databases are ready.
 * Convenience function for tools that search across both.
 */
export async function ensureMemory(projectName?: string): Promise<void> {
  await ensureGlobalMemory();
  if (projectName && projectName !== GLOBAL_PROJECT_NAME) {
    await ensureProjectMemory(projectName);
  }
}
