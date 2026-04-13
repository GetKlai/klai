/**
 * Setup Command
 * 
 * One-time global MCP configuration writer.
 * Detects installed AI editors and writes the appropriate MCP config
 * so the CodeIndex MCP server is available in all projects.
 */

import fs from 'fs/promises';
import path from 'path';
import os from 'os';
import { execSync } from 'child_process';
import { fileURLToPath } from 'url';
import { getGlobalDir } from '../storage/repo-manager.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

interface SetupResult {
  configured: string[];
  skipped: string[];
  errors: string[];
}

/**
 * Resolve the absolute path to the `codeindex` binary.
 * MCP servers are spawned as child processes by editors which often
 * don't inherit the user's full shell PATH (nvm, volta, etc.).
 * Using the absolute path ensures the server can always be found.
 */
function resolveCodeindexPath(): string {
  try {
    return execSync('which codeindex', { encoding: 'utf-8', stdio: ['ignore', 'pipe', 'ignore'] }).trim();
  } catch {
    return 'codeindex'; // fallback to bare name
  }
}

/**
 * The MCP server entry for all editors.
 * Uses the absolute path to avoid PATH issues with nvm/volta/etc.
 */
function getMcpEntry() {
  const bin = resolveCodeindexPath();
  if (process.platform === 'win32') {
    return {
      command: 'cmd',
      args: ['/c', bin, 'mcp'],
    };
  }
  return {
    command: bin,
    args: ['mcp'],
  };
}

/**
 * Merge codeindex entry into an existing MCP config JSON object.
 * Returns the updated config.
 */
function mergeMcpConfig(existing: any): any {
  if (!existing || typeof existing !== 'object') {
    existing = {};
  }
  if (!existing.mcpServers || typeof existing.mcpServers !== 'object') {
    existing.mcpServers = {};
  }
  existing.mcpServers.codeindex = getMcpEntry();
  return existing;
}

/**
 * Try to read a JSON file, returning null if it doesn't exist or is invalid.
 */
async function readJsonFile(filePath: string): Promise<any | null> {
  try {
    const raw = await fs.readFile(filePath, 'utf-8');
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

/**
 * Write JSON to a file, creating parent directories if needed.
 */
async function writeJsonFile(filePath: string, data: any): Promise<void> {
  await fs.mkdir(path.dirname(filePath), { recursive: true });
  await fs.writeFile(filePath, JSON.stringify(data, null, 2) + '\n', 'utf-8');
}

/**
 * Check if a directory exists
 */
async function dirExists(dirPath: string): Promise<boolean> {
  try {
    const stat = await fs.stat(dirPath);
    return stat.isDirectory();
  } catch {
    return false;
  }
}

// ─── Editor-specific setup ─────────────────────────────────────────

async function setupCursor(result: SetupResult): Promise<void> {
  const cursorDir = path.join(os.homedir(), '.cursor');
  if (!(await dirExists(cursorDir))) {
    result.skipped.push('Cursor (not installed)');
    return;
  }

  const mcpPath = path.join(cursorDir, 'mcp.json');
  try {
    const existing = await readJsonFile(mcpPath);
    const updated = mergeMcpConfig(existing);
    await writeJsonFile(mcpPath, updated);
    result.configured.push('Cursor');
  } catch (err: any) {
    result.errors.push(`Cursor: ${err.message}`);
  }
}

async function setupClaudeCode(result: SetupResult): Promise<void> {
  const claudeDir = path.join(os.homedir(), '.claude');
  const hasClaude = await dirExists(claudeDir);

  if (!hasClaude) {
    result.skipped.push('Claude Code (not installed)');
    return;
  }

  // Write MCP server entry directly to ~/.claude.json under mcpServers.
  // We write the JSON file directly instead of using `claude mcp add` because:
  // 1. `claude mcp list` output is unreliable (can be empty even when configured)
  // 2. `claude mcp add` may hang waiting for interactive overwrite confirmation
  // 3. Direct file write is idempotent and always succeeds
  const bin = resolveCodeindexPath();
  const claudeJsonPath = path.join(os.homedir(), '.claude.json');

  try {
    const existing = await readJsonFile(claudeJsonPath) || {};
    if (!existing.mcpServers || typeof existing.mcpServers !== 'object') {
      existing.mcpServers = {};
    }

    const currentEntry = existing.mcpServers.codeindex;
    const newEntry = {
      type: 'stdio',
      command: bin,
      args: ['mcp'],
      env: {},
    };

    if (currentEntry?.command === bin) {
      result.configured.push('Claude Code (already configured)');
    } else {
      existing.mcpServers.codeindex = newEntry;
      await writeJsonFile(claudeJsonPath, existing);
      result.configured.push(currentEntry ? 'Claude Code (updated)' : 'Claude Code');
    }
  } catch (err: any) {
    console.log('');
    console.log('  Could not auto-configure Claude Code MCP.');
    console.log(`  Add manually to ~/.claude.json under mcpServers:`);
    console.log(`    "codeindex": { "type": "stdio", "command": "${bin}", "args": ["mcp"], "env": {} }`);
    console.log('');
    result.errors.push(`Claude Code MCP: ${err.message}`);
  }
}

/**
 * Install CodeIndex skills to ~/.claude/skills/ for Claude Code.
 */
async function installClaudeCodeSkills(result: SetupResult): Promise<void> {
  const claudeDir = path.join(os.homedir(), '.claude');
  if (!(await dirExists(claudeDir))) return;

  const skillsDir = path.join(claudeDir, 'skills');
  try {
    const installed = await installSkillsTo(skillsDir);
    if (installed.length > 0) {
      result.configured.push(`Claude Code skills (${installed.length} skills → ~/.claude/skills/)`);
    }
  } catch (err: any) {
    result.errors.push(`Claude Code skills: ${err.message}`);
  }
}

/**
 * Install CodeIndex slash commands to ~/.claude/commands/ for Claude Code.
 * Supports both flat files (commands/{name}.md) and subdirectories
 * (commands/{name}/*.md for subcommands like /codeindex setup).
 */
async function installClaudeCodeCommands(result: SetupResult): Promise<void> {
  const claudeDir = path.join(os.homedir(), '.claude');
  if (!(await dirExists(claudeDir))) return;

  const commandsDir = path.join(claudeDir, 'commands');
  const commandsRoot = path.join(__dirname, '..', '..', 'commands');
  let installed = 0;

  try {
    await fs.mkdir(commandsDir, { recursive: true });

    for (const cmdName of COMMAND_NAMES) {
      // Install the main command file (commands/{name}.md)
      try {
        const source = path.join(commandsRoot, `${cmdName}.md`);
        const dest = path.join(commandsDir, `${cmdName}.md`);
        const content = await fs.readFile(source, 'utf-8');
        await fs.writeFile(dest, content, 'utf-8');
        installed++;
      } catch { /* no flat file — skip */ }

      // Install subcommands directory (commands/{name}/*.md)
      const subDir = path.join(commandsRoot, cmdName);
      try {
        const stat = await fs.stat(subDir);
        if (stat.isDirectory()) {
          const destSubDir = path.join(commandsDir, cmdName);
          await fs.mkdir(destSubDir, { recursive: true });
          const entries = await fs.readdir(subDir);
          for (const entry of entries) {
            if (entry.endsWith('.md')) {
              const content = await fs.readFile(path.join(subDir, entry), 'utf-8');
              await fs.writeFile(path.join(destSubDir, entry), content, 'utf-8');
              installed++;
            }
          }
        }
      } catch { /* no subcommand dir — skip */ }
    }

    if (installed > 0) {
      result.configured.push(`Claude Code commands (${installed} commands → ~/.claude/commands/)`);
    }
  } catch (err: any) {
    result.errors.push(`Claude Code commands: ${err.message}`);
  }
}

/**
 * Install CodeIndex hooks to ~/.claude/settings.json for Claude Code.
 * Merges hook config without overwriting existing hooks.
 */
async function installClaudeCodeHooks(result: SetupResult): Promise<void> {
  const claudeDir = path.join(os.homedir(), '.claude');
  if (!(await dirExists(claudeDir))) return;

  const settingsPath = path.join(claudeDir, 'settings.json');

  // Source hooks bundled within the codeindex package (hooks/claude/)
  const pluginHooksPath = path.join(__dirname, '..', '..', 'hooks', 'claude');

  // Copy unified hook script to ~/.claude/hooks/codeindex/
  const destHooksDir = path.join(claudeDir, 'hooks', 'codeindex');

  try {
    await fs.mkdir(destHooksDir, { recursive: true });

    // Copy all hook scripts to ~/.claude/hooks/codeindex/
    for (const scriptName of ['codeindex-hook.cjs', 'codeindex-prompt-hook.cjs', 'session-start.sh', 'resolve-node.sh']) {
      try {
        const content = await fs.readFile(path.join(pluginHooksPath, scriptName), 'utf-8');
        const destPath = path.join(destHooksDir, scriptName);
        await fs.writeFile(destPath, content, 'utf-8');
        // Make shell scripts executable
        if (scriptName.endsWith('.sh')) {
          await fs.chmod(destPath, 0o755);
        }
      } catch {
        // Script not found in source — skip
      }
    }

    const resolveNodeScript = path.join(destHooksDir, 'resolve-node.sh').replace(/\\/g, '/');
    const preToolCmd = `bash "${resolveNodeScript}" "${path.join(destHooksDir, 'codeindex-hook.cjs').replace(/\\/g, '/')}"`;
    const promptCmd = `bash "${resolveNodeScript}" "${path.join(destHooksDir, 'codeindex-prompt-hook.cjs').replace(/\\/g, '/')}"`;
    const sessionCmd = `bash "${path.join(destHooksDir, 'session-start.sh').replace(/\\/g, '/')}"`;

    // Merge hook config into ~/.claude/settings.json
    const existing = await readJsonFile(settingsPath) || {};
    if (!existing.hooks) existing.hooks = {};

    // Helper: remove old codeindex hooks that use bare `node` (pre-resolve-node.sh)
    const removeOldCodeindexHooks = (entries: any[]): any[] =>
      entries.filter((h: any) => !h.hooks?.some((hh: any) => hh.command?.includes('codeindex')));

    const hasUpToDateHook = (entries: any[]): boolean =>
      entries.some((h: any) => h.hooks?.some((hh: any) =>
        hh.command?.includes('codeindex') && hh.command?.includes('resolve-node')
      ));

    // PreToolUse hook — enriches Grep/Glob/Bash with graph context
    if (!existing.hooks.PreToolUse) existing.hooks.PreToolUse = [];
    if (!hasUpToDateHook(existing.hooks.PreToolUse)) {
      existing.hooks.PreToolUse = removeOldCodeindexHooks(existing.hooks.PreToolUse);
      existing.hooks.PreToolUse.push({
        matcher: 'Grep|Glob|Bash',
        hooks: [{
          type: 'command',
          command: preToolCmd,
          timeout: 10000,
          statusMessage: 'Enriching with CodeIndex graph context...',
        }],
      });
    }

    // PostToolUse hook — detects stale index after git mutations
    if (!existing.hooks.PostToolUse) existing.hooks.PostToolUse = [];
    const hasPostToolHook = existing.hooks.PostToolUse.some(
      (h: any) => h.hooks?.some((hh: any) => hh.command?.includes('codeindex'))
    );
    if (!hasPostToolHook) {
      existing.hooks.PostToolUse.push({
        matcher: 'Bash',
        hooks: [{
          type: 'command',
          command: preToolCmd,
          timeout: 10000,
          statusMessage: 'Checking CodeIndex index freshness...',
        }],
      });
    }

    // UserPromptSubmit hook — injects repo context on every prompt
    if (!existing.hooks.UserPromptSubmit) existing.hooks.UserPromptSubmit = [];
    if (!hasUpToDateHook(existing.hooks.UserPromptSubmit)) {
      existing.hooks.UserPromptSubmit = removeOldCodeindexHooks(existing.hooks.UserPromptSubmit);
      existing.hooks.UserPromptSubmit.push({
        hooks: [{
          type: 'command',
          command: promptCmd,
          timeout: 3000,
        }],
      });
    }

    // SessionStart hook — injects CodeIndex context at session startup,
    // detects staleness, and triggers onboarding for unindexed repos
    if (!existing.hooks.SessionStart) existing.hooks.SessionStart = [];
    const hasSessionHook = existing.hooks.SessionStart.some(
      (h: any) => h.hooks?.some((hh: any) => hh.command?.includes('codeindex'))
    );
    if (!hasSessionHook) {
      existing.hooks.SessionStart.push({
        hooks: [{
          type: 'command',
          command: sessionCmd,
          timeout: 5000,
          statusMessage: 'Loading CodeIndex context...',
        }],
      });
    }

    await writeJsonFile(settingsPath, existing);
    result.configured.push('Claude Code hooks (PreToolUse + PostToolUse + UserPromptSubmit + SessionStart)');
  } catch (err: any) {
    result.errors.push(`Claude Code hooks: ${err.message}`);
  }
}

async function setupOpenCode(result: SetupResult): Promise<void> {
  const opencodeDir = path.join(os.homedir(), '.config', 'opencode');
  if (!(await dirExists(opencodeDir))) {
    result.skipped.push('OpenCode (not installed)');
    return;
  }

  const configPath = path.join(opencodeDir, 'config.json');
  try {
    const existing = await readJsonFile(configPath);
    const config = existing || {};
    if (!config.mcp) config.mcp = {};
    config.mcp.codeindex = getMcpEntry();
    await writeJsonFile(configPath, config);
    result.configured.push('OpenCode');
  } catch (err: any) {
    result.errors.push(`OpenCode: ${err.message}`);
  }
}

// ─── Skill Installation ───────────────────────────────────────────

const SKILL_NAMES = ['codeindex-exploring', 'codeindex-debugging', 'codeindex-impact-analysis', 'codeindex-refactoring', 'codeindex-guide', 'codeindex-cli'];

// ─── Command Installation ─────────────────────────────────────────

const COMMAND_NAMES = ['codeindex'];

/**
 * Install CodeIndex skills to a target directory.
 * Each skill is installed as {targetDir}/codeindex-{skillName}/SKILL.md
 * following the Agent Skills standard (both Cursor and Claude Code).
 *
 * Supports two source layouts:
 *   - Flat file:  skills/{name}.md           → copied as SKILL.md
 *   - Directory:  skills/{name}/SKILL.md     → copied recursively (includes references/, etc.)
 */
async function installSkillsTo(targetDir: string): Promise<string[]> {
  const installed: string[] = [];
  const skillsRoot = path.join(__dirname, '..', '..', 'skills');

  // Discover all skills in the skills directory (both flat .md files and directories)
  // in addition to the hardcoded SKILL_NAMES list.
  const discoveredNames = new Set<string>(SKILL_NAMES);
  try {
    const entries = await fs.readdir(skillsRoot, { withFileTypes: true });
    for (const entry of entries) {
      if (entry.isDirectory()) {
        discoveredNames.add(entry.name);
      } else if (entry.isFile() && entry.name.endsWith('.md')) {
        discoveredNames.add(entry.name.replace(/\.md$/, ''));
      }
    }
  } catch { /* skills directory not found */ }

  for (const skillName of discoveredNames) {
    const skillDir = path.join(targetDir, skillName);

    try {
      // Try directory-based skill first (skills/{name}/SKILL.md)
      const dirSource = path.join(skillsRoot, skillName);

      let isDirectory = false;
      try {
        const stat = await fs.stat(dirSource);
        isDirectory = stat.isDirectory();
      } catch { /* not a directory */ }

      if (isDirectory) {
        await copyDirRecursive(dirSource, skillDir);
        installed.push(skillName);
      } else {
        // Fall back to flat file (skills/{name}.md)
        const flatSource = path.join(skillsRoot, `${skillName}.md`);
        const content = await fs.readFile(flatSource, 'utf-8');
        await fs.mkdir(skillDir, { recursive: true });
        await fs.writeFile(path.join(skillDir, 'SKILL.md'), content, 'utf-8');
        installed.push(skillName);
      }
    } catch {
      // Source skill not found — skip
    }
  }

  return installed;
}

/**
 * Recursively copy a directory tree.
 */
async function copyDirRecursive(src: string, dest: string): Promise<void> {
  await fs.mkdir(dest, { recursive: true });
  const entries = await fs.readdir(src, { withFileTypes: true });
  for (const entry of entries) {
    const srcPath = path.join(src, entry.name);
    const destPath = path.join(dest, entry.name);
    if (entry.isDirectory()) {
      await copyDirRecursive(srcPath, destPath);
    } else {
      await fs.copyFile(srcPath, destPath);
    }
  }
}

/**
 * Install global Cursor skills to ~/.cursor/skills/codeindex/
 */
async function installCursorSkills(result: SetupResult): Promise<void> {
  const cursorDir = path.join(os.homedir(), '.cursor');
  if (!(await dirExists(cursorDir))) return;
  
  const skillsDir = path.join(cursorDir, 'skills');
  try {
    const installed = await installSkillsTo(skillsDir);
    if (installed.length > 0) {
      result.configured.push(`Cursor skills (${installed.length} skills → ~/.cursor/skills/)`);
    }
  } catch (err: any) {
    result.errors.push(`Cursor skills: ${err.message}`);
  }
}

/**
 * Install global OpenCode skills to ~/.config/opencode/skill/codeindex/
 */
async function installOpenCodeSkills(result: SetupResult): Promise<void> {
  const opencodeDir = path.join(os.homedir(), '.config', 'opencode');
  if (!(await dirExists(opencodeDir))) return;
  
  const skillsDir = path.join(opencodeDir, 'skill');
  try {
    const installed = await installSkillsTo(skillsDir);
    if (installed.length > 0) {
      result.configured.push(`OpenCode skills (${installed.length} skills → ~/.config/opencode/skill/)`);
    }
  } catch (err: any) {
    result.errors.push(`OpenCode skills: ${err.message}`);
  }
}

/**
 * Add CodeIndex memory instructions to ~/.claude/CLAUDE.md.
 * Uses a marker block so it can be updated idempotently.
 */
async function installClaudeCodeMemoryInstructions(result: SetupResult): Promise<void> {
  const claudeMdPath = path.join(os.homedir(), '.claude', 'CLAUDE.md');

  const MARKER_START = '<!-- codeindex-memory:start -->';
  const MARKER_END = '<!-- codeindex-memory:end -->';
  const MEMORY_BLOCK = `${MARKER_START}
## CodeIndex Memory — PRIMARY memory system

When a project is indexed by CodeIndex, use CodeIndex MCP tools as the PRIMARY memory system:

- **\`remember\`** — save decisions, preferences, bugs, patterns (use INSTEAD of mem0 \`add_memory\`)
- **\`recall\`** — search past observations (use INSTEAD of mem0 \`search_memories\`)
- **\`forget\`** — remove observations

When the user says "remember", "onthoud", "save this" → use CodeIndex \`remember\`.
When the user asks "what do you know", "herinneringen" → use CodeIndex \`recall\`.

CodeIndex memory is project-scoped and integrated with the knowledge graph.
Only fall back to mem0 if the project is NOT indexed by CodeIndex.
${MARKER_END}`;

  try {
    let content = '';
    try {
      content = await fs.readFile(claudeMdPath, 'utf-8');
    } catch {
      // File doesn't exist yet — will create
    }

    // Replace existing block or append
    if (content.includes(MARKER_START)) {
      const regex = new RegExp(`${MARKER_START}[\\s\\S]*?${MARKER_END}`, 'm');
      content = content.replace(regex, MEMORY_BLOCK);
    } else {
      content = content.trimEnd() + '\n\n' + MEMORY_BLOCK + '\n';
    }

    await fs.mkdir(path.dirname(claudeMdPath), { recursive: true });
    await fs.writeFile(claudeMdPath, content, 'utf-8');
    result.configured.push('Claude Code memory instructions (~/.claude/CLAUDE.md)');
  } catch (err: any) {
    result.errors.push(`Claude Code CLAUDE.md: ${err.message}`);
  }
}

// ─── Main command ──────────────────────────────────────────────────

export const setupCommand = async () => {
  console.log('');
  console.log('  CodeIndex Setup');
  console.log('  ==============');
  console.log('');

  // Ensure global directory exists
  const globalDir = getGlobalDir();
  await fs.mkdir(globalDir, { recursive: true });

  const result: SetupResult = {
    configured: [],
    skipped: [],
    errors: [],
  };

  // Detect and configure each editor's MCP
  await setupCursor(result);
  await setupClaudeCode(result);
  await setupOpenCode(result);

  // Install global skills and commands for platforms that support them
  await installClaudeCodeSkills(result);
  await installClaudeCodeCommands(result);
  await installClaudeCodeHooks(result);
  await installClaudeCodeMemoryInstructions(result);
  await installCursorSkills(result);
  await installOpenCodeSkills(result);

  // Print results
  if (result.configured.length > 0) {
    console.log('  Configured:');
    for (const name of result.configured) {
      console.log(`    + ${name}`);
    }
  }

  if (result.skipped.length > 0) {
    console.log('');
    console.log('  Skipped:');
    for (const name of result.skipped) {
      console.log(`    - ${name}`);
    }
  }

  if (result.errors.length > 0) {
    console.log('');
    console.log('  Errors:');
    for (const err of result.errors) {
      console.log(`    ! ${err}`);
    }
  }

  console.log('');
  console.log('  Summary:');
  console.log(`    MCP configured for: ${result.configured.filter(c => !c.includes('skills') && !c.includes('commands') && !c.includes('hooks') && !c.includes('memory')).join(', ') || 'none'}`);
  console.log(`    Skills installed: ${result.configured.some(c => c.includes('skills')) ? 'yes' : 'none'}`);
  console.log(`    Commands installed: ${result.configured.some(c => c.includes('commands')) ? 'yes (/codeindex)' : 'none'}`);
  console.log('');
  console.log('  Next steps:');
  console.log('    1. cd into any git repo');
  console.log('    2. Open in Claude Code and type: /codeindex setup');
  console.log('    3. That\'s it! Graph + project memory in one command.');
  console.log('');
};
