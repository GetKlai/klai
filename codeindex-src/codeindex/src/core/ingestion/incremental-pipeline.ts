/**
 * Incremental Update Pipeline
 *
 * Git-diff-based incremental updates for CodeIndex. Instead of rebuilding
 * the entire graph from scratch, this pipeline:
 *
 * 1. Detects changed files via `git diff --name-status`
 * 2. Rebuilds the SymbolTable and ImportMap from existing KuzuDB data
 * 3. Deletes old nodes+edges for changed/deleted files
 * 4. Parses only changed/added files
 * 5. Resolves imports/calls/heritage for changed files
 * 6. Inserts new nodes+edges individually
 * 7. Rebuilds FTS indexes and updates embeddings
 *
 * Target: < 5 seconds for 1-10 file changes on a 470K-node repo.
 */

import path from 'path';
import { createKnowledgeGraph } from '../graph/graph.js';
import { createSymbolTable } from './symbol-table.js';
import { createASTCache } from './ast-cache.js';
import { createImportMap, buildImportResolutionContext } from './import-processor.js';
import { processImportsFromExtracted } from './import-processor.js';
import { processCallsFromExtracted } from './call-processor.js';
import { processHeritageFromExtracted } from './heritage-processor.js';
import { processParsing } from './parsing-processor.js';
import { processStructure } from './structure-processor.js';
import { readFileContents } from './filesystem-walker.js';
import { getLanguageFromFilename } from './utils.js';
import { shouldIgnorePath } from '../../config/ignore-service.js';
import { generateId } from '../../lib/utils.js';
import {
  initKuzu,
  deleteNodesForFiles,
  deleteCommunitiesAndProcesses,
  buildGraphFromKuzu,
  queryAllSymbols,
  queryAllFilePaths,
  batchInsertNodesViaCSV,
  batchInsertRelationshipsViaCSV,
  createFTSIndex,
  executeQuery,
} from '../lbug/lbug-adapter.js';
import type { RelationshipInput } from '../lbug/lbug-adapter.js';
import type { ChangedFiles } from '../../storage/git.js';
import type { PipelineProgress } from '../../types/pipeline.js';

const isDev = process.env.NODE_ENV === 'development';

export interface IncrementalResult {
  added: number;
  modified: number;
  deleted: number;
  nodesInserted: number;
  nodesDeleted: number;
  edgesInserted: number;
  skippedFiles: number;
  communities: number;
  processes: number;
}

export const runIncrementalPipeline = async (
  repoPath: string,
  changedFiles: ChangedFiles,
  lbugPath: string,
  onProgress: (progress: PipelineProgress) => void,
): Promise<IncrementalResult> => {
  const result: IncrementalResult = {
    added: 0,
    modified: 0,
    deleted: 0,
    nodesInserted: 0,
    nodesDeleted: 0,
    edgesInserted: 0,
    skippedFiles: 0,
    communities: 0,
    processes: 0,
  };

  // ── Phase 1: Filter changed files through ignore rules ──────────────
  onProgress({
    phase: 'extracting',
    percent: 0,
    message: 'Filtering changed files...',
  });

  const filterFiles = (paths: string[]): string[] =>
    paths.filter(p => !shouldIgnorePath(p));

  const added = filterFiles(changedFiles.added);
  const modified = filterFiles(changedFiles.modified);
  const deleted = filterFiles(changedFiles.deleted);

  const totalSkipped =
    (changedFiles.added.length - added.length) +
    (changedFiles.modified.length - modified.length) +
    (changedFiles.deleted.length - deleted.length);
  result.skippedFiles = totalSkipped;

  const allChanged = [...added, ...modified];
  const allRemoved = [...modified, ...deleted]; // modified files need old data removed first

  result.added = added.length;
  result.modified = modified.length;
  result.deleted = deleted.length;

  if (allChanged.length === 0 && deleted.length === 0) {
    onProgress({ phase: 'complete', percent: 100, message: 'No relevant changes detected.' });
    return result;
  }

  // Too many changes → full rebuild with CSV COPY is 10x faster than individual queries.
  // Incremental does ~44K individual DB queries for 3000 files. CSV COPY does ~30 bulk loads.
  const MAX_INCREMENTAL_FILES = 500;
  const totalFilesChanged = allChanged.length + deleted.length;
  if (totalFilesChanged > MAX_INCREMENTAL_FILES) {
    throw new ThresholdExceededError(
      `${totalFilesChanged} files changed (>${MAX_INCREMENTAL_FILES}). Full rebuild with CSV COPY is faster.`,
      totalFilesChanged,
      MAX_INCREMENTAL_FILES,
    );
  }

  if (isDev) {
    console.log(`📝 Incremental: +${added.length} ~${modified.length} -${deleted.length} (${totalSkipped} ignored)`);
  }

  // ── Phase 2: Initialize DB and build context ────────────────────────
  onProgress({
    phase: 'extracting',
    percent: 5,
    message: 'Loading existing index...',
  });

  await initKuzu(lbugPath);

  // Get all indexed file paths for import resolution
  const indexedPaths = await queryAllFilePaths();

  // ── Phase 3: Rebuild SymbolTable from DB ────────────────────────────
  onProgress({
    phase: 'extracting',
    percent: 10,
    message: 'Rebuilding symbol table from index...',
  });

  const symbolTable = createSymbolTable();
  const importMap = createImportMap();

  // Load all existing symbols into the symbol table
  const existingSymbols = await queryAllSymbols();
  const removedFilesSet = new Set(allRemoved);

  for (const sym of existingSymbols) {
    // Skip symbols from files we're about to re-process
    if (removedFilesSet.has(sym.filePath)) continue;
    symbolTable.add(sym.filePath, sym.name, sym.id, sym.label as any);
  }

  if (isDev) {
    const stats = symbolTable.getStats();
    console.log(`📊 Symbol table rebuilt: ${stats.globalSymbolCount} symbols from ${stats.fileCount} files`);
  }

  // ── Phase 4: Delete old data for modified/deleted files ─────────────
  onProgress({
    phase: 'extracting',
    percent: 20,
    message: `Removing old data for ${allRemoved.length} files...`,
  });

  const { deletedNodes } = await deleteNodesForFiles(allRemoved);
  result.nodesDeleted = deletedNodes;

  if (isDev && deletedNodes > 0) {
    console.log(`  🗑️ Batch deleted ${deletedNodes} nodes for ${allRemoved.length} files`);
  }

  onProgress({
    phase: 'extracting',
    percent: 30,
    message: `Deleted ${result.nodesDeleted} nodes`,
  });

  // ── Phase 5: Parse changed/added files ──────────────────────────────
  if (allChanged.length === 0) {
    // Only deletions — skip to FTS rebuild
    onProgress({ phase: 'parsing', percent: 70, message: 'No files to parse' });
  } else {
    onProgress({
      phase: 'parsing',
      percent: 30,
      message: `Parsing ${allChanged.length} files...`,
    });

    // Read content for changed files
    const parseableChanged = allChanged.filter(p => getLanguageFromFilename(p));
    const chunkContents = await readFileContents(repoPath, allChanged);
    const chunkFiles = allChanged
      .filter(p => chunkContents.has(p))
      .map(p => ({ path: p, content: chunkContents.get(p)! }));

    // Create an in-memory graph for the changed files
    const graph = createKnowledgeGraph();

    // Add File nodes for changed files
    for (const file of chunkFiles) {
      graph.addNode({
        id: generateId('File', file.path),
        label: 'File',
        properties: {
          name: file.path.split('/').pop() || file.path,
          filePath: file.path,
        },
      });
    }

    // Process structure for new/modified files (creates Folder→File CONTAINS edges)
    processStructure(graph, allChanged);

    // Parse only parseable files with tree-sitter
    const parseableFiles = chunkFiles.filter(f => getLanguageFromFilename(f.path));

    if (parseableFiles.length > 0) {
      const astCache = createASTCache(parseableFiles.length);

      const workerData = await processParsing(
        graph, parseableFiles, symbolTable, astCache,
        (current, _total, filePath) => {
          const pct = 30 + Math.round((current / parseableFiles.length) * 25);
          onProgress({
            phase: 'parsing',
            percent: pct,
            message: `Parsing ${current}/${parseableFiles.length}...`,
            detail: filePath,
          });
        },
      );

      // Build import resolution context using ALL indexed paths + new paths
      const updatedPaths = [
        ...indexedPaths.filter(p => !removedFilesSet.has(p)),
        ...added,
      ];
      const importCtx = buildImportResolutionContext(updatedPaths);
      const allPathObjects = updatedPaths.map(p => ({ path: p }));

      onProgress({
        phase: 'imports',
        percent: 55,
        message: 'Resolving imports...',
      });

      if (workerData) {
        // Worker path: use extracted data
        await processImportsFromExtracted(
          graph, allPathObjects, workerData.imports, importMap, undefined, repoPath, importCtx,
        );

        onProgress({
          phase: 'calls',
          percent: 60,
          message: 'Resolving calls...',
        });

        if (workerData.calls.length > 0) {
          await processCallsFromExtracted(graph, workerData.calls, symbolTable, importMap);
        }

        onProgress({
          phase: 'heritage',
          percent: 65,
          message: 'Resolving inheritance...',
        });

        if (workerData.heritage.length > 0) {
          await processHeritageFromExtracted(graph, workerData.heritage, symbolTable);
        }
      } else {
        // Sequential fallback: re-import using AST cache
        const { processImports } = await import('./import-processor.js');
        const { processCalls } = await import('./call-processor.js');
        const { processHeritage } = await import('./heritage-processor.js');

        await processImports(graph, parseableFiles, astCache, importMap, undefined, repoPath, updatedPaths);
        await processCalls(graph, parseableFiles, astCache, symbolTable, importMap);
        await processHeritage(graph, parseableFiles, astCache, symbolTable);
      }

      astCache.clear();

      // Free import context
      allPathObjects.length = 0;
      importCtx.resolveCache.clear();
    }

    onProgress({
      phase: 'parsing',
      percent: 70,
      message: 'Inserting new data into KuzuDB...',
    });

    // ── Phase 6: Insert new nodes and edges into KuzuDB ─────────────
    // Collect nodes (skip File/Folder that already exist from structure)
    const nodesToInsert: Array<{ label: string; properties: Record<string, any> }> = [];
    graph.forEachNode(node => {
      nodesToInsert.push({
        label: node.label,
        properties: {
          id: node.id,
          name: node.properties.name,
          filePath: node.properties.filePath,
          startLine: node.properties.startLine || 0,
          endLine: node.properties.endLine || 0,
          isExported: node.properties.isExported || false,
          content: (node.properties as any).content || '',
          description: node.properties.description || '',
        },
      });
    });

    const csvDir = path.join(path.dirname(lbugPath), 'csv');

    if (nodesToInsert.length > 0) {
      const { inserted, failed } = await batchInsertNodesViaCSV(nodesToInsert, csvDir);
      result.nodesInserted = inserted;

      if (isDev) {
        console.log(`📦 CSV-inserted ${inserted} nodes (${failed} failed)`);
      }
    }

    // Collect relationships
    const relsToInsert: RelationshipInput[] = [];
    graph.forEachRelationship(rel => {
      // Determine node labels from their IDs (format: "Label:filePath:name")
      const fromLabel = rel.sourceId.split(':')[0];
      const toLabel = rel.targetId.split(':')[0];

      if (fromLabel && toLabel) {
        relsToInsert.push({
          fromId: rel.sourceId,
          fromLabel,
          toId: rel.targetId,
          toLabel,
          type: rel.type,
          confidence: rel.confidence,
          reason: rel.reason || '',
          step: rel.step,
        });
      }
    });

    if (relsToInsert.length > 0) {
      const { inserted, failed } = await batchInsertRelationshipsViaCSV(relsToInsert, csvDir);
      result.edgesInserted = inserted;

      if (isDev) {
        console.log(`🔗 CSV-inserted ${inserted} edges (${failed} failed)`);
      }
    }
  }

  // ── Phase 7: Rebuild FTS indexes ────────────────────────────────────
  onProgress({
    phase: 'enriching',
    percent: 85,
    message: 'Rebuilding search indexes...',
  });

  const ftsConfigs = [
    { table: 'File', index: 'file_fts', columns: ['name', 'content'] },
    { table: 'Function', index: 'function_fts', columns: ['name', 'content'] },
    { table: 'Class', index: 'class_fts', columns: ['name', 'content'] },
    { table: 'Method', index: 'method_fts', columns: ['name', 'content'] },
    { table: 'Interface', index: 'interface_fts', columns: ['name', 'content'] },
  ];

  for (const { table, index, columns } of ftsConfigs) {
    try {
      // Don't drop — FTS indexes auto-update with new nodes. Only create if missing.
      await createFTSIndex(table, index, columns);
    } catch {
      // Non-fatal — index already exists or creation failed
    }
  }

  // ── Phase 8: Conditionally recompute communities and processes ──────
  // Only recompute if a significant fraction of nodes changed.
  // For small changes (e.g. 10 files out of 5000+), existing communities remain valid.
  const totalNodes = indexedPaths.length;
  const changeRatio = totalNodes > 0
    ? (result.nodesInserted + result.nodesDeleted) / totalNodes
    : 1.0;
  const COMMUNITY_RECOMPUTE_THRESHOLD = 0.20;

  if (changeRatio >= COMMUNITY_RECOMPUTE_THRESHOLD) {
    onProgress({
      phase: 'communities',
      percent: 88,
      message: 'Recomputing communities...',
    });

    try {
      const { processCommunities } = await import('./community-processor.js');
      const { processProcesses } = await import('./process-processor.js');

      await deleteCommunitiesAndProcesses();
      const fullGraph = await buildGraphFromKuzu();

      const communityResult = await processCommunities(fullGraph, (message, progress) => {
        onProgress({
          phase: 'communities',
          percent: Math.round(88 + progress * 0.05),
          message,
        });
      });

      if (isDev) {
        console.log(`🏘️ Communities: ${communityResult.stats.totalCommunities} (modularity: ${communityResult.stats.modularity.toFixed(3)})`);
      }

      // Batch insert communities — special schema requires individual queries for now
      const commCsvDir = path.join(path.dirname(lbugPath), 'csv');
      if (communityResult.communities.length > 0) {
        const escVal = (v: unknown): string => {
          if (v === null || v === undefined) return 'NULL';
          if (typeof v === 'number') return String(v);
          return `'${String(v).replace(/\\/g, '\\\\').replace(/'/g, "''")}'`;
        };
        for (const comm of communityResult.communities) {
          try {
            await executeQuery(
              `CREATE (n:Community {id: ${escVal(comm.id)}, label: ${escVal(comm.label)}, heuristicLabel: ${escVal(comm.heuristicLabel)}, keywords: '', description: '', enrichedBy: '', cohesion: ${comm.cohesion}, symbolCount: ${comm.symbolCount}})`
            );
          } catch { /* may already exist */ }
        }
      }

      // Batch insert MEMBER_OF relationships via CSV
      const membershipRels: RelationshipInput[] = [];
      for (const membership of communityResult.memberships) {
        const fromLabel = membership.nodeId.split(':')[0];
        if (fromLabel) {
          membershipRels.push({
            fromId: membership.nodeId,
            fromLabel,
            toId: membership.communityId,
            toLabel: 'Community',
            type: 'MEMBER_OF',
            confidence: 1.0,
            reason: 'leiden-algorithm',
          });
        }
      }
      if (membershipRels.length > 0) {
        await batchInsertRelationshipsViaCSV(membershipRels, commCsvDir);
      }

      result.communities = communityResult.stats.totalCommunities;

      onProgress({ phase: 'processes', percent: 94, message: 'Detecting execution flows...' });

      let symbolCount = 0;
      fullGraph.forEachNode(n => { if (n.label !== 'File' && n.label !== 'Folder') symbolCount++; });
      const dynamicMaxProcesses = Math.max(20, Math.min(300, Math.round(symbolCount / 10)));

      const processResult = await processProcesses(
        fullGraph, communityResult.memberships,
        (message, progress) => {
          onProgress({ phase: 'processes', percent: Math.round(94 + progress * 0.05), message });
        },
        { maxProcesses: dynamicMaxProcesses, minSteps: 3 },
      );

      if (isDev) {
        console.log(`🔄 Processes: ${processResult.stats.totalProcesses} (${processResult.stats.crossCommunityCount} cross-community)`);
      }

      // Process nodes have special schema, insert individually
      const escValP = (v: unknown): string => {
        if (v === null || v === undefined) return 'NULL';
        if (typeof v === 'number') return String(v);
        return `'${String(v).replace(/\\/g, '\\\\').replace(/'/g, "''")}'`;
      };
      for (const proc of processResult.processes) {
        try {
          await executeQuery(
            `CREATE (n:Process {id: ${escValP(proc.id)}, label: ${escValP(proc.label)}, heuristicLabel: ${escValP(proc.heuristicLabel)}, processType: ${escValP(proc.processType)}, stepCount: ${proc.stepCount}, communities: ${escValP(proc.communities.join(','))}, entryPointId: ${escValP(proc.entryPointId)}, terminalId: ${escValP(proc.terminalId)}})`
          );
        } catch { /* may already exist */ }
      }

      // Batch insert STEP_IN_PROCESS relationships via CSV
      const stepRels: RelationshipInput[] = [];
      for (const step of processResult.steps) {
        const fromLabel = step.nodeId.split(':')[0];
        if (fromLabel) {
          stepRels.push({
            fromId: step.nodeId,
            fromLabel,
            toId: step.processId,
            toLabel: 'Process',
            type: 'STEP_IN_PROCESS',
            confidence: 1.0,
            reason: 'trace-detection',
            step: step.step,
          });
        }
      }
      if (stepRels.length > 0) {
        await batchInsertRelationshipsViaCSV(stepRels, commCsvDir);
      }

      result.processes = processResult.stats.totalProcesses;
    } catch (e) {
      if (isDev) {
        console.warn(`⚠️ Community/process recomputation failed: ${e instanceof Error ? e.message : String(e)}`);
      }
    }
  } else {
    if (isDev) {
      console.log(`⏭️ Skipping community/process recomputation (${(changeRatio * 100).toFixed(1)}% changed < ${COMMUNITY_RECOMPUTE_THRESHOLD * 100}% threshold)`);
    }
  }

  onProgress({
    phase: 'complete',
    percent: 100,
    message: `Incremental update complete: +${result.added} ~${result.modified} -${result.deleted} files`,
  });

  return result;
};

/**
 * Error thrown when the number of changed files exceeds the threshold
 * for incremental updates. The caller should fall back to a full rebuild.
 */
export class ThresholdExceededError extends Error {
  constructor(
    message: string,
    public changedCount: number,
    public totalCount: number,
  ) {
    super(message);
    this.name = 'ThresholdExceededError';
  }
}
