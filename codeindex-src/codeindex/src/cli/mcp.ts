/**
 * MCP Command
 * 
 * Starts the MCP server in standalone mode.
 * Loads all indexed repos from the global registry.
 * No longer depends on cwd — works from any directory.
 */

import { startMCPServer } from '../mcp/server.js';
import { LocalBackend } from '../mcp/local/local-backend.js';

export const mcpCommand = async () => {
  // Prevent unhandled errors from crashing the MCP server process.
  // KuzuDB lock conflicts and transient errors should degrade gracefully.
  process.on('uncaughtException', (err) => {
    console.error(`CodeIndex MCP: uncaught exception — ${err.message}`);
  });
  process.on('unhandledRejection', (reason) => {
    const msg = reason instanceof Error ? reason.message : String(reason);
    console.error(`CodeIndex MCP: unhandled rejection — ${msg}`);
  });

  // Initialize multi-repo backend from registry
  const backend = new LocalBackend();
  const ok = await backend.init();

  if (!ok) {
    console.error('CodeIndex: No indexed repositories yet. MCP server starting in standby mode.');
    console.error('  Run "codeindex analyze" in a git repo, then restart your editor.');
  } else {
    const repoNames = (await backend.listRepos()).map(r => r.name);
    console.error(`CodeIndex: MCP server starting with ${repoNames.length} repo(s): ${repoNames.join(', ')}`);
  }

  // Always start the MCP server — even with 0 repos.
  // This way Claude shows "connected" and tools return helpful messages
  // instead of "Failed to connect" which confuses users.
  await startMCPServer(backend);
};
