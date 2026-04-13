/**
 * HTTP API Server
 *
 * REST API for browser-based clients to query the local .codeindex/ index.
 * Also hosts the MCP server over StreamableHTTP for remote AI tool access.
 *
 * Security: binds to 127.0.0.1 by default (use --host to override).
 * CORS is restricted to localhost and the deployed site.
 */

import express from 'express';
import cors from 'cors';
import path from 'path';
import os from 'os';
import fs from 'fs/promises';
import fsSync from 'fs';
import { fileURLToPath } from 'url';
import { loadMeta, listRegisteredRepos } from '../storage/repo-manager.js';
import { executeQuery, closeKuzu, withKuzuDb } from '../core/lbug/lbug-adapter.js';
import { NODE_TABLES } from '../core/lbug/schema.js';
import { GraphNode, GraphRelationship } from '../core/graph/types.js';
import { searchFTSFromKuzu } from '../core/search/bm25-index.js';
import { hybridSearch } from '../core/search/hybrid-search.js';
import { semanticSearch } from '../core/embeddings/embedding-pipeline.js';
import { isEmbedderReady } from '../core/embeddings/embedder.js';
import { LocalBackend } from '../mcp/local/local-backend.js';
import { mountMCPEndpoints } from './mcp-http.js';
import { initMemoryDb, isMemoryDbReady } from '../core/memory/memory-adapter.js';
import { migrateAllMemories } from '../core/memory/migrate-kuzu-to-lbug.js';
import {
  searchObservations,
  getObservation,
  countObservations,
  deleteObservation,
  hybridSearchObservations,
  listByType,
} from '../core/memory/observation-store.js';
import { OBSERVATION_TYPES, GLOBAL_PROJECT_NAME } from '../core/memory/types.js';
import type { ObservationType } from '../core/memory/types.js';

// ── Auto-migration for incompatible database formats ──────────────────
// Tracks which repos are currently being re-indexed to avoid duplicate spawns
const reindexingRepos = new Set<string>();

/**
 * Trigger background re-indexing when an incompatible database format is detected.
 * Spawns `codeindex analyze` as a child process and updates the registry on completion.
 */
const triggerReindex = async (repoName: string, repoPath: string): Promise<void> => {
  if (reindexingRepos.has(repoName)) return;
  reindexingRepos.add(repoName);

  console.log(`[migrate] Re-indexing ${repoName} from ${repoPath}...`);

  const { spawn } = await import('child_process');
  const cliPath = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'cli', 'index.js');

  const child = spawn(process.execPath, [cliPath, 'analyze', repoName, repoPath], {
    stdio: ['ignore', 'pipe', 'pipe'],
    env: { ...process.env, FORCE_COLOR: '0' },
  });

  child.stdout?.on('data', (data: Buffer) => {
    const line = data.toString().trim();
    if (line) console.log(`[migrate:${repoName}] ${line}`);
  });
  child.stderr?.on('data', (data: Buffer) => {
    const line = data.toString().trim();
    if (line) console.warn(`[migrate:${repoName}] ${line}`);
  });

  child.on('close', (code) => {
    reindexingRepos.delete(repoName);
    if (code === 0) {
      console.log(`[migrate] ${repoName} re-indexed successfully.`);
    } else {
      console.error(`[migrate] ${repoName} re-index failed (exit code ${code}).`);
    }
  });
};

const buildGraph = async (): Promise<{ nodes: GraphNode[]; relationships: GraphRelationship[] }> => {
  const nodes: GraphNode[] = [];
  for (const table of NODE_TABLES) {
    try {
      let query = '';
      if (table === 'File') {
        query = `MATCH (n:File) RETURN n.id AS id, n.name AS name, n.filePath AS filePath, n.content AS content`;
      } else if (table === 'Folder') {
        query = `MATCH (n:Folder) RETURN n.id AS id, n.name AS name, n.filePath AS filePath`;
      } else if (table === 'Community') {
        query = `MATCH (n:Community) RETURN n.id AS id, n.label AS label, n.heuristicLabel AS heuristicLabel, n.cohesion AS cohesion, n.symbolCount AS symbolCount`;
      } else if (table === 'Process') {
        query = `MATCH (n:Process) RETURN n.id AS id, n.label AS label, n.heuristicLabel AS heuristicLabel, n.processType AS processType, n.stepCount AS stepCount, n.communities AS communities, n.entryPointId AS entryPointId, n.terminalId AS terminalId`;
      } else {
        query = `MATCH (n:${table}) RETURN n.id AS id, n.name AS name, n.filePath AS filePath, n.startLine AS startLine, n.endLine AS endLine, n.content AS content`;
      }

      const rows = await executeQuery(query);
      for (const row of rows) {
        nodes.push({
          id: row.id ?? row[0],
          label: table as GraphNode['label'],
          properties: {
            name: row.name ?? row.label ?? row[1],
            filePath: row.filePath ?? row[2],
            startLine: row.startLine,
            endLine: row.endLine,
            content: row.content,
            heuristicLabel: row.heuristicLabel,
            cohesion: row.cohesion,
            symbolCount: row.symbolCount,
            processType: row.processType,
            stepCount: row.stepCount,
            communities: row.communities,
            entryPointId: row.entryPointId,
            terminalId: row.terminalId,
          } as GraphNode['properties'],
        });
      }
    } catch {
      // ignore empty tables
    }
  }

  const relationships: GraphRelationship[] = [];
  const relRows = await executeQuery(
    `MATCH (a)-[r:CodeRelation]->(b) RETURN a.id AS sourceId, b.id AS targetId, r.type AS type, r.confidence AS confidence, r.reason AS reason, r.step AS step`
  );
  for (const row of relRows) {
    relationships.push({
      id: `${row.sourceId}_${row.type}_${row.targetId}`,
      type: row.type,
      sourceId: row.sourceId,
      targetId: row.targetId,
      confidence: row.confidence,
      reason: row.reason,
      step: row.step,
    });
  }

  return { nodes, relationships };
};

const statusFromError = (err: any): number => {
  const msg = String(err?.message ?? '');
  if (msg.includes('No indexed repositories') || msg.includes('not found')) return 404;
  if (msg.includes('Multiple repositories')) return 400;
  return 500;
};

const requestedRepo = (req: express.Request): string | undefined => {
  const fromQuery = typeof req.query.repo === 'string' ? req.query.repo : undefined;
  if (fromQuery) return fromQuery;

  if (req.body && typeof req.body === 'object' && typeof req.body.repo === 'string') {
    return req.body.repo;
  }

  return undefined;
};

export const createServer = async (port: number, host: string = '127.0.0.1') => {
  const app = express();

  // CORS: only allow localhost origins and the deployed site.
  // Non-browser requests (curl, server-to-server) have no origin and are allowed.
  app.use(cors({
    origin: (origin, callback) => {
      if (
        !origin
        || origin.startsWith('http://localhost:')
        || origin.startsWith('http://127.0.0.1:')
        || origin === 'https://codeindex.vercel.app'
      ) {
        callback(null, true);
      } else {
        callback(new Error('Not allowed by CORS'));
      }
    }
  }));
  app.use(express.json({ limit: '10mb' }));

  // Initialize MCP backend (multi-repo, shared across all MCP sessions)
  const backend = new LocalBackend();
  await backend.init();
  const cleanupMcp = mountMCPEndpoints(app, backend);

  // Helper: resolve a repo by name from the global registry, or default to first
  const resolveRepo = async (repoName?: string) => {
    const repos = await listRegisteredRepos();
    if (repos.length === 0) return null;
    if (repoName) return repos.find(r => r.name === repoName) || null;
    return repos[0]; // default to first
  };

  // List all registered repos
  app.get('/api/repos', async (_req, res) => {
    try {
      const repos = await listRegisteredRepos();
      res.json(repos.map(r => ({
        name: r.name, path: r.path, indexedAt: r.indexedAt,
        lastCommit: r.lastCommit, stats: r.stats,
      })));
    } catch (err: any) {
      res.status(500).json({ error: err.message || 'Failed to list repos' });
    }
  });

  // Get repo info
  app.get('/api/repo', async (req, res) => {
    try {
      const entry = await resolveRepo(requestedRepo(req));
      if (!entry) {
        res.status(404).json({ error: 'Repository not found. Run: codeindex analyze' });
        return;
      }
      const meta = await loadMeta(entry.storagePath);
      res.json({
        name: entry.name,
        repoPath: entry.path,
        indexedAt: meta?.indexedAt ?? entry.indexedAt,
        stats: meta?.stats ?? entry.stats ?? {},
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message || 'Failed to get repo info' });
    }
  });

  // Get full graph
  app.get('/api/graph', async (req, res) => {
    const entry = await resolveRepo(requestedRepo(req));
    if (!entry) {
      res.status(404).json({ error: 'Repository not found' });
      return;
    }
    try {
      const lbugPath = path.join(entry.storagePath, 'lbug');
      const graph = await withKuzuDb(lbugPath, async () => buildGraph(), { readOnly: true });
      res.json(graph);
    } catch (err: any) {
      if (err?.code === 'INCOMPATIBLE_DB_FORMAT') {
        const meta = await loadMeta(entry.storagePath);
        if (meta?.repoPath) triggerReindex(entry.name, meta.repoPath);
        res.status(503).json({
          error: 'Database is being re-indexed (incompatible format detected)',
          reindexing: true,
        });
        return;
      }
      res.status(500).json({ error: err.message || 'Failed to build graph' });
    }
  });

  // Execute Cypher query
  app.post('/api/query', async (req, res) => {
    const cypher = (req.body?.cypher as string) ?? '';
    if (!cypher) {
      res.status(400).json({ error: 'Missing "cypher" in request body' });
      return;
    }
    const entry = await resolveRepo(requestedRepo(req));
    if (!entry) {
      res.status(404).json({ error: 'Repository not found' });
      return;
    }
    try {
      const lbugPath = path.join(entry.storagePath, 'lbug');
      const result = await withKuzuDb(lbugPath, () => executeQuery(cypher), { readOnly: true });
      res.json({ result });
    } catch (err: any) {
      if (err?.code === 'INCOMPATIBLE_DB_FORMAT') {
        const meta = await loadMeta(entry.storagePath);
        if (meta?.repoPath) triggerReindex(entry.name, meta.repoPath);
        res.status(503).json({ error: 'Database is being re-indexed', reindexing: true });
        return;
      }
      res.status(500).json({ error: err.message || 'Query failed' });
    }
  });

  // Search
  app.post('/api/search', async (req, res) => {
    try {
      const query = (req.body.query ?? '').trim();
      if (!query) {
        res.status(400).json({ error: 'Missing "query" in request body' });
        return;
      }

      const entry = await resolveRepo(requestedRepo(req));
      if (!entry) {
        res.status(404).json({ error: 'Repository not found' });
        return;
      }
      const lbugPath = path.join(entry.storagePath, 'lbug');
      const parsedLimit = Number(req.body.limit ?? 10);
      const limit = Number.isFinite(parsedLimit)
        ? Math.max(1, Math.min(100, Math.trunc(parsedLimit)))
        : 10;

      const results = await withKuzuDb(lbugPath, async () => {
        if (isEmbedderReady()) {
          return hybridSearch(query, limit, executeQuery, semanticSearch);
        }
        // FTS-only fallback when embeddings aren't loaded
        return searchFTSFromKuzu(query, limit);
      }, { readOnly: true });
      res.json({ results });
    } catch (err: any) {
      res.status(500).json({ error: err.message || 'Search failed' });
    }
  });

  // Read file — with path traversal guard
  app.get('/api/file', async (req, res) => {
    try {
      const entry = await resolveRepo(requestedRepo(req));
      if (!entry) {
        res.status(404).json({ error: 'Repository not found' });
        return;
      }
      const filePath = req.query.path as string;
      if (!filePath) {
        res.status(400).json({ error: 'Missing path' });
        return;
      }

      // Prevent path traversal — resolve and verify the path stays within the repo root
      const repoRoot = path.resolve(entry.path);
      const fullPath = path.resolve(repoRoot, filePath);
      if (!fullPath.startsWith(repoRoot + path.sep) && fullPath !== repoRoot) {
        res.status(403).json({ error: 'Path traversal denied' });
        return;
      }

      const content = await fs.readFile(fullPath, 'utf-8');
      res.json({ content });
    } catch (err: any) {
      if (err.code === 'ENOENT') {
        res.status(404).json({ error: 'File not found' });
      } else {
        res.status(500).json({ error: err.message || 'Failed to read file' });
      }
    }
  });

  // List all processes
  app.get('/api/processes', async (req, res) => {
    try {
      const result = await backend.queryProcesses(requestedRepo(req));
      res.json(result);
    } catch (err: any) {
      res.status(statusFromError(err)).json({ error: err.message || 'Failed to query processes' });
    }
  });

  // Process detail
  app.get('/api/process', async (req, res) => {
    try {
      const name = String(req.query.name ?? '').trim();
      if (!name) {
        res.status(400).json({ error: 'Missing "name" query parameter' });
        return;
      }

      const result = await backend.queryProcessDetail(name, requestedRepo(req));
      if (result?.error) {
        res.status(404).json({ error: result.error });
        return;
      }
      res.json(result);
    } catch (err: any) {
      res.status(statusFromError(err)).json({ error: err.message || 'Failed to query process detail' });
    }
  });

  // List all clusters
  app.get('/api/clusters', async (req, res) => {
    try {
      const result = await backend.queryClusters(requestedRepo(req));
      res.json(result);
    } catch (err: any) {
      res.status(statusFromError(err)).json({ error: err.message || 'Failed to query clusters' });
    }
  });

  // Cluster detail
  app.get('/api/cluster', async (req, res) => {
    try {
      const name = String(req.query.name ?? '').trim();
      if (!name) {
        res.status(400).json({ error: 'Missing "name" query parameter' });
        return;
      }

      const result = await backend.queryClusterDetail(name, requestedRepo(req));
      if (result?.error) {
        res.status(404).json({ error: result.error });
        return;
      }
      res.json(result);
    } catch (err: any) {
      res.status(statusFromError(err)).json({ error: err.message || 'Failed to query cluster detail' });
    }
  });

  // ─── Memory / Observation Endpoints ──────────────────────────────────

  /** Ensure the memory DB is initialized for a given project key and path */
  const ensureMemoryDb = async (key: string, memoryPath: string): Promise<void> => {
    if (!isMemoryDbReady(key)) {
      await initMemoryDb(key, memoryPath);
    }
  };

  // List observations
  app.get('/api/memory', async (req, res) => {
    try {
      const entry = await resolveRepo(requestedRepo(req));
      if (!entry) {
        res.status(404).json({ error: 'Repository not found' });
        return;
      }
      const memoryPath = path.join(entry.storagePath, 'memory');
      await ensureMemoryDb(entry.name, memoryPath);

      const type = req.query.type as ObservationType | undefined;
      const project = req.query.project as string | undefined;
      const days = req.query.days ? Number(req.query.days) : undefined;
      const limit = req.query.limit ? Number(req.query.limit) : 50;
      const includeArchived = req.query.includeArchived === 'true';

      const results = await searchObservations(entry.name, {
        type,
        project,
        days,
        limit,
        includeArchived,
      });
      res.json({ results });
    } catch (err: any) {
      res.status(500).json({ error: err.message || 'Failed to list observations' });
    }
  });

  // Get observation counts per type
  app.get('/api/memory/stats', async (req, res) => {
    try {
      const entry = await resolveRepo(requestedRepo(req));
      if (!entry) {
        res.status(404).json({ error: 'Repository not found' });
        return;
      }
      const memoryPath = path.join(entry.storagePath, 'memory');
      await ensureMemoryDb(entry.name, memoryPath);

      const total = await countObservations(entry.name);
      const byType: Record<string, number> = {};
      for (const t of OBSERVATION_TYPES) {
        byType[t] = await countObservations(entry.name, { type: t });
      }
      res.json({ total, byType });
    } catch (err: any) {
      res.status(500).json({ error: err.message || 'Failed to get memory stats' });
    }
  });

  // Access global memory
  app.get('/api/memory/global', async (req, res) => {
    try {
      const globalMemoryPath = path.join(os.homedir(), '.codeindex', '_global', 'memory');
      await ensureMemoryDb(GLOBAL_PROJECT_NAME, globalMemoryPath);

      const type = req.query.type as ObservationType | undefined;
      const days = req.query.days ? Number(req.query.days) : undefined;
      const limit = req.query.limit ? Number(req.query.limit) : 50;
      const includeArchived = req.query.includeArchived === 'true';

      const results = await searchObservations(GLOBAL_PROJECT_NAME, {
        type,
        project: GLOBAL_PROJECT_NAME,
        days,
        limit,
        includeArchived,
      });
      res.json({ results });
    } catch (err: any) {
      res.status(500).json({ error: err.message || 'Failed to list global observations' });
    }
  });

  // Search observations (hybrid text + semantic)
  app.post('/api/memory/search', async (req, res) => {
    try {
      const entry = await resolveRepo(requestedRepo(req));
      if (!entry) {
        res.status(404).json({ error: 'Repository not found' });
        return;
      }
      const memoryPath = path.join(entry.storagePath, 'memory');
      await ensureMemoryDb(entry.name, memoryPath);

      const { query, type, days, limit } = req.body;
      if (!query || typeof query !== 'string') {
        res.status(400).json({ error: 'Missing "query" in request body' });
        return;
      }

      const results = await hybridSearchObservations(entry.name, {
        query,
        type,
        days,
        limit,
      });
      res.json({ results });
    } catch (err: any) {
      res.status(500).json({ error: err.message || 'Failed to search observations' });
    }
  });

  // Get single observation by UID
  app.get('/api/memory/:uid', async (req, res) => {
    try {
      const entry = await resolveRepo(requestedRepo(req));
      if (!entry) {
        res.status(404).json({ error: 'Repository not found' });
        return;
      }
      const memoryPath = path.join(entry.storagePath, 'memory');
      await ensureMemoryDb(entry.name, memoryPath);

      const result = await getObservation(entry.name, req.params.uid);
      if (!result) {
        res.status(404).json({ error: 'Observation not found' });
        return;
      }
      res.json(result);
    } catch (err: any) {
      res.status(500).json({ error: err.message || 'Failed to get observation' });
    }
  });

  // Delete observation by UID
  app.delete('/api/memory/:uid', async (req, res) => {
    try {
      const entry = await resolveRepo(requestedRepo(req));
      if (!entry) {
        res.status(404).json({ error: 'Repository not found' });
        return;
      }
      const memoryPath = path.join(entry.storagePath, 'memory');
      await ensureMemoryDb(entry.name, memoryPath);

      const deleted = await deleteObservation(entry.name, req.params.uid);
      if (!deleted) {
        res.status(404).json({ error: 'Observation not found' });
        return;
      }
      res.json({ success: true });
    } catch (err: any) {
      res.status(500).json({ error: err.message || 'Failed to delete observation' });
    }
  });

  // ─── Static File Serving (Desktop Mode) ─────────────────────────────
  // Serve the built React web app for the desktop app and `codeindex serve` usage.
  // The web app is built by codeindex-web and its dist/ folder is served here.
  const __dirname = path.dirname(fileURLToPath(import.meta.url));
  const webDistCandidates = [
    path.join(__dirname, '../../codeindex-web/dist'),      // Dev: monorepo layout
    path.join(__dirname, '../../../codeindex-web/dist'),    // Alternative layout
  ];
  const webDistPath = webDistCandidates.find(p => fsSync.existsSync(p));

  if (webDistPath) {
    app.use(express.static(webDistPath, {
      setHeaders: (res) => {
        // Required for KuzuDB WASM SharedArrayBuffer support
        res.setHeader('Cross-Origin-Opener-Policy', 'same-origin');
        res.setHeader('Cross-Origin-Embedder-Policy', 'require-corp');
      },
    }));

    // SPA catch-all: serve index.html for client-side routes
    // Must come after all /api/* routes
    app.get('*', (req, res, next) => {
      // Don't intercept API routes or MCP endpoints
      if (req.path.startsWith('/api/') || req.path.startsWith('/mcp')) {
        return next();
      }
      const indexPath = path.join(webDistPath, 'index.html');
      if (fsSync.existsSync(indexPath)) {
        res.setHeader('Cross-Origin-Opener-Policy', 'same-origin');
        res.setHeader('Cross-Origin-Embedder-Policy', 'require-corp');
        res.sendFile(indexPath);
      } else {
        next();
      }
    });
  }

  // Global error handler — catch anything the route handlers miss
  app.use((err: any, _req: express.Request, res: express.Response, _next: express.NextFunction) => {
    console.error('Unhandled error:', err);
    res.status(500).json({ error: 'Internal server error' });
  });

  const server = app.listen(port, host, () => {
    console.log(`CodeIndex server running on http://${host}:${port}`);

    // Run memory migration in background (non-blocking)
    migrateAllMemories((msg) => console.log(`[migration] ${msg}`)).catch(() => {});
  });

  // Graceful shutdown — close Express + KuzuDB cleanly
  const shutdown = async () => {
    server.close();
    await cleanupMcp();
    await closeKuzu();
    await backend.disconnect();
    process.exit(0);
  };
  process.once('SIGINT', shutdown);
  process.once('SIGTERM', shutdown);
};
