#!/usr/bin/env node
/**
 * Cross-platform VictoriaLogs MCP launcher.
 * Resolves the binary from ~/bin/mcp-victorialogs (Mac/Linux)
 * or ~/bin/mcp-victorialogs.exe (Windows).
 *
 * Install the binary first:
 *   Mac/Linux: ~/bin/mcp-victorialogs
 *   Windows:   %USERPROFILE%\bin\mcp-victorialogs.exe
 *
 * Env vars forwarded from .mcp.json:
 *   VL_INSTANCE_ENTRYPOINT — VictoriaLogs URL (via SSH tunnel)
 *   VL_INSTANCE_HEADERS    — Authorization header (Basic auth)
 */
import { execFileSync, spawn } from 'child_process';
import { homedir, platform } from 'os';
import { join } from 'path';

const isWin = platform() === 'win32';
const binName = isWin ? 'mcp-victorialogs.exe' : 'mcp-victorialogs';
const binPath = join(homedir(), 'bin', binName);

function readLoginShellEnv(name) {
  if (isWin) {
    return '';
  }

  try {
    return execFileSync('zsh', ['-lc', `printf %s "$${name}"`], {
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'ignore'],
      timeout: 3000,
    }).trim();
  } catch {
    return '';
  }
}

const env = { ...process.env };
const loginShellAuth = readLoginShellEnv('VICTORIALOGS_BASIC_AUTH_B64');

if (loginShellAuth) {
  env.VICTORIALOGS_BASIC_AUTH_B64 = loginShellAuth;
  env.VL_INSTANCE_HEADERS = `Authorization=Basic ${loginShellAuth}`;
}

const child = spawn(binPath, [], {
  stdio: 'inherit',
  env,
});

child.on('exit', (code) => process.exit(code ?? 0));
child.on('error', (err) => {
  process.stderr.write(`victorialogs-launcher error: ${err.message}\n`);
  process.stderr.write(`Expected binary at: ${binPath}\n`);
  process.exit(1);
});
