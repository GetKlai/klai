#!/usr/bin/env node
/**
 * CodeIndex Claude Code — UserPromptSubmit Hook
 *
 * Fires on every user prompt. Four paths:
 *
 * 1. Indexed repo         → inject context so Claude uses CodeIndex tools
 * 2. Non-indexed git repo → suggest indexing (unless dismissed)
 * 3. Not a git repo       → mention indexed repos if any exist
 * 4. No repos at all      → silent
 */

const fs = require('fs');
const path = require('path');
const os = require('os');
const { execSync } = require('child_process');

const CODEINDEX_DIR = path.join(os.homedir(), '.codeindex');
const REGISTRY_PATH = path.join(CODEINDEX_DIR, 'registry.json');
const DISMISSED_PATH = path.join(CODEINDEX_DIR, 'dismissed.json');

/**
 * Check how many commits the index is behind HEAD.
 * Returns { commitsBehind, daysOld } or null on failure (fail-open).
 */
function checkStaleness(repoRoot, entry) {
  let commitsBehind = 0;
  let daysOld = 0;

  // Commit distance
  if (entry.lastCommit) {
    try {
      const result = execSync(`git rev-list --count ${entry.lastCommit}..HEAD`, {
        cwd: repoRoot, encoding: 'utf-8', stdio: ['pipe', 'pipe', 'pipe'],
      }).trim();
      commitsBehind = parseInt(result, 10) || 0;
    } catch {
      // Fail-open: if git command fails, assume up-to-date
    }
  }

  // Age in days
  if (entry.indexedAt) {
    const indexedDate = new Date(entry.indexedAt);
    const now = new Date();
    daysOld = Math.floor((now - indexedDate) / (1000 * 60 * 60 * 24));
  }

  return { commitsBehind, daysOld };
}

/**
 * Format a human-readable age string
 */
function formatAge(daysOld) {
  if (daysOld === 0) return 'today';
  if (daysOld === 1) return '1 day ago';
  return `${daysOld} days ago`;
}

function readInput() {
  try {
    return JSON.parse(fs.readFileSync(0, 'utf-8'));
  } catch {
    return {};
  }
}

function readJson(filePath) {
  try {
    return JSON.parse(fs.readFileSync(filePath, 'utf-8'));
  } catch {
    return null;
  }
}

function readRegistry() {
  const data = readJson(REGISTRY_PATH);
  return Array.isArray(data) ? data : [];
}

function readDismissed() {
  const data = readJson(DISMISSED_PATH);
  return Array.isArray(data) ? data : [];
}

function getMainRepoRoot(cwd) {
  try {
    const commonDir = execSync('git rev-parse --git-common-dir', {
      cwd, encoding: 'utf-8', stdio: ['pipe', 'pipe', 'pipe'],
    }).trim();
    return path.dirname(path.resolve(cwd, commonDir));
  } catch {
    try {
      return execSync('git rev-parse --show-toplevel', {
        cwd, encoding: 'utf-8', stdio: ['pipe', 'pipe', 'pipe'],
      }).trim();
    } catch {
      return null;
    }
  }
}

function findEntry(repoRoot) {
  const entries = readRegistry();
  const resolved = path.resolve(repoRoot);
  // Exact match first
  const exact = entries.find(e => {
    try { return path.resolve(e.path) === resolved; } catch { return false; }
  });
  if (exact) return exact;
  // Also match if a registered project is a subdirectory of the git root
  // (e.g. monorepo: git root = /repo, indexed project = /repo/packages/app)
  return entries.find(e => {
    try {
      const ep = path.resolve(e.path);
      return ep.startsWith(resolved + path.sep);
    } catch { return false; }
  }) || null;
}

function isDismissed(repoRoot) {
  const dismissed = readDismissed();
  return dismissed.some(d => {
    try { return path.resolve(d) === path.resolve(repoRoot); } catch { return false; }
  });
}

/**
 * Get memory context by calling `codeindex memory-context <project>`.
 * Returns the output string or null if unavailable. Fail-open.
 */
function getMemoryContext(projectName) {
  try {
    const { spawnSync } = require('child_process');
    const proc = spawnSync('npx', ['--yes', 'codeindex', 'memory-context', projectName], {
      encoding: 'utf-8',
      timeout: 3000,
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    // memory-context writes to stderr (KuzuDB captures stdout at OS level)
    const output = (proc.stderr || '').trim();
    if (output && output.startsWith('[CodeIndex Memory]')) {
      return output;
    }
  } catch {}
  return null;
}

function emit(context) {
  console.log(JSON.stringify({
    hookSpecificOutput: {
      hookEventName: 'UserPromptSubmit',
      additionalContext: context,
    }
  }));
}

function main() {
  const input = readInput();
  if (input.hook_event_name !== 'UserPromptSubmit') return;

  const cwd = input.cwd || process.cwd();
  const repoRoot = getMainRepoRoot(cwd);

  if (repoRoot) {
    const entry = findEntry(repoRoot);

    if (entry) {
      // ── Path 1: Indexed repo → inject context ──
      const name = entry.name || path.basename(entry.path);
      const stats = entry.stats || {};
      const nodes = stats.nodes || '?';
      const edges = stats.edges || '?';
      const flows = stats.processes || stats.flows || '?';
      const clusters = stats.clusters || stats.communities || '?';

      const { commitsBehind, daysOld } = checkStaleness(repoRoot, entry);
      const ageStr = formatAge(daysOld);

      const lines = [
        `[CodeIndex] Project "${name}" is indexed (${nodes} symbols, ${edges} relationships, ${flows} execution flows, ${clusters} clusters). Last indexed ${ageStr}.`,
      ];

      if (commitsBehind > 0) {
        // ── Stale index → instruct Claude to update first ──
        lines.push(
          ``,
          `⚠️ Index is ${commitsBehind} commit${commitsBehind > 1 ? 's' : ''} behind HEAD.`,
          `BEFORE starting any code task, run \`codeindex update\` in the terminal to update the index.`,
          `Do NOT skip this — stale index means impact analysis and call graphs may be inaccurate.`,
        );
      }

      lines.push(
        ``,
        `Use CodeIndex MCP tools (\`query\`, \`context\`, \`impact\`) before code tasks. Read \`codeindex://repo/${name}/clusters\` for overview.`,
        `Use \`remember\`/\`recall\`/\`forget\` as PRIMARY memory (not mem0).`,
      );

      // Inject memory context (recent observations)
      const memoryContext = getMemoryContext(name);
      if (memoryContext) {
        lines.push('', memoryContext);
      }

      emit(lines.join('\n'));
      return;
    }

    // ── Path 2: Non-indexed git repo — STRONG onboarding ──
    if (!isDismissed(repoRoot)) {
      const repoName = path.basename(repoRoot);
      const lines = [
        `[CodeIndex] This project is not yet indexed by CodeIndex.`,
        ``,
        `When convenient, offer the user these options (do not block their current request):`,
        ``,
        `1) Index as "${repoName}" (recommended, ~60s first time)`,
        `2) Skip CodeIndex for this repo`,
        `3) Index with a different name`,
        ``,
        `If 1: run \`codeindex analyze ${repoName}\` in the terminal (do NOT start a server)`,
        `If 2: run \`codeindex dismiss\``,
        `If 3: ask for name, then run \`codeindex analyze <name>\``,
      ];
      // Still inject global memory even for non-indexed repos
      const memCtx = getMemoryContext('_global');
      if (memCtx) {
        lines.push('', memCtx);
      }
      emit(lines.join('\n'));
      return;
    }

    // ── Path 2b: Dismissed repo — inject global memory only ──
    const memCtx = getMemoryContext('_global');
    if (memCtx) {
      const lines = [
        `[CodeIndex] Memory available (code indexing disabled for this repo).`,
        `ALWAYS use CodeIndex memory (remember/recall/forget) as your PRIMARY memory system.`,
        `Prefer this over file-based memory (~/.claude/projects/*/memory/), mem0, or any other system.`,
        '',
        memCtx,
      ];
      emit(lines.join('\n'));
    }
    return;
  }

  // ── Path 3: Not a git repo — check if any indexed repos exist ──
  const entries = readRegistry();
  if (entries.length === 0) return; // Path 4: nothing indexed, silent

  const repoList = entries.map(e => {
    const name = e.name || path.basename(e.path);
    return `  - ${name} (${e.path})`;
  }).join('\n');

  emit([
    `[CodeIndex] You are not in a git repository, but CodeIndex has ${entries.length} indexed project(s):`,
    repoList,
    ``,
    `If you want to work on one of these, \`cd\` into its directory first.`,
    `Or if this directory contains git repos, \`cd\` into one and run \`codeindex analyze\` to index it.`,
  ].join('\n'));
}

main();
