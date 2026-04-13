#!/usr/bin/env node
/**
 * CodeIndex Claude Code Hook
 *
 * PreToolUse  — intercepts Grep/Glob/Bash searches and augments
 *               with graph context from the CodeIndex index.
 * PostToolUse — detects stale index after git mutations and notifies
 *               the agent to reindex.
 *
 * Worktree-aware: resolves worktrees to their main repo via git,
 * then checks the global registry at ~/.codeindex/registry.json.
 */

const fs = require('fs');
const path = require('path');
const os = require('os');
const { execSync, spawnSync } = require('child_process');

/**
 * Read JSON input from stdin synchronously.
 */
function readInput() {
  try {
    const data = fs.readFileSync(0, 'utf-8');
    return JSON.parse(data);
  } catch {
    return {};
  }
}

/**
 * Read the global registry at ~/.codeindex/registry.json.
 */
function readRegistry() {
  try {
    const registryPath = path.join(os.homedir(), '.codeindex', 'registry.json');
    const raw = fs.readFileSync(registryPath, 'utf-8');
    const data = JSON.parse(raw);
    return Array.isArray(data) ? data : [];
  } catch {
    return [];
  }
}

/**
 * Resolve a working directory to its main repo root.
 * Handles git worktrees by using --git-common-dir.
 */
function getMainRepoRoot(cwd) {
  try {
    const commonDir = execSync('git rev-parse --git-common-dir', {
      cwd,
      encoding: 'utf-8',
      stdio: ['pipe', 'pipe', 'pipe'],
    }).trim();
    const resolved = path.resolve(cwd, commonDir);
    return path.dirname(resolved);
  } catch {
    // Fallback: try git toplevel
    try {
      return execSync('git rev-parse --show-toplevel', {
        cwd,
        encoding: 'utf-8',
        stdio: ['pipe', 'pipe', 'pipe'],
      }).trim();
    } catch {
      return null;
    }
  }
}

/**
 * Check if a cwd has an indexed CodeIndex project.
 * Worktree-aware: resolves to main repo, then checks registry.
 */
function hasIndexedRepo(cwd) {
  const mainRoot = getMainRepoRoot(cwd);
  if (!mainRoot) return false;

  const resolved = path.resolve(mainRoot);
  const entries = readRegistry();
  return entries.some(e => {
    try {
      const ep = path.resolve(e.path);
      // Exact match or subdirectory match (monorepo: git root = /repo, indexed = /repo/packages/app)
      return ep === resolved || ep.startsWith(resolved + path.sep);
    } catch {
      return false;
    }
  });
}

/**
 * Find the .codeindex directory by walking up from startDir.
 * Returns the path to .codeindex/ or null if not found.
 */
function findCodeindexDir(startDir) {
  let dir = startDir || process.cwd();
  for (let i = 0; i < 5; i++) {
    const candidate = path.join(dir, '.codeindex');
    if (fs.existsSync(candidate)) return candidate;
    const parent = path.dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  return null;
}

/**
 * Extract search pattern from tool input.
 */
function extractPattern(toolName, toolInput) {
  if (toolName === 'Grep') {
    return toolInput.pattern || null;
  }

  if (toolName === 'Glob') {
    const raw = toolInput.pattern || '';
    const match = raw.match(/[*\/]([a-zA-Z][a-zA-Z0-9_-]{2,})/);
    return match ? match[1] : null;
  }

  if (toolName === 'Bash') {
    const cmd = toolInput.command || '';
    if (!/\brg\b|\bgrep\b/.test(cmd)) return null;

    const tokens = cmd.split(/\s+/);
    let foundCmd = false;
    let skipNext = false;
    const flagsWithValues = new Set(['-e', '-f', '-m', '-A', '-B', '-C', '-g', '--glob', '-t', '--type', '--include', '--exclude']);

    for (const token of tokens) {
      if (skipNext) { skipNext = false; continue; }
      if (!foundCmd) {
        if (/\brg$|\bgrep$/.test(token)) foundCmd = true;
        continue;
      }
      if (token.startsWith('-')) {
        if (flagsWithValues.has(token)) skipNext = true;
        continue;
      }
      const cleaned = token.replace(/['"]/g, '');
      return cleaned.length >= 3 ? cleaned : null;
    }
    return null;
  }

  return null;
}

/**
 * Find the codeindex CLI entry point.
 * Tries: 1) relative to this hook (fork install), 2) global npm install.
 */
function findCliPath() {
  // Option 1: relative to this hook script (hooks/claude/ -> dist/cli/index.js)
  const relative = path.resolve(__dirname, '..', '..', 'dist', 'cli', 'index.js');
  if (fs.existsSync(relative)) return relative;

  // Option 2: find via npm global prefix
  try {
    const prefix = execSync('npm prefix -g', { encoding: 'utf-8', stdio: ['pipe', 'pipe', 'pipe'] }).trim();
    const globalPath = path.join(prefix, 'lib', 'node_modules', 'codeindex', 'dist', 'cli', 'index.js');
    if (fs.existsSync(globalPath)) return globalPath;
  } catch { /* skip */ }

  return null;
}

/**
 * Emit a hook response with additional context for the agent.
 */
function sendHookResponse(hookEventName, message) {
  console.log(JSON.stringify({
    hookSpecificOutput: { hookEventName, additionalContext: message }
  }));
}

/**
 * PreToolUse handler — augment searches with graph context.
 */
function handlePreToolUse(input) {
  const cwd = input.cwd || process.cwd();
  if (!path.isAbsolute(cwd)) return;
  if (!hasIndexedRepo(cwd)) return;

  const toolName = input.tool_name || '';
  const toolInput = input.tool_input || {};

  if (toolName !== 'Grep' && toolName !== 'Glob' && toolName !== 'Bash') return;

  // Skip Glob searches for non-code files (check raw pattern before extraction strips extension)
  if (toolName === 'Glob') {
    const rawPattern = toolInput.pattern || '';
    if (/\.(md|css|json|ya?ml|txt|png|jpe?g|svg|gif|ico|lock|env|sh|xml|html|csv|pdf|woff2?|eot|ttf)["'*}\s]*$/i.test(rawPattern)) return;
  }

  const pattern = extractPattern(toolName, toolInput);
  if (!pattern || pattern.length < 3) return;

  // Skip augmentation for non-code patterns (catches Grep/Bash patterns that include extensions)
  if (/\.(md|css|json|ya?ml|txt|png|jpe?g|svg|gif|ico|lock|env|sh|xml|html|csv|pdf|woff2?|eot|ttf)$/i.test(pattern)) return;

  const cliPath = findCliPath();
  if (!cliPath) return;

  // augment CLI writes result to stderr (KuzuDB's native module captures
  // stdout fd at OS level, making it unusable in subprocess contexts).
  let result = '';
  try {
    const child = spawnSync(
      process.execPath,
      [cliPath, 'augment', '--', pattern],
      { encoding: 'utf-8', timeout: 7000, cwd, stdio: ['pipe', 'pipe', 'pipe'] }
    );
    if (!child.error && child.status === 0) {
      result = child.stderr || '';
    }
  } catch { /* graceful failure */ }

  if (result && result.trim()) {
    sendHookResponse('PreToolUse', result.trim());
  }
}

/**
 * PostToolUse handler — detect index staleness after git mutations.
 *
 * Instead of spawning a full `codeindex analyze` synchronously (which blocks
 * the agent for up to 120s and risks KuzuDB corruption on timeout), we do a
 * lightweight staleness check: compare `git rev-parse HEAD` against the
 * lastCommit stored in `.codeindex/meta.json`. If they differ, notify the
 * agent so it can decide when to reindex.
 */
function handlePostToolUse(input) {
  const toolName = input.tool_name || '';
  if (toolName !== 'Bash') return;

  const command = (input.tool_input || {}).command || '';
  if (!/\bgit\s+(commit|merge|rebase|cherry-pick|pull)(\s|$)/.test(command)) return;

  // Only proceed if the command succeeded
  const toolOutput = input.tool_output || {};
  if (toolOutput.exit_code !== undefined && toolOutput.exit_code !== 0) return;

  const cwd = input.cwd || process.cwd();
  if (!path.isAbsolute(cwd)) return;
  const codeindexDir = findCodeindexDir(cwd);
  if (!codeindexDir) return;

  // Compare HEAD against last indexed commit — skip if unchanged
  let currentHead = '';
  try {
    const headResult = spawnSync('git', ['rev-parse', 'HEAD'], {
      encoding: 'utf-8', timeout: 3000, cwd, stdio: ['pipe', 'pipe', 'pipe'],
    });
    currentHead = (headResult.stdout || '').trim();
  } catch { return; }

  if (!currentHead) return;

  let lastCommit = '';
  let hadEmbeddings = false;
  try {
    const meta = JSON.parse(fs.readFileSync(path.join(codeindexDir, 'meta.json'), 'utf-8'));
    lastCommit = meta.lastCommit || '';
    hadEmbeddings = (meta.stats && meta.stats.embeddings > 0);
  } catch { /* no meta — treat as stale */ }

  // If HEAD matches last indexed commit, no reindex needed
  if (currentHead && currentHead === lastCommit) return;

  const analyzeCmd = `npx codeindex analyze${hadEmbeddings ? ' --embeddings' : ''}`;
  sendHookResponse('PostToolUse',
    `CodeIndex index is stale (last indexed: ${lastCommit ? lastCommit.slice(0, 7) : 'never'}). ` +
    `Run \`${analyzeCmd}\` to update the knowledge graph.`
  );
}

// Dispatch map for hook events
const handlers = {
  PreToolUse: handlePreToolUse,
  PostToolUse: handlePostToolUse,
};

function main() {
  try {
    const input = readInput();
    const handler = handlers[input.hook_event_name || ''];
    if (handler) handler(input);
  } catch (err) {
    // Graceful failure — log to stderr for debugging
    if (process.env.CODEINDEX_DEBUG) {
      console.error('CodeIndex hook error:', (err.message || '').slice(0, 200));
    }
  }
}

main();
