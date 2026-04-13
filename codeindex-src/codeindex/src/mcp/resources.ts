/**
 * MCP Resources (Multi-Repo)
 * 
 * Provides structured on-demand data to AI agents.
 * All resources use repo-scoped URIs: codeindex://repo/{name}/context
 */

import type { LocalBackend } from './local/local-backend.js';
import { checkStaleness } from './staleness.js';
import { ensureGlobalMemory, ensureProjectMemory } from '../core/memory/global-store.js';
import { listRecentObservations, countObservations } from '../core/memory/observation-store.js';
import { GLOBAL_PROJECT_NAME } from '../core/memory/types.js';

export interface ResourceDefinition {
  uri: string;
  name: string;
  description: string;
  mimeType: string;
}

export interface ResourceTemplate {
  uriTemplate: string;
  name: string;
  description: string;
  mimeType: string;
}

/**
 * Static resources — includes per-repo resources and the global repos list
 */
export function getResourceDefinitions(): ResourceDefinition[] {
  return [
    {
      uri: 'codeindex://repos',
      name: 'All Indexed Repositories',
      description: 'List of all indexed repos with stats. Read this first to discover available repos.',
      mimeType: 'text/yaml',
    },
    {
      uri: 'codeindex://setup',
      name: 'CodeIndex Setup Content',
      description: 'Returns AGENTS.md content for all indexed repos. Useful for setup/onboarding.',
      mimeType: 'text/markdown',
    },
  ];
}

/**
 * Dynamic resource templates
 */
export function getResourceTemplates(): ResourceTemplate[] {
  return [
    {
      uriTemplate: 'codeindex://repo/{name}/context',
      name: 'Repo Overview',
      description: 'Codebase stats, staleness check, and available tools',
      mimeType: 'text/yaml',
    },
    {
      uriTemplate: 'codeindex://repo/{name}/clusters',
      name: 'Repo Modules',
      description: 'All functional areas (Leiden clusters)',
      mimeType: 'text/yaml',
    },
    {
      uriTemplate: 'codeindex://repo/{name}/processes',
      name: 'Repo Processes',
      description: 'All execution flows',
      mimeType: 'text/yaml',
    },
    {
      uriTemplate: 'codeindex://repo/{name}/schema',
      name: 'Graph Schema',
      description: 'Node/edge schema for Cypher queries',
      mimeType: 'text/yaml',
    },
    {
      uriTemplate: 'codeindex://repo/{name}/cluster/{clusterName}',
      name: 'Module Detail',
      description: 'Deep dive into a specific functional area',
      mimeType: 'text/yaml',
    },
    {
      uriTemplate: 'codeindex://repo/{name}/process/{processName}',
      name: 'Process Trace',
      description: 'Step-by-step execution trace',
      mimeType: 'text/yaml',
    },
    {
      uriTemplate: 'codeindex://repo/{name}/memory',
      name: 'Project Memory',
      description: 'Recent observations (decisions, bugs, patterns) for this project',
      mimeType: 'text/yaml',
    },
    {
      uriTemplate: 'codeindex://memory/global',
      name: 'Global Memory',
      description: 'Cross-project observations (preferences, learnings, dos/donts)',
      mimeType: 'text/yaml',
    },
  ];
}

/**
 * Parse a resource URI to extract the repo name and resource type.
 */
function parseUri(uri: string): { repoName?: string; resourceType: string; param?: string } {
  if (uri === 'codeindex://repos') return { resourceType: 'repos' };
  if (uri === 'codeindex://setup') return { resourceType: 'setup' };
  if (uri === 'codeindex://memory/global') return { resourceType: 'memory_global' };

  // Repo-scoped: codeindex://repo/{name}/context
  const repoMatch = uri.match(/^codeindex:\/\/repo\/([^/]+)\/(.+)$/);
  if (repoMatch) {
    const repoName = decodeURIComponent(repoMatch[1]);
    const rest = repoMatch[2];

    if (rest.startsWith('cluster/')) {
      return { repoName, resourceType: 'cluster', param: decodeURIComponent(rest.replace('cluster/', '')) };
    }
    if (rest.startsWith('process/')) {
      return { repoName, resourceType: 'process', param: decodeURIComponent(rest.replace('process/', '')) };
    }

    return { repoName, resourceType: rest };
  }

  throw new Error(`Unknown resource URI: ${uri}`);
}

/**
 * Read a resource and return its content
 */
export async function readResource(uri: string, backend: LocalBackend): Promise<string> {
  const parsed = parseUri(uri);

  // Global repos list — no repo context needed
  if (parsed.resourceType === 'repos') {
    return getReposResource(backend);
  }
  
  // Setup resource — returns AGENTS.md content for all repos
  if (parsed.resourceType === 'setup') {
    return getSetupResource(backend);
  }

  // Global memory resource
  if (parsed.resourceType === 'memory_global') {
    return getGlobalMemoryResource();
  }

  const repoName = parsed.repoName;

  switch (parsed.resourceType) {
    case 'context':
      return getContextResource(backend, repoName);
    case 'clusters':
      return getClustersResource(backend, repoName);
    case 'processes':
      return getProcessesResource(backend, repoName);
    case 'schema':
      return getSchemaResource();
    case 'cluster':
      return getClusterDetailResource(parsed.param!, backend, repoName);
    case 'process':
      return getProcessDetailResource(parsed.param!, backend, repoName);
    case 'memory':
      return getProjectMemoryResource(repoName!);
    default:
      throw new Error(`Unknown resource: ${uri}`);
  }
}

// ─── Resource Implementations ─────────────────────────────────────────

/**
 * Repos resource — list all indexed repositories
 */
async function getReposResource(backend: LocalBackend): Promise<string> {
  const repos = await backend.listRepos();

  if (repos.length === 0) {
    return 'repos: []\n# No repositories indexed. Run: codeindex analyze';
  }

  const lines: string[] = ['repos:'];
  for (const repo of repos) {
    lines.push(`  - name: "${repo.name}"`);
    lines.push(`    path: "${repo.path}"`);
    lines.push(`    indexed: "${repo.indexedAt}"`);
    lines.push(`    commit: "${repo.lastCommit?.slice(0, 7) || 'unknown'}"`);
    if (repo.stats) {
      lines.push(`    files: ${repo.stats.files || 0}`);
      lines.push(`    symbols: ${repo.stats.nodes || 0}`);
      lines.push(`    processes: ${repo.stats.processes || 0}`);
    }
  }

  if (repos.length > 1) {
    lines.push('');
    lines.push('# Multiple repos indexed. Use repo parameter in tool calls:');
    lines.push(`# codeindex_search({query: "auth", repo: "${repos[0].name}"})`);
  }

  return lines.join('\n');
}

/**
 * Context resource — codebase overview for a specific repo
 */
async function getContextResource(backend: LocalBackend, repoName?: string): Promise<string> {
  // Resolve repo
  const repo = await backend.resolveRepo(repoName);
  const repoId = repo.name.toLowerCase();
  const context = backend.getContext(repoId) || backend.getContext();

  if (!context) {
    return 'error: No codebase loaded. Run: codeindex analyze';
  }
  
  // Check staleness
  const repoPath = repo.repoPath;
  const lastCommit = repo.lastCommit || 'HEAD';
  const indexedAt = repo.indexedAt;
  const staleness = repoPath ? checkStaleness(repoPath, lastCommit, indexedAt) : { isStale: false, commitsBehind: 0, daysOld: 0 };
  
  const lines: string[] = [
    `project: ${context.projectName}`,
  ];
  
  if (staleness.isStale && staleness.hint) {
    lines.push('');
    lines.push(`staleness: "${staleness.hint}"`);
  }
  
  lines.push('');
  lines.push('stats:');
  lines.push(`  files: ${context.stats.fileCount}`);
  lines.push(`  symbols: ${context.stats.functionCount}`);
  lines.push(`  processes: ${context.stats.processCount}`);
  lines.push('');
  lines.push('tools_available:');
  lines.push('  - query: Process-grouped code intelligence (execution flows related to a concept)');
  lines.push('  - context: 360-degree symbol view (categorized refs, process participation)');
  lines.push('  - impact: Blast radius analysis (what breaks if you change a symbol)');
  lines.push('  - detect_changes: Git-diff impact analysis (what do your changes affect)');
  lines.push('  - rename: Multi-file coordinated rename with confidence tags');
  lines.push('  - cypher: Raw graph queries');
  lines.push('  - list_repos: Discover all indexed repositories');
  lines.push('');
  lines.push('re_index: Run `npx codeindex analyze` in terminal if data is stale');
  lines.push('');
  lines.push('resources_available:');
  lines.push('  - codeindex://repos: All indexed repositories');
  lines.push(`  - codeindex://repo/${context.projectName}/clusters: All functional areas`);
  lines.push(`  - codeindex://repo/${context.projectName}/processes: All execution flows`);
  lines.push(`  - codeindex://repo/${context.projectName}/cluster/{name}: Module details`);
  lines.push(`  - codeindex://repo/${context.projectName}/process/{name}: Process trace`);
  
  return lines.join('\n');
}

/**
 * Clusters resource — queries graph directly via backend.queryClusters()
 */
async function getClustersResource(backend: LocalBackend, repoName?: string): Promise<string> {
  try {
    const result = await backend.queryClusters(repoName, 100);

    if (!result.clusters || result.clusters.length === 0) {
      return 'modules: []\n# No functional areas detected. Run: codeindex analyze';
    }

    const displayLimit = 20;
    const lines: string[] = ['modules:'];
    const toShow = result.clusters.slice(0, displayLimit);

    for (const cluster of toShow) {
      const label = cluster.heuristicLabel || cluster.label || cluster.id;
      lines.push(`  - name: "${label}"`);
      lines.push(`    symbols: ${cluster.symbolCount || 0}`);
      if (cluster.cohesion) {
        lines.push(`    cohesion: ${(cluster.cohesion * 100).toFixed(0)}%`);
      }
    }

    if (result.clusters.length > displayLimit) {
      lines.push(`\n# Showing top ${displayLimit} of ${result.clusters.length} modules. Use codeindex_query for deeper search.`);
    }

    return lines.join('\n');
  } catch (err: any) {
    return `error: ${err.message}`;
  }
}

/**
 * Processes resource — queries graph directly via backend.queryProcesses()
 */
async function getProcessesResource(backend: LocalBackend, repoName?: string): Promise<string> {
  try {
    const result = await backend.queryProcesses(repoName, 50);

    if (!result.processes || result.processes.length === 0) {
      return 'processes: []\n# No processes detected. Run: codeindex analyze';
    }

    const displayLimit = 20;
    const lines: string[] = ['processes:'];
    const toShow = result.processes.slice(0, displayLimit);

    for (const proc of toShow) {
      const label = proc.heuristicLabel || proc.label || proc.id;
      lines.push(`  - name: "${label}"`);
      lines.push(`    type: ${proc.processType || 'unknown'}`);
      lines.push(`    steps: ${proc.stepCount || 0}`);
    }

    if (result.processes.length > displayLimit) {
      lines.push(`\n# Showing top ${displayLimit} of ${result.processes.length} processes. Use codeindex_query for deeper search.`);
    }

    return lines.join('\n');
  } catch (err: any) {
    return `error: ${err.message}`;
  }
}

/**
 * Schema resource — graph structure for Cypher queries
 */
function getSchemaResource(): string {
  return `# CodeIndex Graph Schema

nodes:
  - File: Source code files
  - Folder: Directory containers
  - Function: Functions and arrow functions
  - Class: Class definitions
  - Interface: Interface/type definitions
  - Method: Class methods
  - CodeElement: Catch-all for other code elements
  - Community: Auto-detected functional area (Leiden algorithm)
  - Process: Execution flow trace

additional_node_types: "Multi-language: Struct, Enum, Macro, Typedef, Union, Namespace, Trait, Impl, TypeAlias, Const, Static, Property, Record, Delegate, Annotation, Constructor, Template, Module (use backticks in queries: \`Struct\`, \`Enum\`, etc.)"

relationships:
  - CONTAINS: File/Folder contains child
  - DEFINES: File defines a symbol
  - CALLS: Function/method invocation
  - IMPORTS: Module imports
  - EXTENDS: Class inheritance
  - IMPLEMENTS: Interface implementation
  - MEMBER_OF: Symbol belongs to community
  - STEP_IN_PROCESS: Symbol is step N in process

relationship_table: "All relationships use a single CodeRelation table with a 'type' property. Properties: type (STRING), confidence (DOUBLE), reason (STRING), step (INT32)"

example_queries:
  find_callers: |
    MATCH (caller)-[:CodeRelation {type: 'CALLS'}]->(f:Function {name: "myFunc"})
    RETURN caller.name, caller.filePath
  
  find_community_members: |
    MATCH (s)-[:CodeRelation {type: 'MEMBER_OF'}]->(c:Community)
    WHERE c.heuristicLabel = "Auth"
    RETURN s.name, labels(s)[0] AS type
  
  trace_process: |
    MATCH (s)-[r:CodeRelation {type: 'STEP_IN_PROCESS'}]->(p:Process)
    WHERE p.heuristicLabel = "LoginFlow"
    RETURN s.name, r.step
    ORDER BY r.step
`;
}

/**
 * Cluster detail resource — queries graph directly via backend.queryClusterDetail()
 */
async function getClusterDetailResource(name: string, backend: LocalBackend, repoName?: string): Promise<string> {
  try {
    const result = await backend.queryClusterDetail(name, repoName);

    if (result.error) {
      return `error: ${result.error}`;
    }

    const cluster = result.cluster;
    const members = result.members || [];

    const lines: string[] = [
      `module: "${cluster.heuristicLabel || cluster.label || cluster.id}"`,
      `symbols: ${cluster.symbolCount || members.length}`,
    ];

    if (cluster.cohesion) {
      lines.push(`cohesion: ${(cluster.cohesion * 100).toFixed(0)}%`);
    }

    if (members.length > 0) {
      lines.push('');
      lines.push('members:');
      for (const member of members.slice(0, 20)) {
        lines.push(`  - name: ${member.name}`);
        lines.push(`    type: ${member.type}`);
        lines.push(`    file: ${member.filePath}`);
      }
      if (members.length > 20) {
        lines.push(`  # ... and ${members.length - 20} more`);
      }
    }

    return lines.join('\n');
  } catch (err: any) {
    return `error: ${err.message}`;
  }
}

/**
 * Process detail resource — queries graph directly via backend.queryProcessDetail()
 */
async function getProcessDetailResource(name: string, backend: LocalBackend, repoName?: string): Promise<string> {
  try {
    const result = await backend.queryProcessDetail(name, repoName);

    if (result.error) {
      return `error: ${result.error}`;
    }

    const proc = result.process;
    const steps = result.steps || [];

    const lines: string[] = [
      `name: "${proc.heuristicLabel || proc.label || proc.id}"`,
      `type: ${proc.processType || 'unknown'}`,
      `step_count: ${proc.stepCount || steps.length}`,
    ];

    if (steps.length > 0) {
      lines.push('');
      lines.push('trace:');
      for (const step of steps) {
        lines.push(`  ${step.step}: ${step.name} (${step.filePath})`);
      }
    }

    return lines.join('\n');
  } catch (err: any) {
    return `error: ${err.message}`;
  }
}

/**
 * Setup resource — generates AGENTS.md content for all indexed repos.
 * Useful for `codeindex setup` onboarding or dynamic content injection.
 */
async function getSetupResource(backend: LocalBackend): Promise<string> {
  const repos = await backend.listRepos();

  if (repos.length === 0) {
    return '# CodeIndex\n\nNo repositories indexed. Run: `npx codeindex analyze` in a repository.';
  }
  
  const sections: string[] = [];
  
  for (const repo of repos) {
    const stats = repo.stats || {};
    const lines = [
      `# CodeIndex MCP — ${repo.name}`,
      '',
      `This project is indexed by CodeIndex as **${repo.name}** (${stats.nodes || 0} symbols, ${stats.edges || 0} relationships, ${stats.processes || 0} execution flows).`,
      '',
      '## Tools',
      '',
      '| Tool | What it gives you |',
      '|------|-------------------|',
      '| `query` | Process-grouped code intelligence — execution flows related to a concept |',
      '| `context` | 360-degree symbol view — categorized refs, processes it participates in |',
      '| `impact` | Symbol blast radius — what breaks at depth 1/2/3 with confidence |',
      '| `detect_changes` | Git-diff impact — what do your current changes affect |',
      '| `rename` | Multi-file coordinated rename with confidence-tagged edits |',
      '| `cypher` | Raw graph queries |',
      '| `list_repos` | Discover indexed repos |',
      '',
      '## Resources',
      '',
      `- \`codeindex://repo/${repo.name}/context\` — Stats, staleness check`,
      `- \`codeindex://repo/${repo.name}/clusters\` — All functional areas`,
      `- \`codeindex://repo/${repo.name}/processes\` — All execution flows`,
      `- \`codeindex://repo/${repo.name}/schema\` — Graph schema for Cypher`,
    ];
    sections.push(lines.join('\n'));
  }
  
  return sections.join('\n\n---\n\n');
}

// ─── Memory Resource Implementations ──────────────────────────────────

/** Format age from ISO date to compact relative string */
function formatAge(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(ms / 60_000);
  if (mins < 60) return 'just now';
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  const weeks = Math.floor(days / 7);
  if (weeks < 5) return `${weeks}w ago`;
  return `${Math.floor(days / 30)}mo ago`;
}

/**
 * Global memory resource — cross-project observations (preferences, learnings, dos/donts)
 */
async function getGlobalMemoryResource(): Promise<string> {
  try {
    await ensureGlobalMemory();
  } catch {
    return 'memory: []\n# Global memory not initialized. Use remember() to save your first observation.';
  }

  const count = await countObservations(GLOBAL_PROJECT_NAME);
  const recent = await listRecentObservations(GLOBAL_PROJECT_NAME, { limit: 15 });

  if (recent.length === 0) {
    return 'memory:\n  total: 0\n# No global observations yet. Use remember({scope: "global"}) to save preferences, learnings, dos/donts.';
  }

  const lines: string[] = [
    'memory:',
    `  scope: global`,
    `  total: ${count}`,
    '',
    'recent:',
  ];

  for (const obs of recent) {
    lines.push(`  - name: "${obs.name}"`);
    lines.push(`    type: ${obs.type}`);
    lines.push(`    age: ${formatAge(obs.createdAt)}`);
    if (obs.tags.length > 0) {
      lines.push(`    tags: [${obs.tags.join(', ')}]`);
    }
  }

  lines.push('');
  lines.push('# Use recall({scope: "global"}) to search. Use remember({scope: "global"}) to add.');

  return lines.join('\n');
}

/**
 * Project memory resource — observations (decisions, bugs, patterns) for a specific repo
 */
async function getProjectMemoryResource(repoName: string): Promise<string> {
  if (!repoName) {
    return 'error: Repository name required. Use codeindex://repo/{name}/memory';
  }

  try {
    await ensureProjectMemory(repoName);
  } catch {
    return `memory: []\n# Memory for "${repoName}" not initialized. Use remember() to save your first observation.`;
  }

  const count = await countObservations(repoName, { project: repoName });
  const recent = await listRecentObservations(repoName, { limit: 15, project: repoName });

  if (recent.length === 0) {
    return `memory:\n  project: "${repoName}"\n  total: 0\n# No observations for this project. Use remember() to save decisions, bugs, patterns.`;
  }

  const lines: string[] = [
    'memory:',
    `  project: "${repoName}"`,
    `  total: ${count}`,
    '',
    'recent:',
  ];

  for (const obs of recent) {
    lines.push(`  - name: "${obs.name}"`);
    lines.push(`    type: ${obs.type}`);
    lines.push(`    age: ${formatAge(obs.createdAt)}`);
    if (obs.tags.length > 0) {
      lines.push(`    tags: [${obs.tags.join(', ')}]`);
    }
  }

  lines.push('');
  lines.push(`# Use recall({repo: "${repoName}"}) to search. Use remember() to add.`);

  return lines.join('\n');
}
