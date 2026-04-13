import { execSync } from 'child_process';
import path from 'path';

// Git utilities for repository detection, commit tracking, and diff analysis

export const isGitRepo = (repoPath: string): boolean => {
  try {
    execSync('git rev-parse --is-inside-work-tree', { cwd: repoPath, stdio: 'ignore' });
    return true;
  } catch {
    return false;
  }
};

export const getCurrentCommit = (repoPath: string): string => {
  try {
    return execSync('git rev-parse HEAD', { cwd: repoPath }).toString().trim();
  } catch {
    return '';
  }
};

/**
 * Find the git repository root from any path inside the repo.
 * Returns the worktree root if inside a worktree.
 */
export const getGitRoot = (fromPath: string): string | null => {
  try {
    return execSync('git rev-parse --show-toplevel', { cwd: fromPath, stdio: ['ignore', 'pipe', 'ignore'] })
      .toString()
      .trim();
  } catch {
    return null;
  }
};

/**
 * Find the MAIN repository root, resolving through worktrees.
 *
 * In a worktree, `git rev-parse --show-toplevel` returns the worktree root,
 * but we need the main repo root where the shared .git directory lives.
 * `git rev-parse --git-common-dir` returns the shared .git dir (e.g. /repo/.git),
 * and the repo root is its parent.
 *
 * Falls back to getGitRoot() if not in a worktree.
 */
export const getMainRepoRoot = (fromPath: string): string | null => {
  try {
    const commonDir = execSync('git rev-parse --git-common-dir', { cwd: fromPath, stdio: ['ignore', 'pipe', 'ignore'] })
      .toString()
      .trim();
    // commonDir is relative to cwd or absolute
    // e.g. "/Users/x/repos/project/.git" or "../repos/project/.git"
    const resolved = path.resolve(fromPath, commonDir);
    // The repo root is the parent of the .git directory
    return path.dirname(resolved);
  } catch {
    return getGitRoot(fromPath);
  }
};

/**
 * Check if the current path is inside a git worktree (not the main repo).
 */
export const isWorktree = (fromPath: string): boolean => {
  const toplevel = getGitRoot(fromPath);
  const mainRoot = getMainRepoRoot(fromPath);
  return !!(toplevel && mainRoot && toplevel !== mainRoot);
};

/**
 * Derive the canonical project name from any path inside a repo (including worktrees).
 * Always uses the main repo root for name derivation, so worktree folder names
 * don't leak into the project name.
 */
export const deriveProjectName = (fromPath: string): string => {
  const mainRoot = getMainRepoRoot(fromPath);
  const gitRoot = getGitRoot(fromPath);
  return path.basename(mainRoot || gitRoot || fromPath);
};

/**
 * Result of git diff between two commits
 */
export interface ChangedFiles {
  added: string[];
  modified: string[];
  deleted: string[];
}

/**
 * Parse `git diff --name-status` output into ChangedFiles.
 */
function parseDiffOutput(output: string, result: ChangedFiles): void {
  if (!output) return;
  for (const line of output.split('\n')) {
    if (!line.trim()) continue;
    // Format: "STATUS\tpath" or "R###\toldpath\tnewpath"
    const parts = line.split('\t');
    const status = parts[0];

    if (status === 'A') {
      result.added.push(parts[1]);
    } else if (status === 'M') {
      result.modified.push(parts[1]);
    } else if (status === 'D') {
      result.deleted.push(parts[1]);
    } else if (status.startsWith('R')) {
      // Rename: old path deleted, new path added
      result.deleted.push(parts[1]);
      result.added.push(parts[2]);
    } else if (status.startsWith('C')) {
      // Copy: new path added (old path unchanged)
      result.added.push(parts[2]);
    }
  }
}

/**
 * Get files changed between two commits using `git diff --name-status`.
 * Also includes uncommitted working tree changes (staged + unstaged)
 * so that `codeindex update` picks up edits before they are committed.
 *
 * Returns paths relative to the repo root.
 * Handles renames (R status) as delete + add.
 * Handles copies (C status) as add only.
 */
export const getChangedFiles = (fromCommit: string, toCommit: string, repoPath: string): ChangedFiles => {
  const result: ChangedFiles = { added: [], modified: [], deleted: [] };

  try {
    // Committed changes between fromCommit and toCommit
    const committedOutput = execSync(
      `git diff --name-status ${fromCommit} ${toCommit}`,
      { cwd: repoPath, maxBuffer: 50 * 1024 * 1024 }
    ).toString().trim();
    parseDiffOutput(committedOutput, result);

    // Uncommitted working tree changes (staged + unstaged) relative to HEAD
    const workingOutput = execSync(
      `git diff --name-status HEAD`,
      { cwd: repoPath, maxBuffer: 50 * 1024 * 1024 }
    ).toString().trim();
    parseDiffOutput(workingOutput, result);

    // Deduplicate: a file can appear in both committed and working tree diffs
    result.added = [...new Set(result.added)];
    result.modified = [...new Set(result.modified)];
    result.deleted = [...new Set(result.deleted)];
  } catch {
    // git diff failed — return empty (caller should fall back to full rebuild)
  }

  return result;
};

/**
 * Check if there are uncommitted changes in the working tree.
 */
export const hasUncommittedChanges = (repoPath: string): boolean => {
  try {
    const output = execSync(
      'git status --porcelain -uno',
      { cwd: repoPath, maxBuffer: 10 * 1024 * 1024 }
    ).toString().trim();
    return output.length > 0;
  } catch {
    return false;
  }
};
