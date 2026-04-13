/**
 * Project Scanner
 *
 * Scans project context on first indexing: README, package manifest,
 * git log, directory structure. Saves observations to CodeIndex memory
 * so Claude has project knowledge from the start.
 *
 * Runs only on first `codeindex analyze`, not on updates.
 * Target: < 2 seconds.
 */

import fs from 'fs/promises';
import path from 'path';
import { execSync } from 'child_process';
import { createObservation } from '../core/memory/observation-store.js';
import { ensureProjectMemory } from '../core/memory/global-store.js';

/**
 * Scan project context and save observations.
 * Call after first-time indexing is complete.
 */
export async function scanProjectContext(repoPath: string, projectName: string): Promise<number> {
  // Initialize the memory DB for this project (separate from the code index DB)
  await ensureProjectMemory(projectName);
  const dbKey = projectName;

  let saved = 0;

  try {
    // ── Gather project info ────────────────────────────────────────
    const readme = await readFileHead(path.join(repoPath, 'README.md'), 150);
    const manifest = await detectManifest(repoPath);
    const gitLog = gitLogOneline(repoPath, 20);
    const branches = gitBranches(repoPath);
    const topDirs = await listTopDirs(repoPath);

    // ── Build project overview ─────────────────────────────────────
    const overviewParts: string[] = [];

    if (manifest) {
      overviewParts.push(`Type: ${manifest.type}`);
      if (manifest.name) overviewParts.push(`Package: ${manifest.name}`);
      if (manifest.description) overviewParts.push(`Description: ${manifest.description}`);
      if (manifest.deps.length > 0) overviewParts.push(`Key dependencies: ${manifest.deps.slice(0, 15).join(', ')}`);
    }

    if (topDirs.length > 0) {
      overviewParts.push(`Top-level directories: ${topDirs.join(', ')}`);
    }

    if (readme) {
      // Take first ~500 chars of README for context
      const readmeSnippet = readme.slice(0, 500).trim();
      overviewParts.push(`README excerpt:\n${readmeSnippet}`);
    }

    if (overviewParts.length > 0) {
      await createObservation(dbKey, {
        name: `${projectName} - project overview`,
        type: 'note',
        content: overviewParts.join('\n'),
        tags: ['architecture', 'overview'],
        project: projectName,
      });
      saved++;
    }

    // ── Build git history overview ─────────────────────────────────
    const historyParts: string[] = [];

    if (branches.main) historyParts.push(`Main branch: ${branches.main}`);
    if (branches.all.length > 1) {
      historyParts.push(`${branches.all.length} branches. Active: ${branches.all.slice(0, 10).join(', ')}`);
    }

    if (gitLog.length > 0) {
      historyParts.push(`Recent commits (${gitLog.length}):`);
      for (const line of gitLog.slice(0, 10)) {
        historyParts.push(`  ${line}`);
      }
    }

    if (historyParts.length > 0) {
      await createObservation(dbKey, {
        name: `Git history on branch strategy`,
        type: 'note',
        content: historyParts.join('\n'),
        tags: ['git', 'branches', 'history'],
        project: projectName,
      });
      saved++;
    }

    // ── Project structure ──────────────────────────────────────────
    if (topDirs.length > 0 && manifest) {
      const structureParts = [`Project structure of ${projectName}:`];
      structureParts.push(`Root directories: ${topDirs.join(', ')}`);

      // Detect monorepo patterns
      const hasWorkspaces = topDirs.some(d => ['packages', 'apps', 'libs', 'modules'].includes(d));
      if (hasWorkspaces) {
        structureParts.push(`Monorepo detected (has packages/apps/libs directory)`);
        // List sub-packages
        for (const wsDir of ['packages', 'apps', 'libs', 'modules']) {
          const wsPath = path.join(repoPath, wsDir);
          try {
            const entries = await fs.readdir(wsPath, { withFileTypes: true });
            const subdirs = entries.filter(e => e.isDirectory() && !e.name.startsWith('.')).map(e => e.name);
            if (subdirs.length > 0) {
              structureParts.push(`${wsDir}/: ${subdirs.join(', ')}`);
            }
          } catch { /* dir doesn't exist */ }
        }
      }

      await createObservation(dbKey, {
        name: `Project structure - directories, ${manifest.type}, entitlements`,
        type: 'note',
        content: structureParts.join('\n'),
        tags: ['structure', 'directories'],
        project: projectName,
      });
      saved++;
    }
  } catch {
    // Non-fatal — project scanning is best-effort
  }

  return saved;
}

// ── Helpers ──────────────────────────────────────────────────────────

async function readFileHead(filePath: string, maxLines: number): Promise<string | null> {
  try {
    const content = await fs.readFile(filePath, 'utf-8');
    return content.split('\n').slice(0, maxLines).join('\n');
  } catch {
    return null;
  }
}

interface ManifestInfo {
  type: string;
  name?: string;
  description?: string;
  deps: string[];
}

async function detectManifest(repoPath: string): Promise<ManifestInfo | null> {
  // Try package.json (Node.js)
  try {
    const pkg = JSON.parse(await fs.readFile(path.join(repoPath, 'package.json'), 'utf-8'));
    const deps = [
      ...Object.keys(pkg.dependencies || {}),
      ...Object.keys(pkg.devDependencies || {}),
    ].filter(d => !d.startsWith('@types/'));
    return {
      type: pkg.workspaces ? 'Node.js monorepo' : 'Node.js',
      name: pkg.name,
      description: pkg.description,
      deps: deps.slice(0, 20),
    };
  } catch { /* not Node.js */ }

  // Try Cargo.toml (Rust)
  try {
    const cargo = await fs.readFile(path.join(repoPath, 'Cargo.toml'), 'utf-8');
    const nameMatch = cargo.match(/^name\s*=\s*"([^"]+)"/m);
    return { type: 'Rust', name: nameMatch?.[1], deps: [] };
  } catch { /* not Rust */ }

  // Try pyproject.toml (Python)
  try {
    const pyproject = await fs.readFile(path.join(repoPath, 'pyproject.toml'), 'utf-8');
    const nameMatch = pyproject.match(/^name\s*=\s*"([^"]+)"/m);
    return { type: 'Python', name: nameMatch?.[1], deps: [] };
  } catch { /* not Python */ }

  // Try go.mod (Go)
  try {
    const gomod = await fs.readFile(path.join(repoPath, 'go.mod'), 'utf-8');
    const moduleMatch = gomod.match(/^module\s+(\S+)/m);
    return { type: 'Go', name: moduleMatch?.[1], deps: [] };
  } catch { /* not Go */ }

  return null;
}

function gitLogOneline(repoPath: string, count: number): string[] {
  try {
    const log = execSync(`git log --oneline -${count} --no-merges`, {
      cwd: repoPath, encoding: 'utf-8', stdio: ['pipe', 'pipe', 'pipe'],
    }).trim();
    return log ? log.split('\n') : [];
  } catch {
    return [];
  }
}

function gitBranches(repoPath: string): { main: string; all: string[] } {
  try {
    const branches = execSync('git branch --format="%(refname:short)"', {
      cwd: repoPath, encoding: 'utf-8', stdio: ['pipe', 'pipe', 'pipe'],
    }).trim().split('\n').filter(Boolean);

    const main = branches.find(b => ['main', 'master', 'develop', 'dev'].includes(b)) || branches[0] || 'main';
    return { main, all: branches };
  } catch {
    return { main: 'main', all: [] };
  }
}

async function listTopDirs(repoPath: string): Promise<string[]> {
  try {
    const entries = await fs.readdir(repoPath, { withFileTypes: true });
    return entries
      .filter(e => e.isDirectory() && !e.name.startsWith('.') && e.name !== 'node_modules')
      .map(e => e.name)
      .sort();
  } catch {
    return [];
  }
}
