import { KnowledgeGraph, GraphNode, GraphRelationship } from '../graph/types.js';
import Parser from 'tree-sitter';
import { loadParser, loadLanguage } from '../tree-sitter/parser-loader.js';
import { LANGUAGE_QUERIES } from './tree-sitter-queries.js';
import { generateId } from '../../lib/utils.js';
import { SymbolTable } from './symbol-table.js';
import { ASTCache } from './ast-cache.js';
import { getLanguageFromFilename, yieldToEventLoop, getDefinitionNodeFromCaptures, findEnclosingClassId, extractMethodSignature } from './utils.js';
import { detectFrameworkFromAST } from './framework-detection.js';
import { WorkerPool } from './workers/worker-pool.js';
import type { ParseWorkerResult, ParseWorkerInput, ExtractedImport, ExtractedCall, ExtractedHeritage } from './workers/parse-worker.js';

export type FileProgressCallback = (current: number, total: number, filePath: string) => void;

export interface WorkerExtractedData {
  imports: ExtractedImport[];
  calls: ExtractedCall[];
  heritage: ExtractedHeritage[];
}

// ============================================================================
// EXPORT DETECTION - Language-specific visibility detection
// ============================================================================

/**
 * Check if a symbol (function, class, etc.) is exported/public
 * Handles all 9 supported languages with explicit logic
 *
 * @param node - The AST node for the symbol name
 * @param name - The symbol name
 * @param language - The programming language
 * @returns true if the symbol is exported/public
 */
const isNodeExported = (node: any, name: string, language: string): boolean => {
  let current = node;

  switch (language) {
    // JavaScript/TypeScript: Check for export keyword in ancestors
    case 'javascript':
    case 'typescript':
      while (current) {
        const type = current.type;
        if (type === 'export_statement' ||
            type === 'export_specifier' ||
            type === 'lexical_declaration' && current.parent?.type === 'export_statement') {
          return true;
        }
        // Also check if text starts with 'export '
        if (current.text?.startsWith('export ')) {
          return true;
        }
        current = current.parent;
      }
      return false;

    // Python: Public if no leading underscore (convention)
    case 'python':
      return !name.startsWith('_');

    // Java: Check for 'public' modifier
    // In tree-sitter Java, modifiers are siblings of the name node, not parents
    case 'java':
      while (current) {
        // Check if this node or any sibling is a 'modifiers' node containing 'public'
        if (current.parent) {
          const parent = current.parent;
          // Check all children of the parent for modifiers
          for (let i = 0; i < parent.childCount; i++) {
            const child = parent.child(i);
            if (child?.type === 'modifiers' && child.text?.includes('public')) {
              return true;
            }
          }
          // Also check if the parent's text starts with 'public' (fallback)
          if (parent.type === 'method_declaration' || parent.type === 'constructor_declaration') {
            if (parent.text?.trimStart().startsWith('public')) {
              return true;
            }
          }
        }
        current = current.parent;
      }
      return false;

    // C#: Check for 'public' modifier in ancestors
    case 'csharp':
      while (current) {
        if (current.type === 'modifier' || current.type === 'modifiers') {
          if (current.text?.includes('public')) return true;
        }
        current = current.parent;
      }
      return false;

    // Go: Uppercase first letter = exported
    case 'go':
      if (name.length === 0) return false;
      const first = name[0];
      // Must be uppercase letter (not a number or symbol)
      return first === first.toUpperCase() && first !== first.toLowerCase();

    // Rust: Check for 'pub' visibility modifier
    case 'rust':
      while (current) {
        if (current.type === 'visibility_modifier') {
          if (current.text?.includes('pub')) return true;
        }
        current = current.parent;
      }
      return false;

    // Kotlin: default visibility is public (unlike Java).
    // visibility_modifier is inside modifiers, a sibling of the name node within the declaration.
    case 'kotlin':
      while (current) {
        if (current.parent) {
          for (let i = 0; i < current.parent.childCount; i++) {
            const sibling = current.parent.child(i);
            if (sibling?.type === 'modifiers') {
              for (let j = 0; j < sibling.childCount; j++) {
                const child = sibling.child(j);
                if (child?.type === 'visibility_modifier') {
                  const text = child.text;
                  if (text === 'private' || text === 'internal' || text === 'protected') return false;
                  if (text === 'public') return true;
                }
              }
            }
          }
        }
        current = current.parent;
      }
      // No visibility modifier = public (Kotlin default)
      return true;

    // C/C++: No native export concept at language level
    // Entry points will be detected via name patterns (main, etc.)
    case 'c':
    case 'cpp':
      return false;

    // Ruby: Public if no leading underscore (convention, like Python)
    case 'ruby':
      return !name.startsWith('_');

    // Swift: Check for 'public' or 'open' access modifiers
    case 'swift':
      while (current) {
        if (current.type === 'modifiers' || current.type === 'visibility_modifier') {
          const text = current.text || '';
          if (text.includes('public') || text.includes('open')) return true;
        }
        current = current.parent;
      }
      return false;

    default:
      return false;
  }
};

// ============================================================================
// Worker-based parallel parsing
// ============================================================================

const processParsingWithWorkers = async (
  graph: KnowledgeGraph,
  files: { path: string; content: string }[],
  symbolTable: SymbolTable,
  astCache: ASTCache,
  workerPool: WorkerPool,
  onFileProgress?: FileProgressCallback,
): Promise<WorkerExtractedData> => {
  // Filter to parseable files only
  const parseableFiles: ParseWorkerInput[] = [];
  for (const file of files) {
    const lang = getLanguageFromFilename(file.path);
    if (lang) parseableFiles.push({ path: file.path, content: file.content });
  }

  if (parseableFiles.length === 0) return { imports: [], calls: [], heritage: [] };

  const total = files.length;

  // Dispatch to worker pool — pool handles splitting into chunks and sub-batching
  const chunkResults = await workerPool.dispatch<ParseWorkerInput, ParseWorkerResult>(
    parseableFiles,
    (filesProcessed) => {
      onFileProgress?.(Math.min(filesProcessed, total), total, 'Parsing...');
    },
  );

  // Merge results from all workers into graph and symbol table
  const allImports: ExtractedImport[] = [];
  const allCalls: ExtractedCall[] = [];
  const allHeritage: ExtractedHeritage[] = [];
  for (const result of chunkResults) {
    for (const node of result.nodes) {
      graph.addNode({
        id: node.id,
        label: node.label as any,
        properties: node.properties as any,
      });
    }

    for (const rel of result.relationships) {
      graph.addRelationship(rel);
    }

    for (const sym of result.symbols) {
      symbolTable.add(sym.filePath, sym.name, sym.nodeId, sym.type as any);
    }

    allImports.push(...result.imports);
    allCalls.push(...result.calls);
    allHeritage.push(...result.heritage);
  }

  // Final progress
  onFileProgress?.(total, total, 'done');
  return { imports: allImports, calls: allCalls, heritage: allHeritage };
};

// ============================================================================
// Sequential fallback (original implementation)
// ============================================================================

const processParsingSequential = async (
  graph: KnowledgeGraph,
  files: { path: string; content: string }[],
  symbolTable: SymbolTable,
  astCache: ASTCache,
  onFileProgress?: FileProgressCallback
) => {
  const parser = await loadParser();
  const total = files.length;

  for (let i = 0; i < files.length; i++) {
    const file = files[i];

    onFileProgress?.(i + 1, total, file.path);

    if (i % 20 === 0) await yieldToEventLoop();

    const language = getLanguageFromFilename(file.path);

    if (!language) continue;

    // Skip very large files — they can crash tree-sitter or cause OOM
    if (file.content.length > 512 * 1024) continue;

    try {
      await loadLanguage(language, file.path);
    } catch {
      continue;  // parser unavailable — already warned in pipeline
    }

    let tree;
    try {
      tree = parser.parse(file.content, undefined, { bufferSize: 1024 * 256 });
    } catch (parseError) {
      console.warn(`Skipping unparseable file: ${file.path}`);
      continue;
    }

    astCache.set(file.path, tree);

    const queryString = LANGUAGE_QUERIES[language];
    if (!queryString) {
      continue;
    }

    let query;
    let matches;
    try {
      const language = parser.getLanguage();
      query = new Parser.Query(language, queryString);
      matches = query.matches(tree.rootNode);
    } catch (queryError) {
      console.warn(`Query error for ${file.path}:`, queryError);
      continue;
    }

    matches.forEach(match => {
      const captureMap: Record<string, any> = {};

      match.captures.forEach(c => {
        captureMap[c.name] = c.node;
      });

      if (captureMap['import']) {
        return;
      }

      if (captureMap['call']) {
        return;
      }

      const nameNode = captureMap['name'];
      if (!nameNode) return;

      const nodeName = nameNode.text;

      let nodeLabel = 'CodeElement';

      if (captureMap['definition.function']) nodeLabel = 'Function';
      else if (captureMap['definition.class']) nodeLabel = 'Class';
      else if (captureMap['definition.interface']) nodeLabel = 'Interface';
      else if (captureMap['definition.method']) nodeLabel = 'Method';
      else if (captureMap['definition.struct']) nodeLabel = 'Struct';
      else if (captureMap['definition.enum']) nodeLabel = 'Enum';
      else if (captureMap['definition.namespace']) nodeLabel = 'Namespace';
      else if (captureMap['definition.module']) nodeLabel = 'Module';
      else if (captureMap['definition.trait']) nodeLabel = 'Trait';
      else if (captureMap['definition.impl']) nodeLabel = 'Impl';
      else if (captureMap['definition.type']) nodeLabel = 'TypeAlias';
      else if (captureMap['definition.const']) nodeLabel = 'Const';
      else if (captureMap['definition.static']) nodeLabel = 'Static';
      else if (captureMap['definition.typedef']) nodeLabel = 'Typedef';
      else if (captureMap['definition.macro']) nodeLabel = 'Macro';
      else if (captureMap['definition.union']) nodeLabel = 'Union';
      else if (captureMap['definition.property']) nodeLabel = 'Property';
      else if (captureMap['definition.record']) nodeLabel = 'Record';
      else if (captureMap['definition.delegate']) nodeLabel = 'Delegate';
      else if (captureMap['definition.annotation']) nodeLabel = 'Annotation';
      else if (captureMap['definition.constructor']) nodeLabel = 'Constructor';
      else if (captureMap['definition.template']) nodeLabel = 'Template';

      const nodeId = generateId(nodeLabel, `${file.path}:${nodeName}`);

      const definitionNode = getDefinitionNodeFromCaptures(captureMap);
      const frameworkHint = definitionNode
        ? detectFrameworkFromAST(language, definitionNode.text || '')
        : null;

      const node: GraphNode = {
        id: nodeId,
        label: nodeLabel as any,
        properties: {
          name: nodeName,
          filePath: file.path,
          startLine: nameNode.startPosition.row,
          endLine: nameNode.endPosition.row,
          language: language,
          isExported: isNodeExported(nameNode, nodeName, language),
          ...(frameworkHint ? {
            astFrameworkMultiplier: frameworkHint.entryPointMultiplier,
            astFrameworkReason: frameworkHint.reason,
          } : {}),
        }
      };

      graph.addNode(node);

      // Compute enclosing class for Method/Constructor/Property/Function — used for ownerId and HAS_METHOD
      const needsOwner = nodeLabel === 'Method' || nodeLabel === 'Constructor' || nodeLabel === 'Property' || nodeLabel === 'Function';
      const enclosingClassId = needsOwner ? findEnclosingClassId(nameNode || definitionNode, file.path) : null;

      // Extract method signature for type resolution
      const methodSig = (nodeLabel === 'Function' || nodeLabel === 'Method' || nodeLabel === 'Constructor') && definitionNode
        ? extractMethodSignature(definitionNode)
        : undefined;

      symbolTable.add(file.path, nodeName, nodeId, nodeLabel as any, {
        parameterCount: methodSig?.parameterCount,
        returnType: methodSig?.returnType,
        ownerId: enclosingClassId ?? undefined,
      });

      const fileId = generateId('File', file.path);

      const relId = generateId('DEFINES', `${fileId}->${nodeId}`);

      const relationship: GraphRelationship = {
        id: relId,
        sourceId: fileId,
        targetId: nodeId,
        type: 'DEFINES',
        confidence: 1.0,
        reason: '',
      };

      graph.addRelationship(relationship);

      // HAS_METHOD / HAS_PROPERTY: link member to enclosing class
      if (enclosingClassId) {
        const memberEdgeType = nodeLabel === 'Property' ? 'HAS_PROPERTY' : 'HAS_METHOD';
        graph.addRelationship({
          id: generateId(memberEdgeType, `${enclosingClassId}->${nodeId}`),
          sourceId: enclosingClassId,
          targetId: nodeId,
          type: memberEdgeType,
          confidence: 1.0,
          reason: '',
        });
      }
    });
  }
};

// ============================================================================
// Public API
// ============================================================================

export const processParsing = async (
  graph: KnowledgeGraph,
  files: { path: string; content: string }[],
  symbolTable: SymbolTable,
  astCache: ASTCache,
  onFileProgress?: FileProgressCallback,
  workerPool?: WorkerPool,
): Promise<WorkerExtractedData | null> => {
  if (workerPool) {
    try {
      return await processParsingWithWorkers(graph, files, symbolTable, astCache, workerPool, onFileProgress);
    } catch (err) {
      console.warn('Worker pool parsing failed, falling back to sequential:', err instanceof Error ? err.message : err);
    }
  }

  // Fallback: sequential parsing (no pre-extracted data)
  await processParsingSequential(graph, files, symbolTable, astCache, onFileProgress);
  return null;
};
