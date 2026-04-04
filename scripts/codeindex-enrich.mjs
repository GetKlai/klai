#!/usr/bin/env node
/**
 * CodeIndex Enrichment Layer for Klai — v2
 *
 * Adds contextual intelligence to CodeIndex's structural graph:
 *   1. Git hotspots — change_frequency, last_author, last_reason per symbol
 *   2. SPEC traceability — links SPEC documents to implementing symbols
 *   3. Test-to-code mapping — which tests cover which functions
 *   4. PageRank — importance scoring on the call graph
 *
 * Architecture:
 *   - Reads from KuzuDB (CodeIndex's graph) — read-only
 *   - Writes enrichments back into KuzuDB description fields
 *   - Survives CodeIndex updates (re-run after `codeindex analyze`)
 *   - Also writes a JSON sidecar for tools that can't query KuzuDB
 *
 * Usage:
 *   node scripts/codeindex-enrich.mjs              # current directory
 *   node scripts/codeindex-enrich.mjs --repo-path /path/to/repo
 */

import { execSync } from 'child_process';
import { readFileSync, writeFileSync, readdirSync, existsSync } from 'fs';
import { join, resolve, basename } from 'path';
import { createRequire } from 'module';
import { homedir } from 'os';

// ── KuzuDB access via codeindex's installation ─────────────────────────
const _npmGlobalRoot = execSync('npm root -g').toString().trim();
const codeindexRequire = createRequire(`${_npmGlobalRoot}/codeindex/node_modules/kuzu/`);
const kuzu = codeindexRequire('./index.js');

// ── Config ──────────────────────────────────────────────────────────────
const REPO_PATH = process.argv.includes('--repo-path')
  ? resolve(process.argv[process.argv.indexOf('--repo-path') + 1])
  : process.cwd();

const REGISTRY_PATH = join(homedir(), '.codeindex', 'registry.json');
const GIT_LOG_DAYS = 90;
const SPEC_DIRS = ['.moai/specs', '.workflow/specs'];
const TEST_PATTERNS = ['test_', '_test.', '.test.', '.spec.', '/tests/', '__tests__'];
const NOISE_NAMES = new Set([
  'get', 'set', 'run', 'init', 'main', 'start', 'stop', 'create', 'delete',
  'update', 'list', 'find', 'check', 'log', 'error', 'index', 'default',
  'render', 'handle', 'process', 'load', 'save', 'read', 'write', 'close',
  'open', 'test', 'setup', 'teardown', 'before', 'after', 'describe', 'it',
]);

// ── Helpers ─────────────────────────────────────────────────────────────
function findProject() {
  const registry = JSON.parse(readFileSync(REGISTRY_PATH, 'utf-8'));
  const entry = registry.find(e => resolve(e.path) === resolve(REPO_PATH));
  if (!entry) throw new Error(`No CodeIndex project found for ${REPO_PATH}`);
  return entry;
}

function git(cmd) {
  return execSync(`git -C "${REPO_PATH}" ${cmd}`, {
    encoding: 'utf-8',
    maxBuffer: 10 * 1024 * 1024,
  }).trim();
}

async function query(conn, cypher) {
  const result = await conn.query(cypher);
  return await result.getAll();
}

function esc(s) {
  if (!s) return '';
  return s.replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/\n/g, '\\n');
}

function isTestFile(filePath) {
  return TEST_PATTERNS.some(p => filePath.includes(p));
}

function parseSpecFrontmatter(content) {
  const match = content.match(/^---\n([\s\S]*?)\n---/);
  if (!match) return {};
  const fm = {};
  for (const line of match[1].split('\n')) {
    const kv = line.match(/^(\w+):\s*(.+)/);
    if (kv) fm[kv[1]] = kv[2].trim();
  }
  return fm;
}

// ── Phase 1: Git Hotspots ───────────────────────────────────────────────
async function enrichGitHotspots(conn) {
  console.log('\n📊 Phase 1: Git hotspots...');

  const functions = await query(conn,
    `MATCH (n:Function) RETURN n.id AS id, n.filePath AS filePath`
  );
  const methods = await query(conn,
    `MATCH (n:Method) RETURN n.id AS id, n.filePath AS filePath`
  );
  const allSymbols = [...functions, ...methods];
  const fileSet = new Set(allSymbols.map(f => f.filePath));
  console.log(`   ${allSymbols.length} symbols across ${fileSet.size} files`);

  const since = new Date(Date.now() - GIT_LOG_DAYS * 86400000).toISOString().split('T')[0];
  let gitLog;
  try {
    gitLog = git(`log --since="${since}" --name-only --pretty=format:"COMMIT|%an|%as|%s" -- .`);
  } catch {
    console.log('   ⚠ git log failed, skipping');
    return { enriched: 0 };
  }

  // Parse into per-file stats
  const fileStats = new Map();
  let currentCommit = null;
  for (const line of gitLog.split('\n')) {
    if (line.startsWith('COMMIT|')) {
      const [author, date, ...rest] = line.substring(7).split('|');
      currentCommit = { author, date, subject: rest.join('|') };
    } else if (line.trim() && currentCommit) {
      const fp = line.trim();
      if (!fileStats.has(fp)) {
        fileStats.set(fp, { changeCount: 0, lastAuthor: currentCommit.author, lastDate: currentCommit.date, lastReason: currentCommit.subject });
      }
      fileStats.get(fp).changeCount++;
    }
  }

  // Batch write: collect all updates, then execute
  const updates = [];
  for (const sym of allSymbols) {
    const stats = fileStats.get(sym.filePath);
    if (!stats) continue;
    updates.push({ id: sym.id, data: {
      _enrichment: 'git_hotspot',
      change_frequency: stats.changeCount,
      last_author: stats.lastAuthor,
      last_date: stats.lastDate,
      last_reason: stats.lastReason,
      period_days: GIT_LOG_DAYS,
    }});
  }

  let enriched = 0;
  for (const u of updates) {
    try {
      await query(conn,
        `MATCH (n) WHERE n.id = '${esc(u.id)}' SET n.description = '${esc(JSON.stringify(u.data))}'`
      );
      enriched++;
    } catch { /* skip */ }
  }

  console.log(`   ✓ ${enriched} symbols enriched (${GIT_LOG_DAYS}d window)`);
  return { enriched };
}

// ── Phase 2: SPEC Traceability ──────────────────────────────────────────
async function enrichSpecLinks(conn) {
  console.log('\n📋 Phase 2: SPEC traceability...');

  // Find spec files
  const specFiles = [];
  for (const dir of SPEC_DIRS) {
    const absDir = join(REPO_PATH, dir);
    if (!existsSync(absDir)) continue;
    (function walk(d) {
      for (const entry of readdirSync(d, { withFileTypes: true })) {
        if (entry.isDirectory()) walk(join(d, entry.name));
        else if (entry.name.endsWith('.md')) specFiles.push(join(d, entry.name));
      }
    })(absDir);
  }

  if (specFiles.length === 0) {
    console.log('   ⚠ No SPEC files found');
    return { linked: 0 };
  }
  console.log(`   ${specFiles.length} SPEC files found`);

  // Build symbol lookup (only non-noise names)
  const symbols = await query(conn,
    `MATCH (n:Function) RETURN n.id AS id, n.name AS name, n.filePath AS filePath
     UNION ALL
     MATCH (n:Class) RETURN n.id AS id, n.name AS name, n.filePath AS filePath
     UNION ALL
     MATCH (n:Method) RETURN n.id AS id, n.name AS name, n.filePath AS filePath`
  );

  const byName = new Map();
  const byFile = new Map();
  for (const s of symbols) {
    if (!NOISE_NAMES.has(s.name)) {
      if (!byName.has(s.name)) byName.set(s.name, []);
      byName.get(s.name).push(s);
    }
    if (!byFile.has(s.filePath)) byFile.set(s.filePath, []);
    byFile.get(s.filePath).push(s);
  }

  let totalLinks = 0;
  const specToSymbols = new Map(); // for JSON sidecar

  for (const specPath of specFiles) {
    const content = readFileSync(specPath, 'utf-8');
    const specName = basename(specPath, '.md');
    const fm = parseSpecFrontmatter(content);
    const linked = new Set();

    // Strategy 1: Frontmatter files/implements fields
    if (fm.files) {
      for (const ref of fm.files.split(',').map(s => s.trim())) {
        const matches = byFile.get(ref) || [];
        for (const m of matches) linked.add(m.id);
      }
    }

    // Strategy 2: Explicit file path references in content
    const fileRefs = content.match(/[\w\-./]+\.(py|ts|tsx|js)\b/g) || [];
    for (const ref of fileRefs) {
      for (const [fp, syms] of byFile) {
        if (fp.endsWith(ref) || fp.includes(ref)) {
          for (const s of syms) linked.add(s.id);
        }
      }
    }

    // Strategy 3: Backtick code references (non-noise, low ambiguity)
    const codeRefs = content.match(/`([a-zA-Z_][a-zA-Z0-9_]+)`/g) || [];
    for (const ref of codeRefs) {
      const name = ref.replace(/`/g, '');
      if (NOISE_NAMES.has(name) || name.length < 4) continue;
      const matches = byName.get(name) || [];
      if (matches.length > 0 && matches.length <= 3) {
        for (const m of matches) linked.add(m.id);
      }
    }

    // Strategy 4: Unique snake_case identifiers (3+ segments)
    const snakeRefs = content.match(/\b[a-z][a-z0-9]*(?:_[a-z0-9]+){2,}\b/g) || [];
    for (const ref of snakeRefs) {
      const matches = byName.get(ref) || [];
      if (matches.length === 1) linked.add(matches[0].id);
    }

    if (linked.size === 0) continue;
    specToSymbols.set(specName, [...linked]);

    // Write to graph
    for (const symId of linked) {
      try {
        const existing = await query(conn,
          `MATCH (n) WHERE n.id = '${esc(symId)}' RETURN n.description AS d`
        );
        if (!existing.length) continue;

        let desc = {};
        try { desc = JSON.parse(existing[0].d || '{}'); } catch { desc = {}; }
        if (!desc._specs) desc._specs = [];
        if (!desc._specs.includes(specName)) {
          desc._specs.push(specName);
          await query(conn,
            `MATCH (n) WHERE n.id = '${esc(symId)}' SET n.description = '${esc(JSON.stringify(desc))}'`
          );
          totalLinks++;
        }
      } catch { /* skip */ }
    }

    console.log(`   ${specName}: ${linked.size} symbols`);
  }

  console.log(`   ✓ ${totalLinks} SPEC→symbol links`);
  return { linked: totalLinks, specs: specToSymbols.size };
}

// ── Phase 3: Test-to-Code Mapping ───────────────────────────────────────
async function enrichTestMapping(conn) {
  console.log('\n🧪 Phase 3: Test mapping...');

  const allFiles = await query(conn,
    `MATCH (f:File) RETURN f.filePath AS path`
  );
  const testPaths = allFiles.map(r => r.path).filter(isTestFile);
  console.log(`   ${testPaths.length} test files`);

  let total = 0;
  for (const testPath of testPaths) {
    const imports = await query(conn,
      `MATCH (f:File {filePath: '${esc(testPath)}'})-[r:CodeRelation]->(target)
       WHERE r.type = 'IMPORTS'
       RETURN target.filePath AS targetPath`
    );

    const codePaths = [...new Set(
      imports.map(i => i.targetPath).filter(p => p && !isTestFile(p))
    )];

    for (const codePath of codePaths) {
      const fns = await query(conn,
        `MATCH (n:Function {filePath: '${esc(codePath)}'})
         RETURN n.id AS id, n.description AS d`
      );

      for (const fn of fns) {
        let desc = {};
        try { desc = JSON.parse(fn.d || '{}'); } catch { desc = {}; }
        if (!desc._tested_by) desc._tested_by = [];
        const testRef = basename(testPath);
        if (!desc._tested_by.includes(testRef)) {
          desc._tested_by.push(testRef);
          try {
            await query(conn,
              `MATCH (n:Function) WHERE n.id = '${esc(fn.id)}'
               SET n.description = '${esc(JSON.stringify(desc))}'`
            );
            total++;
          } catch { /* skip */ }
        }
      }
    }
  }

  console.log(`   ✓ ${total} test→function mappings`);
  return { mappings: total };
}

// ── Phase 4: PageRank ───────────────────────────────────────────────────
async function enrichPageRank(conn) {
  console.log('\n📈 Phase 4: PageRank...');

  const edges = await query(conn,
    `MATCH (a)-[r:CodeRelation]->(b) WHERE r.type = 'CALLS' RETURN a.id AS src, b.id AS dst`
  );
  if (!edges.length) {
    console.log('   ⚠ No CALLS edges');
    return { scored: 0 };
  }
  console.log(`   ${edges.length} CALLS edges`);

  // Build adjacency: outLinks and inLinks for O(E) iteration
  const outLinks = new Map();
  const inLinks = new Map();
  const nodeSet = new Set();

  for (const { src, dst } of edges) {
    nodeSet.add(src);
    nodeSet.add(dst);
    if (!outLinks.has(src)) outLinks.set(src, []);
    outLinks.get(src).push(dst);
    if (!inLinks.has(dst)) inLinks.set(dst, []);
    inLinks.get(dst).push(src);
  }

  const nodes = [...nodeSet];
  const N = nodes.length;
  const d = 0.85;

  let scores = new Map(nodes.map(n => [n, 1.0 / N]));

  for (let iter = 0; iter < 30; iter++) {
    const sinkScore = nodes
      .filter(n => !outLinks.has(n) || !outLinks.get(n).length)
      .reduce((s, n) => s + scores.get(n), 0);

    const next = new Map();
    for (const node of nodes) {
      let score = (1 - d) / N + d * sinkScore / N;
      const incoming = inLinks.get(node) || [];
      for (const src of incoming) {
        score += d * scores.get(src) / outLinks.get(src).length;
      }
      next.set(node, score);
    }

    let diff = 0;
    for (const n of nodes) diff += Math.abs(next.get(n) - scores.get(n));
    scores = next;
    if (diff < 1e-6) {
      console.log(`   Converged at iteration ${iter + 1}`);
      break;
    }
  }

  // Normalize 0-1
  const max = Math.max(...scores.values());
  const min = Math.min(...scores.values());
  const range = max - min || 1;

  const sorted = [...scores.entries()].sort((a, b) => b[1] - a[1]);
  const topHalf = sorted.slice(0, Math.floor(sorted.length / 2));

  let updated = 0;
  for (const [nodeId, raw] of topHalf) {
    const norm = parseFloat(((raw - min) / range).toFixed(4));
    try {
      const existing = await query(conn,
        `MATCH (n) WHERE n.id = '${esc(nodeId)}' RETURN n.description AS d`
      );
      if (!existing.length) continue;

      let desc = {};
      try { desc = JSON.parse(existing[0].d || '{}'); } catch { desc = {}; }
      desc._pagerank = norm;

      await query(conn,
        `MATCH (n) WHERE n.id = '${esc(nodeId)}' SET n.description = '${esc(JSON.stringify(desc))}'`
      );
      updated++;
    } catch { /* skip */ }
  }

  console.log(`   ✓ ${updated} symbols scored`);
  console.log('   Top 10:');
  for (const [id, raw] of sorted.slice(0, 10)) {
    const norm = ((raw - min) / range).toFixed(3);
    console.log(`     [${norm}] ${id.split(':').pop()} (${id.split(':')[1]?.split('/').slice(-2).join('/') || ''})`);
  }

  return { scored: updated };
}

// ── Main ────────────────────────────────────────────────────────────────
async function main() {
  const t0 = Date.now();
  console.log('CodeIndex Enrichment v2');
  console.log('=======================');

  const project = findProject();
  console.log(`Project: ${project.name} | ${project.stats.nodes} symbols, ${project.stats.edges} edges`);

  const dbPath = join(homedir(), '.codeindex', project.name, 'kuzu');
  const db = new kuzu.Database(dbPath, 0, false, false, 256 * 1024 * 1024);
  const conn = new kuzu.Connection(db);

  const results = {};
  try {
    results.gitHotspots = await enrichGitHotspots(conn);
    results.specLinks = await enrichSpecLinks(conn);
    results.testMapping = await enrichTestMapping(conn);
    results.pageRank = await enrichPageRank(conn);
  } finally {
    try { db.close(); } catch { /* kuzu cleanup crash — harmless */ }
  }

  // Write JSON sidecar for non-KuzuDB consumers
  const sidecarPath = join(homedir(), '.codeindex', project.name, 'enrichment.json');
  writeFileSync(sidecarPath, JSON.stringify({
    version: 2,
    project: project.name,
    enrichedAt: new Date().toISOString(),
    repoPath: REPO_PATH,
    results,
  }, null, 2));

  const elapsed = ((Date.now() - t0) / 1000).toFixed(1);
  console.log(`\n=======================`);
  console.log(`Done in ${elapsed}s`);
  console.log(`Sidecar: ${sidecarPath}`);
  console.log(JSON.stringify(results, null, 2));
}

main().catch(e => {
  console.error('Error:', e.message);
  process.exit(1);
});
