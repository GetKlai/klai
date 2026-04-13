/**
 * Staleness Check
 * 
 * Checks if the CodeIndex index is behind the current git HEAD.
 * Returns a hint for the LLM to call analyze if stale.
 */

import { execFileSync } from 'child_process';
import path from 'path';

export interface StalenessInfo {
  isStale: boolean;
  commitsBehind: number;
  daysOld: number;
  hint?: string;
}

/**
 * Check how many commits the index is behind HEAD and how old it is.
 */
export function checkStaleness(repoPath: string, lastCommit: string, indexedAt?: string): StalenessInfo {
  let commitsBehind = 0;
  let daysOld = 0;

  // Commit distance
  try {
    const result = execFileSync(
      'git', ['rev-list', '--count', `${lastCommit}..HEAD`],
      { cwd: repoPath, encoding: 'utf-8', stdio: ['pipe', 'pipe', 'pipe'] }
    ).trim();
    commitsBehind = parseInt(result, 10) || 0;
  } catch {
    // If git command fails, assume not stale (fail open)
  }

  // Age in days
  if (indexedAt) {
    const indexedDate = new Date(indexedAt);
    const now = new Date();
    daysOld = Math.floor((now.getTime() - indexedDate.getTime()) / (1000 * 60 * 60 * 24));
  }

  const isStale = commitsBehind > 0;

  if (isStale) {
    const parts: string[] = [];
    parts.push(`${commitsBehind} commit${commitsBehind > 1 ? 's' : ''} behind HEAD`);
    if (daysOld > 0) {
      parts.push(`${daysOld} day${daysOld > 1 ? 's' : ''} old`);
    }
    return {
      isStale,
      commitsBehind,
      daysOld,
      hint: `⚠️ Index is ${parts.join(' and ')}. Run \`codeindex update\` to refresh.`,
    };
  }

  return { isStale: false, commitsBehind: 0, daysOld };
}
