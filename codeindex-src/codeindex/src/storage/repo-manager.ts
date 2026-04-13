/**
 * Repository Manager
 *
 * Manages CodeIndex index storage centrally under ~/.codeindex/{projectName}/.
 * Maintains a global registry at ~/.codeindex/registry.json
 * so the MCP server can discover indexed repos from any cwd or worktree.
 *
 * Worktree support: all worktrees of a repo share the same index.
 * The registry maps main repo paths to project names, and worktrees
 * are resolved to their main repo via `git rev-parse --git-common-dir`.
 */

import fs from 'fs/promises';
import fsSync from 'fs';
import path from 'path';
import os from 'os';

export interface RepoMeta {
  repoPath: string;
  lastCommit: string;
  indexedAt: string;
  stats?: {
    files?: number;
    nodes?: number;
    edges?: number;
    communities?: number;
    processes?: number;
    embeddings?: number;
  };
}

export interface IndexedRepo {
  repoPath: string;
  storagePath: string;
  lbugPath: string;
  metaPath: string;
  meta: RepoMeta;
}

/**
 * Shape of an entry in the global registry (~/.codeindex/registry.json)
 */
export interface RegistryEntry {
  name: string;
  path: string;
  storagePath: string;
  indexedAt: string;
  lastCommit: string;
  stats?: RepoMeta['stats'];
}

// ─── Centralized Storage Helpers ──────────────────────────────────────

/**
 * Get the centralized storage path for a project.
 * Storage is under ~/.codeindex/{projectName}/ (NOT in the repo itself).
 */
export const getStoragePath = (projectName: string): string => {
  return path.join(getGlobalDir(), projectName);
};

/**
 * Get paths to key storage files for a project.
 * Handles backward compatibility: if 'lbug' doesn't exist but 'kuzu' does,
 * renames 'kuzu' → 'lbug' in place (one-time silent migration).
 */
export const getStoragePaths = (projectName: string) => {
  const storagePath = getStoragePath(projectName);
  const lbugPath = path.join(storagePath, 'lbug');
  const kuzuPath = path.join(storagePath, 'kuzu');

  // One-time migration: rename kuzu → lbug if needed
  if (!fsSync.existsSync(lbugPath) && fsSync.existsSync(kuzuPath)) {
    try {
      fsSync.renameSync(kuzuPath, lbugPath);
    } catch {
      // Race condition or permissions — use whatever exists
    }
  }

  // Verify the database file has valid LadybugDB magic bytes ("LBUG").
  // Old kuzu 0.11 files crash the native module with uncatchable exceptions.
  // If incompatible, delete it so codeindex analyze can rebuild.
  if (fsSync.existsSync(lbugPath)) {
    try {
      const fd = fsSync.openSync(lbugPath, 'r');
      const buf = Buffer.alloc(4);
      fsSync.readSync(fd, buf, 0, 4, 0);
      fsSync.closeSync(fd);
      const magic = buf.toString('ascii');
      if (magic !== 'LBUG') {
        console.warn(`Graph DB "${projectName}" has incompatible format (${magic}), removing. Re-index with: codeindex analyze`);
        fsSync.unlinkSync(lbugPath);
        // Also clean up WAL/lock from old version
        try { fsSync.unlinkSync(lbugPath + '.wal'); } catch {}
        try { fsSync.unlinkSync(lbugPath + '.lock'); } catch {}
      }
    } catch {
      // Can't read — leave it for the adapter to handle
    }
  }

  return {
    storagePath,
    lbugPath,
    metaPath: path.join(storagePath, 'meta.json'),
  };
};

/**
 * Legacy: get storage path from repo path (for backward compatibility).
 * Checks the registry first for a project name, falls back to basename.
 */
export const getStoragePathFromRepo = async (repoPath: string): Promise<string> => {
  const resolved = path.resolve(repoPath);
  const entries = await readRegistry();
  const entry = entries.find(e => path.resolve(e.path) === resolved);
  if (entry) return entry.storagePath;
  // Fallback: use repo directory name
  return getStoragePath(path.basename(resolved));
};

/**
 * Legacy: get storage paths from repo path (for backward compatibility).
 */
export const getStoragePathsFromRepo = async (repoPath: string) => {
  const storagePath = await getStoragePathFromRepo(repoPath);
  const lbugPath = path.join(storagePath, 'lbug');
  const kuzuPath = path.join(storagePath, 'kuzu');

  // One-time migration: rename kuzu → lbug if needed
  if (!fsSync.existsSync(lbugPath) && fsSync.existsSync(kuzuPath)) {
    try {
      fsSync.renameSync(kuzuPath, lbugPath);
    } catch {}
  }

  return {
    storagePath,
    lbugPath,
    metaPath: path.join(storagePath, 'meta.json'),
  };
};

/**
 * Load metadata from a storage path
 */
export const loadMeta = async (storagePath: string): Promise<RepoMeta | null> => {
  try {
    const metaPath = path.join(storagePath, 'meta.json');
    const raw = await fs.readFile(metaPath, 'utf-8');
    return JSON.parse(raw) as RepoMeta;
  } catch {
    return null;
  }
};

/**
 * Save metadata to storage
 */
export const saveMeta = async (storagePath: string, meta: RepoMeta): Promise<void> => {
  await fs.mkdir(storagePath, { recursive: true });
  const metaPath = path.join(storagePath, 'meta.json');
  await fs.writeFile(metaPath, JSON.stringify(meta, null, 2), 'utf-8');
};

/**
 * Check if a project has a CodeIndex index (by project name)
 */
export const hasIndex = async (projectName: string): Promise<boolean> => {
  const { metaPath } = getStoragePaths(projectName);
  try {
    await fs.access(metaPath);
    return true;
  } catch {
    return false;
  }
};

/**
 * Load an indexed repo by project name
 */
export const loadRepoByName = async (projectName: string): Promise<IndexedRepo | null> => {
  const paths = getStoragePaths(projectName);
  const meta = await loadMeta(paths.storagePath);
  if (!meta) return null;

  return {
    repoPath: meta.repoPath,
    ...paths,
    meta,
  };
};

/**
 * Load an indexed repo from a filesystem path.
 * Resolves worktrees to their main repo, then looks up the registry.
 */
export const loadRepo = async (repoPath: string): Promise<IndexedRepo | null> => {
  const resolved = path.resolve(repoPath);
  const entries = await readRegistry();
  const entry = entries.find(e => path.resolve(e.path) === resolved);
  if (!entry) return null;

  const meta = await loadMeta(entry.storagePath);
  if (!meta) return null;

  const lbugPath = path.join(entry.storagePath, 'lbug');
  const kuzuPath = path.join(entry.storagePath, 'kuzu');
  if (!fsSync.existsSync(lbugPath) && fsSync.existsSync(kuzuPath)) {
    try { fsSync.renameSync(kuzuPath, lbugPath); } catch {}
  }

  return {
    repoPath: resolved,
    storagePath: entry.storagePath,
    lbugPath,
    metaPath: path.join(entry.storagePath, 'meta.json'),
    meta,
  };
};

/**
 * Find a repo for a given working directory.
 * Resolves worktrees to their main repo via git, then matches the registry.
 */
export const findRepo = async (startPath: string): Promise<IndexedRepo | null> => {
  const { getMainRepoRoot } = await import('./git.js');

  // First: resolve worktree → main repo root via git
  const mainRoot = getMainRepoRoot(startPath);
  if (mainRoot) {
    const repo = await loadRepo(mainRoot);
    if (repo) return repo;
  }

  // Fallback: check the toplevel (for non-worktree cases)
  const { getGitRoot } = await import('./git.js');
  const gitRoot = getGitRoot(startPath);
  if (gitRoot && gitRoot !== mainRoot) {
    const repo = await loadRepo(gitRoot);
    if (repo) return repo;
  }

  return null;
};

// ─── Global Registry (~/.codeindex/registry.json) ───────────────────────

/**
 * Get the path to the global CodeIndex directory
 */
export const getGlobalDir = (): string => {
  return path.join(os.homedir(), '.codeindex');
};

/**
 * Get the path to the global registry file
 */
export const getGlobalRegistryPath = (): string => {
  return path.join(getGlobalDir(), 'registry.json');
};

/**
 * Read the global registry. Returns empty array if not found.
 */
export const readRegistry = async (): Promise<RegistryEntry[]> => {
  try {
    const raw = await fs.readFile(getGlobalRegistryPath(), 'utf-8');
    const data = JSON.parse(raw);
    return Array.isArray(data) ? data : [];
  } catch {
    return [];
  }
};

/**
 * Write the global registry to disk
 */
const writeRegistry = async (entries: RegistryEntry[]): Promise<void> => {
  const dir = getGlobalDir();
  await fs.mkdir(dir, { recursive: true });
  await fs.writeFile(getGlobalRegistryPath(), JSON.stringify(entries, null, 2), 'utf-8');
};

/**
 * Register (add or update) a repo in the global registry.
 * Called after `codeindex analyze` completes.
 *
 * @param projectName - User-chosen project name (e.g. "ParrotKey")
 * @param repoPath - Path to the main git repository root
 * @param meta - Index metadata
 */
export const registerRepo = async (projectName: string, repoPath: string, meta: RepoMeta): Promise<void> => {
  const resolved = path.resolve(repoPath);
  const storagePath = getStoragePath(projectName);

  const entries = await readRegistry();
  // Match by path OR by name — case-INSENSITIVE (CodeIndex == codeindex)
  const nameLower = projectName.toLowerCase();
  const existing = entries.findIndex(
    (e) => path.resolve(e.path) === resolved || e.name.toLowerCase() === nameLower
  );

  const entry: RegistryEntry = {
    name: projectName,
    path: resolved,
    storagePath,
    indexedAt: meta.indexedAt,
    lastCommit: meta.lastCommit,
    stats: meta.stats,
  };

  if (existing >= 0) {
    entries[existing] = entry;
  } else {
    entries.push(entry);
  }

  await writeRegistry(entries);
};

/**
 * Remove a repo from the global registry (by path or name).
 * Called after `codeindex clean`.
 */
export const unregisterRepo = async (repoPathOrName: string): Promise<void> => {
  const resolved = path.resolve(repoPathOrName);
  const entries = await readRegistry();
  const filtered = entries.filter(
    (e) => path.resolve(e.path) !== resolved && e.name !== repoPathOrName
  );
  await writeRegistry(filtered);
};

/**
 * Look up a registry entry by working directory.
 * Resolves worktrees to their main repo automatically.
 */
export const findRegistryEntry = async (cwd: string): Promise<RegistryEntry | null> => {
  const { getMainRepoRoot, getGitRoot } = await import('./git.js');
  const entries = await readRegistry();

  // Resolve worktree → main repo root
  const mainRoot = getMainRepoRoot(cwd);
  if (mainRoot) {
    const entry = entries.find(e => path.resolve(e.path) === mainRoot);
    if (entry) return entry;
  }

  // Fallback: try git toplevel directly
  const gitRoot = getGitRoot(cwd);
  if (gitRoot) {
    const entry = entries.find(e => path.resolve(e.path) === gitRoot);
    if (entry) return entry;
  }

  return null;
};

/**
 * List all registered repos from the global registry.
 * Optionally validates that each entry's .codeindex/ still exists.
 */
export const listRegisteredRepos = async (opts?: { validate?: boolean }): Promise<RegistryEntry[]> => {
  const entries = await readRegistry();
  if (!opts?.validate) return entries;

  // Validate each entry still has a .codeindex/ directory
  const valid: RegistryEntry[] = [];
  for (const entry of entries) {
    try {
      await fs.access(path.join(entry.storagePath, 'meta.json'));
      valid.push(entry);
    } catch {
      // Index no longer exists — skip
    }
  }

  // If we pruned any entries, save the cleaned registry
  if (valid.length !== entries.length) {
    await writeRegistry(valid);
  }

  return valid;
};

// ─── Global CLI Config (~/.codeindex/config.json) ─────────────────────────

export interface CLIConfig {
  apiKey?: string;
  model?: string;
  baseUrl?: string;
}

/**
 * Get the path to the global CLI config file
 */
export const getGlobalConfigPath = (): string => {
  return path.join(getGlobalDir(), 'config.json');
};

/**
 * Load CLI config from ~/.codeindex/config.json
 */
export const loadCLIConfig = async (): Promise<CLIConfig> => {
  try {
    const raw = await fs.readFile(getGlobalConfigPath(), 'utf-8');
    return JSON.parse(raw) as CLIConfig;
  } catch {
    return {};
  }
};

/**
 * Save CLI config to ~/.codeindex/config.json
 */
export const saveCLIConfig = async (config: CLIConfig): Promise<void> => {
  const dir = getGlobalDir();
  await fs.mkdir(dir, { recursive: true });
  await fs.writeFile(getGlobalConfigPath(), JSON.stringify(config, null, 2), 'utf-8');
};
