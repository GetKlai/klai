/**
 * Memory CLI Commands
 *
 * Commands for listing and searching observations by category.
 *
 * Usage:
 *   codeindex learnings [project]    — List learnings
 *   codeindex dos [project]          — List "do" rules
 *   codeindex donts [project]        — List "dont" rules
 *   codeindex preferences            — List global preferences
 *   codeindex decisions [project]    — List architecture decisions
 *   codeindex bugs [project]         — List known bugs + resolutions
 *   codeindex memory [--search text] — Search all observations
 *   codeindex note <title>           — Quick-add an observation
 */

import { ensureMemory, getMemoryPath, getGlobalMemoryPath } from '../core/memory/global-store.js';
import {
  listByType,
  searchObservations,
  listRecentObservations,
  createObservation,
} from '../core/memory/observation-store.js';
import { GLOBAL_PROJECT_NAME } from '../core/memory/types.js';
import type { Observation, ObservationType, ObservationSearchResult } from '../core/memory/types.js';
import { findRegistryEntry } from '../storage/repo-manager.js';
import { isGitRepo, getMainRepoRoot } from '../storage/git.js';

const dim = (s: string) => `\x1b[2m${s}\x1b[0m`;
const bold = (s: string) => `\x1b[1m${s}\x1b[0m`;
const cyan = (s: string) => `\x1b[36m${s}\x1b[0m`;
const yellow = (s: string) => `\x1b[33m${s}\x1b[0m`;

/** Resolve the current project name from CWD or explicit argument */
async function resolveProject(explicit?: string): Promise<string | undefined> {
  if (explicit) return explicit;
  if (!isGitRepo(process.cwd())) return undefined;
  const root = getMainRepoRoot(process.cwd());
  if (!root) return undefined;
  const entry = await findRegistryEntry(root);
  return entry?.name || undefined;
}

/** Format age */
function formatAge(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  const hours = Math.floor(ms / 3_600_000);
  if (hours < 24) return 'today';
  const days = Math.floor(hours / 24);
  if (days === 1) return '1d ago';
  if (days < 7) return `${days}d ago`;
  const weeks = Math.floor(days / 7);
  if (weeks < 5) return `${weeks}w ago`;
  return `${Math.floor(days / 30)}mo ago`;
}

/** Print a list of observations */
function printResults(results: ObservationSearchResult[], title: string): void {
  if (results.length === 0) {
    console.log(`  ${dim('No observations found.')}`);
    return;
  }

  console.log(`  ${bold(title)} (${results.length})\n`);
  for (const r of results) {
    const obs = r.observation;
    const source = r.source === 'global' ? dim('global') : cyan(r.source);
    const age = dim(formatAge(obs.createdAt));
    const tags = obs.tags.length > 0 ? dim(` [${obs.tags.join(', ')}]`) : '';
    console.log(`  ${yellow(`[${obs.type}]`)} ${obs.name} ${age} ${source}${tags}`);
    if (obs.content && obs.content !== obs.name) {
      // Truncate long content
      const content = obs.content.length > 120 ? obs.content.slice(0, 117) + '...' : obs.content;
      console.log(`    ${dim(content)}`);
    }
  }
}

/** List observations by type across global + project memory */
async function listByCategory(type: ObservationType, project?: string): Promise<void> {
  const projectName = await resolveProject(project);
  await ensureMemory(projectName);

  const results: ObservationSearchResult[] = [];

  // Global observations
  try {
    const global = await listByType(GLOBAL_PROJECT_NAME, type, { limit: 20 });
    results.push(...global);
  } catch {}

  // Project observations
  if (projectName && projectName !== GLOBAL_PROJECT_NAME) {
    try {
      const proj = await listByType(projectName, type, { project: projectName, limit: 20 });
      results.push(...proj);
    } catch {}
  }

  // Sort by date, newest first
  results.sort((a, b) => b.observation.createdAt.localeCompare(a.observation.createdAt));

  const title = `${type.charAt(0).toUpperCase() + type.slice(1)}s${projectName ? ` (${projectName} + global)` : ' (global)'}`;
  printResults(results, title);
}

// ─── Exported Commands ─────────────────────────────────────────────

export async function learningsCommand(project?: string): Promise<void> {
  await listByCategory('learning', project);
}

export async function dosCommand(project?: string): Promise<void> {
  await listByCategory('do', project);
}

export async function dontsCommand(project?: string): Promise<void> {
  await listByCategory('dont', project);
}

export async function preferencesCommand(project?: string): Promise<void> {
  await listByCategory('preference', project);
}

export async function decisionsCommand(project?: string): Promise<void> {
  await listByCategory('decision', project);
}

export async function bugsCommand(project?: string): Promise<void> {
  await listByCategory('bug', project);
}

export async function patternsCommand(project?: string): Promise<void> {
  await listByCategory('pattern', project);
}

export async function memoryCommand(opts: { search?: string; project?: string }): Promise<void> {
  const projectName = await resolveProject(opts.project);
  await ensureMemory(projectName);

  if (opts.search) {
    // Search mode
    const results: ObservationSearchResult[] = [];

    try {
      const global = await searchObservations(GLOBAL_PROJECT_NAME, { query: opts.search, limit: 10 });
      results.push(...global);
    } catch {}

    if (projectName && projectName !== GLOBAL_PROJECT_NAME) {
      try {
        const proj = await searchObservations(projectName, { query: opts.search, project: projectName, limit: 10 });
        results.push(...proj);
      } catch {}
    }

    results.sort((a, b) => b.observation.createdAt.localeCompare(a.observation.createdAt));
    printResults(results, `Search: "${opts.search}"`);
  } else {
    // Recent mode
    const results: ObservationSearchResult[] = [];

    try {
      const global = await listRecentObservations(GLOBAL_PROJECT_NAME, { limit: 10 });
      results.push(...global.map(o => ({
        observation: o,
        refs: [],
        source: 'global' as const,
      })));
    } catch {}

    if (projectName && projectName !== GLOBAL_PROJECT_NAME) {
      try {
        const proj = await listRecentObservations(projectName, { limit: 10, project: projectName });
        results.push(...proj.map(o => ({
          observation: o,
          refs: [],
          source: projectName,
        })));
      } catch {}
    }

    results.sort((a, b) => b.observation.createdAt.localeCompare(a.observation.createdAt));
    const title = `Recent observations${projectName ? ` (${projectName} + global)` : ' (global)'}`;
    printResults(results, title);
  }
}

export async function noteCommand(title: string, opts: { type?: string; scope?: string; tags?: string; content?: string }): Promise<void> {
  const projectName = await resolveProject();
  const scope = (opts.scope === 'global' ? 'global' : 'repo') as 'global' | 'repo';
  const type = (opts.type || 'note') as ObservationType;
  const tags = opts.tags ? opts.tags.split(',').map(t => t.trim()) : [];
  const content = opts.content || title;

  const dbKey = scope === 'global' ? GLOBAL_PROJECT_NAME : (projectName || GLOBAL_PROJECT_NAME);
  const project = scope === 'global' ? GLOBAL_PROJECT_NAME : (projectName || GLOBAL_PROJECT_NAME);

  await ensureMemory(projectName);

  const obs = await createObservation(dbKey, {
    name: title,
    type,
    content,
    tags,
    project,
  });

  console.log(`  ${bold('Saved:')} [${type}] ${obs.name}`);
  console.log(`  ${dim(`scope: ${scope}, project: ${project}, id: ${obs.uid.slice(0, 8)}`)}`);
}
