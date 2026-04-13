/**
 * Memory Context CLI Command
 *
 * Fast-path command for the UserPromptSubmit hook.
 * Outputs recent observations (global + project) as compact text to stderr.
 *
 * Usage: codeindex memory-context [project]
 * Returns compact observation summary to stderr.
 *
 * Performance: Must cold-start fast (<500ms).
 */

import { ensureGlobalMemory, ensureProjectMemory } from '../core/memory/global-store.js';
import { listRecentObservations } from '../core/memory/observation-store.js';
import { GLOBAL_PROJECT_NAME } from '../core/memory/types.js';
import type { Observation } from '../core/memory/types.js';

/** Format age from ISO date to compact relative string */
function formatAge(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  const hours = Math.floor(ms / 3_600_000);
  if (hours < 24) return 'today';
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d`;
  const weeks = Math.floor(days / 7);
  if (weeks < 5) return `${weeks}w`;
  return `${Math.floor(days / 30)}mo`;
}

/** Format a single observation as a compact one-liner */
function formatObs(obs: Observation, showSource: boolean): string {
  const age = formatAge(obs.createdAt);
  const source = showSource ? (obs.project === GLOBAL_PROJECT_NAME ? 'global' : obs.project) : '';
  const sourceTag = source ? `, ${source}` : '';
  return `- [${obs.type}] ${obs.name} (${age}${sourceTag})`;
}

export async function memoryContextCommand(project?: string): Promise<void> {
  try {
    const lines: string[] = [];
    const globalObs: Observation[] = [];
    const projectObs: Observation[] = [];

    // Load global memory
    try {
      await ensureGlobalMemory();
      const recent = await listRecentObservations(GLOBAL_PROJECT_NAME, { limit: 5 });
      globalObs.push(...recent);
    } catch {
      // No global memory yet — that's fine
    }

    // Load project memory if provided
    if (project && project !== GLOBAL_PROJECT_NAME) {
      try {
        await ensureProjectMemory(project);
        const recent = await listRecentObservations(project, { limit: 5, project });
        projectObs.push(...recent);
      } catch {
        // No project memory yet — that's fine
      }
    }

    if (globalObs.length === 0 && projectObs.length === 0) {
      process.exit(0);
    }

    lines.push('[CodeIndex Memory]');

    if (globalObs.length > 0) {
      for (const obs of globalObs) {
        lines.push(formatObs(obs, true));
      }
    }

    if (projectObs.length > 0) {
      for (const obs of projectObs) {
        lines.push(formatObs(obs, globalObs.length > 0));
      }
    }

    lines.push('');
    lines.push('Use recall() to search memory. Use remember() to save observations.');

    // Write to stderr (same reason as augment: KuzuDB captures stdout)
    process.stderr.write(lines.join('\n') + '\n');
  } catch {
    // Graceful failure — never break the calling hook
    process.exit(0);
  }
}
