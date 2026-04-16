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
import { spawn } from 'child_process';
import { homedir, platform } from 'os';
import { join } from 'path';

const isWin = platform() === 'win32';
const binName = isWin ? 'mcp-victorialogs.exe' : 'mcp-victorialogs';
const binPath = join(homedir(), 'bin', binName);

const child = spawn(binPath, [], {
  stdio: 'inherit',
  // Inherit parent env so VL_INSTANCE_ENTRYPOINT and VL_INSTANCE_HEADERS pass through
});

child.on('exit', (code) => process.exit(code ?? 0));
child.on('error', (err) => {
  process.stderr.write(`victorialogs-launcher error: ${err.message}\n`);
  process.stderr.write(`Expected binary at: ${binPath}\n`);
  process.exit(1);
});
