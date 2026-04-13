/**
 * Claude Code Hook Registration
 *
 * Ensures the CodeIndex PreToolUse hook is registered in ~/.claude/settings.json
 * using the INSTALLED hook paths (~/.claude/hooks/codeindex/), not source paths.
 *
 * Also cleans up legacy hooks.json entries that may reference development paths.
 *
 * Idempotent — safe to call multiple times.
 */

import fs from 'fs/promises';
import path from 'path';
import os from 'os';

/**
 * Register (or verify) the CodeIndex hook in Claude Code's settings.json.
 *
 * Uses ~/.claude/hooks/codeindex/ (the installed location) for all paths.
 * Never references source/development directories.
 *
 * Also removes any stale codeindex entries from the legacy hooks.json.
 */
export async function registerClaudeHook(): Promise<{ registered: boolean; message: string }> {
  const claudeDir = path.join(os.homedir(), '.claude');
  const settingsFile = path.join(claudeDir, 'settings.json');
  const hooksJsonFile = path.join(claudeDir, 'hooks.json');

  // Installed hook locations (written by `codeindex setup` / installClaudeCodeHooks)
  const installedDir = path.join(claudeDir, 'hooks', 'codeindex');
  const resolveNode = path.join(installedDir, 'resolve-node.sh');
  const hookScript = path.join(installedDir, 'codeindex-hook.cjs');

  // Check if ~/.claude/ exists (user has Claude Code installed)
  try {
    await fs.access(claudeDir);
  } catch {
    return { registered: false, message: 'Claude Code not detected (~/.claude/ not found)' };
  }

  // Check if installed hooks exist (requires `codeindex setup` to have run)
  try {
    await fs.access(hookScript);
    await fs.access(resolveNode);
  } catch {
    return { registered: false, message: 'Hooks not installed yet (run `codeindex setup` first)' };
  }

  const hookCommand = `bash "${resolveNode.replace(/\\/g, '/')}" "${hookScript.replace(/\\/g, '/')}"`;

  // ── Check settings.json for existing up-to-date hook ──
  let settings: any = {};
  try {
    settings = JSON.parse(await fs.readFile(settingsFile, 'utf-8'));
  } catch {
    // File doesn't exist or is invalid
  }

  const hasUpToDate = settings.hooks?.PreToolUse?.some((entry: any) =>
    entry.hooks?.some((h: any) =>
      h.command?.includes('codeindex') && h.command?.includes('resolve-node')
    )
  );

  let registered = false;

  if (!hasUpToDate) {
    // Ensure structure
    if (!settings.hooks) settings.hooks = {};
    if (!Array.isArray(settings.hooks.PreToolUse)) settings.hooks.PreToolUse = [];

    // Remove any old codeindex entries
    settings.hooks.PreToolUse = settings.hooks.PreToolUse.filter(
      (entry: any) => !entry.hooks?.some((h: any) => h.command?.includes('codeindex'))
    );

    // Add the correct entry
    settings.hooks.PreToolUse.push({
      matcher: 'Grep|Glob|Bash',
      hooks: [{
        type: 'command',
        command: hookCommand,
        timeout: 8000,
        statusMessage: 'Enriching with CodeIndex graph context...',
      }],
    });

    await fs.writeFile(settingsFile, JSON.stringify(settings, null, 2) + '\n', 'utf-8');
    registered = true;
  }

  // ── Clean up legacy hooks.json — remove codeindex entries ──
  await cleanLegacyHooksJson(hooksJsonFile);

  return {
    registered,
    message: registered ? 'Claude Code hook registered in settings.json' : 'Claude Code hook already registered',
  };
}

/**
 * Remove any codeindex entries from the legacy ~/.claude/hooks.json.
 * Preserves entries from other tools (e.g. gitnexus).
 */
async function cleanLegacyHooksJson(hooksJsonFile: string): Promise<void> {
  try {
    const raw = await fs.readFile(hooksJsonFile, 'utf-8');
    const config = JSON.parse(raw);
    if (!config.hooks) return;

    let changed = false;
    for (const eventType of Object.keys(config.hooks)) {
      if (!Array.isArray(config.hooks[eventType])) continue;
      const before = config.hooks[eventType].length;
      config.hooks[eventType] = config.hooks[eventType].filter(
        (entry: any) => !entry.hooks?.some((h: any) =>
          h.command?.includes('codeindex-hook') || h.command?.includes('codeindex augment')
        )
      );
      if (config.hooks[eventType].length !== before) changed = true;
    }

    if (changed) {
      await fs.writeFile(hooksJsonFile, JSON.stringify(config, null, 2) + '\n', 'utf-8');
    }
  } catch {
    // hooks.json doesn't exist or is invalid — nothing to clean
  }
}
