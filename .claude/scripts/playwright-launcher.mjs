#!/usr/bin/env node
/**
 * Cross-platform Playwright MCP launcher.
 * Auto-detects OS and sets the Brave executable path accordingly.
 * User data dir is derived from os.homedir() — works for any username.
 *
 * Mac:     /Applications/Brave Browser.app/Contents/MacOS/Brave Browser
 * Windows: C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe
 */
import { spawn } from 'child_process';
import { homedir, platform } from 'os';
import { join } from 'path';

const isWin = platform() === 'win32';

const braveExecutable = isWin
  ? 'C:\\Program Files\\BraveSoftware\\Brave-Browser\\Application\\brave.exe'
  : '/Applications/Brave Browser.app/Contents/MacOS/Brave Browser';

const userDataDir = join(homedir(), '.claude', 'mcp-brave-profile');

const child = spawn(
  'npx',
  [
    '--yes',
    '@playwright/mcp@0.0.70',
    '--browser', 'chromium',
    '--executable-path', braveExecutable,
    '--user-data-dir', userDataDir,
  ],
  {
    stdio: 'inherit',
    shell: isWin, // Windows requires shell:true for npx to resolve
  }
);

child.on('exit', (code) => process.exit(code ?? 0));
child.on('error', (err) => {
  process.stderr.write(`playwright-launcher error: ${err.message}\n`);
  process.exit(1);
});
