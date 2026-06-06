#!/usr/bin/env node
/**
 * Cross-platform Playwright MCP launcher.
 *
 * Default pattern: persistent profile, parallel-safe via workspace-hash.
 * Fallback: `--isolated` (ephemeral profile, logged-out) when the env
 * var `PLAYWRIGHT_ISOLATED=1` is set.
 *
 * Why default is persistent (verified 2026-05-13 against Microsoft docs):
 *   @playwright/mcp@latest stores its persistent profile at
 *   `~/Library/Caches/ms-playwright/mcp-{channel}-{workspace-hash}` on
 *   macOS (analogous paths on Linux/Windows). The `{workspace-hash}` is
 *   derived from the MCP client's workspace root, so every distinct
 *   workspace gets its own profile directory automatically.
 *
 *   In our setup (Conductor + a canonical clone) each parallel session
 *   runs from a different workspace root, so each picks a different
 *   hash, so each opens a different profile. No lock conflict. Login
 *   state persists between Claude Code restarts within that workspace.
 *
 *   The one situation where the default would still conflict is two
 *   Claude Code instances inside the SAME workspace touching the
 *   Playwright MCP simultaneously. We don't do that. If you ever
 *   need it, set PLAYWRIGHT_ISOLATED=1 on the second instance.
 *
 * When to use the isolated fallback (PLAYWRIGHT_ISOLATED=1):
 *   - You explicitly want a fresh, logged-out browser (testing the
 *     login flow itself, verifying unauthenticated routes, etc).
 *   - Two Claude Code instances inside the same workspace.
 *   - Disposable verification where you don't want to pollute the
 *     workspace's persistent profile.
 *
 * Storage-state files:
 *   For Klai workspaces the launcher first looks for the repo-local
 *   Voys attached-session state at
 *   `klai-portal/frontend/e2e/prod-tenant/_config/storageState.voys.json`.
 *   When present, Playwright MCP starts with the real Voys user state.
 *   This makes "test in Voys" / "test in voice" requests reliable across
 *   fresh MCP sessions.
 *
 *   Fallback remains the generic `~/.claude/mcp-storageState.json`.
 *
 *   Override with `KLAI_PLAYWRIGHT_STORAGE_STATE`:
 *     - `voys`: require the repo-local Voys state
 *     - `global`: use `~/.claude/mcp-storageState.json`
 *     - `none`: pass no storage-state
 *     - absolute path: use that exact file
 *
 *   Important: isolated e2e tenant tests do NOT rely on MCP storage-state;
 *   they run through `npm run test:e2e:prod` and log in with the bot user.
 *   Voys-attached tests use `npm run test:e2e:prod:voys`.
 *
 *   To (re)seed: open a Playwright MCP session, browser_navigate to a
 *   login URL, log in by hand, then call `browser_run_code_unsafe`:
 *     async (page) => {
 *       await page.context().storageState({
 *         path: '/Users/<you>/.claude/mcp-storageState.json',
 *       });
 *       return { url: page.url(),
 *                cookies: (await page.context().cookies()).length };
 *     }
 *
 *   NOTE: there is no separate `browser_storage_state` MCP tool. The
 *   functionality lives in the generic `browser_run_code_unsafe` tool.
 *
 * Why CLI flags instead of a JSON config file:
 *   microsoft/playwright-mcp#1446 — `userDataDir` set in a JSON
 *   `--config` file is silently ignored on `@playwright/mcp@0.0.70`.
 *   CLI flags work correctly.
 *
 * Why `--browser chrome`:
 *   On `@playwright/mcp >= 0.0.74` the valid `--browser` values are
 *   chrome|firefox|webkit|msedge (chromium was dropped). `chrome` uses
 *   the system Google Chrome install; the workspace-hashed profile
 *   path means it does NOT collide with the user's regular Chrome
 *   browsing profile.
 *
 * History note: earlier versions of this launcher hardcoded `--isolated`
 * because Microsoft's docs didn't surface workspace-hashing clearly.
 * Once the workspace-hash mechanism was confirmed (2026-05-13, see
 * playwright.dev/mcp/configuration/user-profile + issue #1294), the
 * default flipped to persistent. The `--user-data-dir` single-instance
 * lock pitfall is mitigated by the hash; it only re-emerges if you
 * override with an explicit `--user-data-dir` argument.
 */
import { spawn } from 'child_process';
import { homedir, platform } from 'os';
import { isAbsolute, join, resolve } from 'path';
import { existsSync } from 'fs';

const isWin = platform() === 'win32';
const GLOBAL_STATE_FILE = join(homedir(), '.claude', 'mcp-storageState.json');
const VOYS_STATE_FILE = resolve(
  process.cwd(),
  'klai-portal',
  'frontend',
  'e2e',
  'prod-tenant',
  '_config',
  'storageState.voys.json',
);

// Default: persistent profile (login state survives between sessions).
// Opt-in `--isolated` via PLAYWRIGHT_ISOLATED=1 for the explicit
// "I want a fresh, logged-out browser" case (testing the login flow,
// incognito-style verification, etc).
const isolated = process.env.PLAYWRIGHT_ISOLATED === '1';

const args = [
  '--yes',
  '@playwright/mcp@latest',
  '--browser', 'chrome',
];

if (isolated) {
  args.push('--isolated');
}

function selectStorageState() {
  const requested = process.env.KLAI_PLAYWRIGHT_STORAGE_STATE;
  if (requested === 'none') return null;
  if (requested === 'voys') return VOYS_STATE_FILE;
  if (requested === 'global') return GLOBAL_STATE_FILE;
  if (requested && isAbsolute(requested)) return requested;
  if (existsSync(VOYS_STATE_FILE)) return VOYS_STATE_FILE;
  if (existsSync(GLOBAL_STATE_FILE)) return GLOBAL_STATE_FILE;
  return null;
}

const stateFile = selectStorageState();
if (stateFile && existsSync(stateFile)) {
  args.push('--storage-state', stateFile);
  process.stderr.write(`playwright-launcher: using storage-state ${stateFile}\n`);
} else if (stateFile) {
  process.stderr.write(
    `playwright-launcher: requested storage-state ${stateFile} not found; starting without it.\n`
  );
  if (process.env.KLAI_PLAYWRIGHT_STORAGE_STATE) {
    process.exit(1);
  }
} else if (isolated) {
  process.stderr.write(
    `playwright-launcher: no storage-state found and PLAYWRIGHT_ISOLATED=1.\n` +
    `Isolated browser starts logged-out. To seed the file from inside a\n` +
    `running MCP session: open a login URL via browser_navigate, log in by\n` +
    `hand, then have the AI call browser_run_code_unsafe with a snippet\n` +
    `that runs page.context().storageState({path}). Restart Claude Code\n` +
    `afterwards so the launcher picks up the file.\n`
  );
}

const child = spawn('npx', args, {
  stdio: 'inherit',
  shell: isWin, // Windows requires shell:true for npx to resolve
});

child.on('exit', (code) => process.exit(code ?? 0));
child.on('error', (err) => {
  process.stderr.write(`playwright-launcher error: ${err.message}\n`);
  process.exit(1);
});
