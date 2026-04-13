import { KnowledgeGraph } from '../graph/types.js';
import { ASTCache } from './ast-cache.js';
import { SymbolTable } from './symbol-table.js';
import { ImportMap } from './import-processor.js';
import Parser from 'tree-sitter';
import { loadParser, loadLanguage, isLanguageAvailable } from '../tree-sitter/parser-loader.js';
import { LANGUAGE_QUERIES } from './tree-sitter-queries.js';
import { generateId } from '../../lib/utils.js';
import { getLanguageFromFilename, isVerboseIngestionEnabled, yieldToEventLoop } from './utils.js';
import type { ExtractedCall } from './workers/parse-worker.js';

/**
 * Node types that represent function/method definitions across languages.
 * Used to find the enclosing function for a call site.
 */
const FUNCTION_NODE_TYPES = new Set([
  // TypeScript/JavaScript
  'function_declaration',
  'arrow_function',
  'function_expression',
  'method_definition',
  'generator_function_declaration',
  // Python
  'function_definition',
  // Common async variants
  'async_function_declaration',
  'async_arrow_function',
  // Java
  'method_declaration',
  'constructor_declaration',
  // C/C++
  // 'function_definition' already included above
  // Go
  // 'method_declaration' already included from Java
  // C#
  'local_function_statement',
  // Rust
  'function_item',
  'impl_item', // Methods inside impl blocks
  // Kotlin
  'lambda_literal',
  // Swift
  'init_declaration',
  'deinit_declaration',
  // Ruby
  'method',           // def foo
  'singleton_method', // def self.foo
]);

/**
 * Walk up the AST from a node to find the enclosing function/method.
 * Returns null if the call is at module/file level (top-level code).
 */
const findEnclosingFunction = (
  node: any,
  filePath: string,
  symbolTable: SymbolTable
): string | null => {
  let current = node.parent;
  
  while (current) {
    if (FUNCTION_NODE_TYPES.has(current.type)) {
      // Found enclosing function - try to get its name
      let funcName: string | null = null;
      let label = 'Function';
      
      // Swift init/deinit — handle before generic cases (more specific)
      if (current.type === 'init_declaration' || current.type === 'deinit_declaration') {
        const funcName = current.type === 'init_declaration' ? 'init' : 'deinit';
        const startLine = current.startPosition?.row ?? 0;
        return generateId('Constructor', `${filePath}:${funcName}:${startLine}`);
      }

      // Different node types have different name locations
      if (current.type === 'function_declaration' ||
          current.type === 'function_definition' ||
          current.type === 'async_function_declaration' ||
          current.type === 'generator_function_declaration' ||
          current.type === 'function_item') { // Rust function
        // Named function: function foo() {}
        const nameNode = current.childForFieldName?.('name') || 
                         current.children?.find((c: any) => c.type === 'identifier' || c.type === 'property_identifier');
        funcName = nameNode?.text;
      } else if (current.type === 'impl_item') {
        // Rust method inside impl block: wrapper around function_item or const_item
        // We need to look inside for the function_item
        const funcItem = current.children?.find((c: any) => c.type === 'function_item');
        if (funcItem) {
           const nameNode = funcItem.childForFieldName?.('name') || 
                            funcItem.children?.find((c: any) => c.type === 'identifier');
           funcName = nameNode?.text;
           label = 'Method';
        }
      } else if (current.type === 'method_definition') {
        // Method: foo() {} inside class (JS/TS)
        const nameNode = current.childForFieldName?.('name') ||
                         current.children?.find((c: any) => c.type === 'property_identifier');
        funcName = nameNode?.text;
        label = 'Method';
      } else if (current.type === 'method_declaration') {
        // Java method: public void foo() {}
        const nameNode = current.childForFieldName?.('name') ||
                         current.children?.find((c: any) => c.type === 'identifier');
        funcName = nameNode?.text;
        label = 'Method';
      } else if (current.type === 'constructor_declaration') {
        // Java constructor: public ClassName() {}
        const nameNode = current.childForFieldName?.('name') ||
                         current.children?.find((c: any) => c.type === 'identifier');
        funcName = nameNode?.text;
        label = 'Method'; // Treat constructors as methods for process detection
      } else if (current.type === 'arrow_function' || current.type === 'function_expression') {
        // Arrow/expression: const foo = () => {} - check parent variable declarator
        const parent = current.parent;
        if (parent?.type === 'variable_declarator') {
          const nameNode = parent.childForFieldName?.('name') ||
                           parent.children?.find((c: any) => c.type === 'identifier');
          funcName = nameNode?.text;
        }
      }
      
      if (funcName) {
        // Look up the function in symbol table to get its node ID
        // Try exact match first
        const nodeId = symbolTable.lookupExact(filePath, funcName);
        if (nodeId) return nodeId;
        
        // Try construct ID manually if lookup fails (common for non-exported internal functions)
        // Format should match what parsing-processor generates: "Function:path/to/file:funcName"
        // Check if we already have a node with this ID in the symbol table to be safe
        const startLine = current.startPosition?.row ?? 0;
        const generatedId = generateId(label, `${filePath}:${funcName}:${startLine}`);

        // Ideally we should verify this ID exists, but strictly speaking if we are inside it,
        // it SHOULD exist. Returning it is better than falling back to File.
        return generatedId;
      }
      
      // Couldn't determine function name - try parent (might be nested)
    }
    current = current.parent;
  }
  
  return null; // Top-level call (not inside any function)
};

export const processCalls = async (
  graph: KnowledgeGraph,
  files: { path: string; content: string }[],
  astCache: ASTCache,
  symbolTable: SymbolTable,
  importMap: ImportMap,
  onProgress?: (current: number, total: number) => void
) => {
  const parser = await loadParser();
  const logSkipped = isVerboseIngestionEnabled();
  const skippedByLang = logSkipped ? new Map<string, number>() : null;

  for (let i = 0; i < files.length; i++) {
    const file = files[i];
    onProgress?.(i + 1, files.length);
    if (i % 20 === 0) await yieldToEventLoop();

    // 1. Check language support first
    const language = getLanguageFromFilename(file.path);
    if (!language) continue;
    if (!isLanguageAvailable(language)) {
      if (skippedByLang) {
        skippedByLang.set(language, (skippedByLang.get(language) ?? 0) + 1);
      }
      continue;
    }

    const queryStr = LANGUAGE_QUERIES[language];
    if (!queryStr) continue;

    // 2. ALWAYS load the language before querying (parser is stateful)
    await loadLanguage(language, file.path);

    // 3. Get AST (Try Cache First)
    let tree = astCache.get(file.path);
    let wasReparsed = false;

    if (!tree) {
      // Cache Miss: Re-parse
      // Use larger bufferSize for files > 32KB
      try {
        tree = parser.parse(file.content, undefined, { bufferSize: 1024 * 256 });
      } catch (parseError) {
        // Skip files that can't be parsed
        continue;
      }
      wasReparsed = true;
      // Cache re-parsed tree so heritage phase gets hits
      astCache.set(file.path, tree);
    }

    let query;
    let matches;
    try {
      const language = parser.getLanguage();
      query = new Parser.Query(language, queryStr);
      matches = query.matches(tree.rootNode);
    } catch (queryError) {
      console.warn(`Query error for ${file.path}:`, queryError);
      continue;
    }

    // 3. Process each call match
    matches.forEach(match => {
      const captureMap: Record<string, any> = {};
      match.captures.forEach(c => captureMap[c.name] = c.node);

      // Only process @call captures
      if (!captureMap['call']) return;

      const nameNode = captureMap['call.name'];
      if (!nameNode) return;

      const calledName = nameNode.text;

      // Skip common built-ins and noise
      if (isBuiltInOrNoise(calledName)) return;

      // 4. Resolve the target using priority strategy (returns confidence)
      const resolved = resolveCallTarget(
        calledName,
        file.path,
        symbolTable,
        importMap
      );

      if (!resolved) return;

      // 5. Find the enclosing function (caller)
      const callNode = captureMap['call'];
      const enclosingFuncId = findEnclosingFunction(callNode, file.path, symbolTable);
      
      // Use enclosing function as source, fallback to file for top-level calls
      const sourceId = enclosingFuncId || generateId('File', file.path);
      
      const relId = generateId('CALLS', `${sourceId}:${calledName}->${resolved.nodeId}`);

      graph.addRelationship({
        id: relId,
        sourceId,
        targetId: resolved.nodeId,
        type: 'CALLS',
        confidence: resolved.confidence,
        reason: resolved.reason,
      });
    });

    // Extract Laravel routes from route files via procedural AST walk
    if (language === 'php' && (file.path.includes('/routes/') || file.path.startsWith('routes/')) && file.path.endsWith('.php')) {
      const extractedRoutes = extractLaravelRoutes(tree, file.path);
      for (const route of extractedRoutes) {
        if (!route.controllerName || !route.methodName) continue;

        const controllerDefs = symbolTable.lookupFuzzy(route.controllerName);
        if (controllerDefs.length === 0) continue;

        const routeImportedFiles = importMap.get(route.filePath);
        let controllerDef = controllerDefs[0];
        let conf = controllerDefs.length === 1 ? 0.7 : 0.5;

        if (routeImportedFiles) {
          for (const def of controllerDefs) {
            if (routeImportedFiles.has(def.filePath)) {
              controllerDef = def;
              conf = 0.9;
              break;
            }
          }
        }

        const methodId = symbolTable.lookupExact(controllerDef.filePath, route.methodName);
        const routeSourceId = generateId('File', route.filePath);

        if (!methodId) {
          const guessedId = generateId('Method', `${controllerDef.filePath}:${route.methodName}`);
          const routeRelId = generateId('CALLS', `${routeSourceId}:route->${guessedId}`);
          graph.addRelationship({
            id: routeRelId,
            sourceId: routeSourceId,
            targetId: guessedId,
            type: 'CALLS',
            confidence: conf * 0.8,
            reason: 'laravel-route',
          });
          continue;
        }

        const routeRelId = generateId('CALLS', `${routeSourceId}:route->${methodId}`);
        graph.addRelationship({
          id: routeRelId,
          sourceId: routeSourceId,
          targetId: methodId,
          type: 'CALLS',
          confidence: conf,
          reason: 'laravel-route',
        });
      }
    }

    // Tree is now owned by the LRU cache — no manual delete needed
  }

  // Emit verbose warnings for skipped languages
  if (skippedByLang) {
    for (const [lang, count] of skippedByLang) {
      console.warn(`[ingestion] Skipped ${count} ${lang} file(s) in call processing -- ${lang} parser not available.`);
    }
  }
};

/**
 * Resolution result with confidence scoring
 */
interface ResolveResult {
  nodeId: string;
  confidence: number;  // 0-1: how sure are we?
  reason: string;      // 'import-resolved' | 'same-file' | 'fuzzy-global'
}

/**
 * Resolve a function call to its target node ID using priority strategy:
 * A. Check imported files first (highest confidence)
 * B. Check local file definitions
 * C. Fuzzy global search (lowest confidence)
 * 
 * Returns confidence score so agents know what to trust.
 */
const resolveCallTarget = (
  calledName: string,
  currentFile: string,
  symbolTable: SymbolTable,
  importMap: ImportMap
): ResolveResult | null => {
  // Strategy B first (cheapest — single map lookup): Check local file
  const localNodeId = symbolTable.lookupExact(currentFile, calledName);
  if (localNodeId) {
    return { nodeId: localNodeId, confidence: 0.85, reason: 'same-file' };
  }

  // Strategy A: Check if any definition of calledName is in an imported file
  // Reversed: instead of iterating all imports and checking each, get all definitions
  // and check if any is imported. O(definitions) instead of O(imports).
  const allDefs = symbolTable.lookupFuzzy(calledName);
  if (allDefs.length > 0) {
    const importedFiles = importMap.get(currentFile);
    if (importedFiles) {
      for (const def of allDefs) {
        if (importedFiles.has(def.filePath)) {
          return { nodeId: def.nodeId, confidence: 0.9, reason: 'import-resolved' };
        }
      }
    }

    // Strategy C: Fuzzy global (no import match found)
    const confidence = allDefs.length === 1 ? 0.5 : 0.3;
    return { nodeId: allDefs[0].nodeId, confidence, reason: 'fuzzy-global' };
  }

  return null;
};

/**
 * Filter out common built-in functions and noise
 * that shouldn't be tracked as calls
 */
/** Pre-built set (module-level singleton) to avoid re-creating per call */
const BUILT_IN_NAMES = new Set([
  // JavaScript/TypeScript built-ins
  'console', 'log', 'warn', 'error', 'info', 'debug',
  'setTimeout', 'setInterval', 'clearTimeout', 'clearInterval',
  'parseInt', 'parseFloat', 'isNaN', 'isFinite',
  'encodeURI', 'decodeURI', 'encodeURIComponent', 'decodeURIComponent',
  'JSON', 'parse', 'stringify',
  'Object', 'Array', 'String', 'Number', 'Boolean', 'Symbol', 'BigInt',
  'Map', 'Set', 'WeakMap', 'WeakSet',
  'Promise', 'resolve', 'reject', 'then', 'catch', 'finally',
  'Math', 'Date', 'RegExp', 'Error',
  'require', 'import', 'export',
  'fetch', 'Response', 'Request',
  // React hooks and common functions
  'useState', 'useEffect', 'useCallback', 'useMemo', 'useRef', 'useContext',
  'useReducer', 'useLayoutEffect', 'useImperativeHandle', 'useDebugValue',
  'createElement', 'createContext', 'createRef', 'forwardRef', 'memo', 'lazy',
  // Common array/object methods
  'map', 'filter', 'reduce', 'forEach', 'find', 'findIndex', 'some', 'every',
  'includes', 'indexOf', 'slice', 'splice', 'concat', 'join', 'split',
  'push', 'pop', 'shift', 'unshift', 'sort', 'reverse',
  'keys', 'values', 'entries', 'assign', 'freeze', 'seal',
  'hasOwnProperty', 'toString', 'valueOf',
  // Python built-ins
  'print', 'len', 'range', 'str', 'int', 'float', 'list', 'dict', 'set', 'tuple',
  'open', 'read', 'write', 'close', 'append', 'extend', 'update',
  'super', 'type', 'isinstance', 'issubclass', 'getattr', 'setattr', 'hasattr',
  'enumerate', 'zip', 'sorted', 'reversed', 'min', 'max', 'sum', 'abs',
  // Kotlin stdlib
  'println', 'print', 'readLine', 'require', 'requireNotNull', 'check', 'assert', 'lazy', 'error',
  'listOf', 'mapOf', 'setOf', 'mutableListOf', 'mutableMapOf', 'mutableSetOf',
  'arrayOf', 'sequenceOf', 'also', 'apply', 'run', 'with', 'takeIf', 'takeUnless',
  'TODO', 'buildString', 'buildList', 'buildMap', 'buildSet',
  'repeat', 'synchronized',
  // Kotlin coroutine builders & scope functions
  'launch', 'async', 'runBlocking', 'withContext', 'coroutineScope',
  'supervisorScope', 'delay',
  // Kotlin Flow operators
  'flow', 'flowOf', 'collect', 'emit', 'onEach', 'catch',
  'buffer', 'conflate', 'distinctUntilChanged',
  'flatMapLatest', 'flatMapMerge', 'combine',
  'stateIn', 'shareIn', 'launchIn',
  // Kotlin infix stdlib functions
  'to', 'until', 'downTo', 'step',
  // C/C++ standard library and common kernel helpers
  'printf', 'fprintf', 'sprintf', 'snprintf', 'vprintf', 'vfprintf', 'vsprintf', 'vsnprintf',
  'scanf', 'fscanf', 'sscanf',
  'malloc', 'calloc', 'realloc', 'free', 'memcpy', 'memmove', 'memset', 'memcmp',
  'strlen', 'strcpy', 'strncpy', 'strcat', 'strncat', 'strcmp', 'strncmp', 'strstr', 'strchr', 'strrchr',
  'atoi', 'atol', 'atof', 'strtol', 'strtoul', 'strtoll', 'strtoull', 'strtod',
  'sizeof', 'offsetof', 'typeof',
  'assert', 'abort', 'exit', '_exit',
  'fopen', 'fclose', 'fread', 'fwrite', 'fseek', 'ftell', 'rewind', 'fflush', 'fgets', 'fputs',
  // Linux kernel common macros/helpers (not real call targets)
  'likely', 'unlikely', 'BUG', 'BUG_ON', 'WARN', 'WARN_ON', 'WARN_ONCE',
  'IS_ERR', 'PTR_ERR', 'ERR_PTR', 'IS_ERR_OR_NULL',
  'ARRAY_SIZE', 'container_of', 'list_for_each_entry', 'list_for_each_entry_safe',
  'min', 'max', 'clamp', 'abs', 'swap',
  'pr_info', 'pr_warn', 'pr_err', 'pr_debug', 'pr_notice', 'pr_crit', 'pr_emerg',
  'printk', 'dev_info', 'dev_warn', 'dev_err', 'dev_dbg',
  'GFP_KERNEL', 'GFP_ATOMIC',
  'spin_lock', 'spin_unlock', 'spin_lock_irqsave', 'spin_unlock_irqrestore',
  'mutex_lock', 'mutex_unlock', 'mutex_init',
  'kfree', 'kmalloc', 'kzalloc', 'kcalloc', 'krealloc', 'kvmalloc', 'kvfree',
  'get', 'put',
  // Ruby built-ins and Kernel methods
  'puts', 'p', 'pp', 'raise', 'fail',
  'require', 'require_relative', 'load', 'autoload',
  'include', 'extend', 'prepend',
  'attr_accessor', 'attr_reader', 'attr_writer',
  'public', 'private', 'protected', 'module_function',
  'nil?', 'is_a?', 'kind_of?', 'instance_of?', 'respond_to?',
  'freeze', 'frozen?', 'dup', 'tap', 'yield_self',
  // Ruby enumerables
  'each', 'select', 'reject', 'detect', 'collect',
  'inject', 'flat_map', 'each_with_object', 'each_with_index',
  'any?', 'all?', 'none?', 'count', 'first', 'last',
  'sort_by', 'min_by', 'max_by',
  'group_by', 'partition', 'compact', 'flatten', 'uniq',
  // Swift/iOS built-ins and standard library
  'print', 'debugPrint', 'dump', 'fatalError', 'precondition', 'preconditionFailure',
  'assert', 'assertionFailure', 'NSLog',
  'abs', 'min', 'max', 'zip', 'stride', 'sequence', 'repeatElement',
  'swap', 'withUnsafePointer', 'withUnsafeMutablePointer', 'withUnsafeBytes',
  'autoreleasepool', 'unsafeBitCast', 'unsafeDowncast', 'numericCast',
  'type', 'MemoryLayout',
  // Swift collection/string methods (common noise)
  'map', 'flatMap', 'compactMap', 'filter', 'reduce', 'forEach', 'contains',
  'first', 'last', 'prefix', 'suffix', 'dropFirst', 'dropLast',
  'sorted', 'reversed', 'enumerated', 'joined', 'split',
  'append', 'insert', 'remove', 'removeAll', 'removeFirst', 'removeLast',
  'isEmpty', 'count', 'index', 'startIndex', 'endIndex',
  // UIKit/Foundation common methods (noise in call graph)
  'addSubview', 'removeFromSuperview', 'layoutSubviews', 'setNeedsLayout',
  'layoutIfNeeded', 'setNeedsDisplay', 'invalidateIntrinsicContentSize',
  'addTarget', 'removeTarget', 'addGestureRecognizer',
  'addConstraint', 'addConstraints', 'removeConstraint', 'removeConstraints',
  'NSLocalizedString', 'Bundle',
  'reloadData', 'reloadSections', 'reloadRows', 'performBatchUpdates',
  'register', 'dequeueReusableCell', 'dequeueReusableSupplementaryView',
  'beginUpdates', 'endUpdates', 'insertRows', 'deleteRows', 'insertSections', 'deleteSections',
  'present', 'dismiss', 'pushViewController', 'popViewController', 'popToRootViewController',
  'performSegue', 'prepare',
  // GCD / async
  'DispatchQueue', 'async', 'sync', 'asyncAfter',
  'Task', 'withCheckedContinuation', 'withCheckedThrowingContinuation',
  // Combine
  'sink', 'store', 'assign', 'receive', 'subscribe',
  // Notification / KVO
  'addObserver', 'removeObserver', 'post', 'NotificationCenter',
]);

const isBuiltInOrNoise = (name: string): boolean => BUILT_IN_NAMES.has(name);

/**
 * Fast path: resolve pre-extracted call sites from workers.
 * No AST parsing — workers already extracted calledName + sourceId.
 * This function only does symbol table lookups + graph mutations.
 */
export const processCallsFromExtracted = async (
  graph: KnowledgeGraph,
  extractedCalls: ExtractedCall[],
  symbolTable: SymbolTable,
  importMap: ImportMap,
  onProgress?: (current: number, total: number) => void
) => {
  // Group by file for progress reporting
  const byFile = new Map<string, ExtractedCall[]>();
  for (const call of extractedCalls) {
    let list = byFile.get(call.filePath);
    if (!list) {
      list = [];
      byFile.set(call.filePath, list);
    }
    list.push(call);
  }

  const totalFiles = byFile.size;
  let filesProcessed = 0;

  for (const [_filePath, calls] of byFile) {
    filesProcessed++;
    if (filesProcessed % 100 === 0) {
      onProgress?.(filesProcessed, totalFiles);
      await yieldToEventLoop();
    }

    for (const call of calls) {
      const resolved = resolveCallTarget(
        call.calledName,
        call.filePath,
        symbolTable,
        importMap
      );
      if (!resolved) continue;

      const relId = generateId('CALLS', `${call.sourceId}:${call.calledName}->${resolved.nodeId}`);
      graph.addRelationship({
        id: relId,
        sourceId: call.sourceId,
        targetId: resolved.nodeId,
        type: 'CALLS',
        confidence: resolved.confidence,
        reason: resolved.reason,
      });
    }
  }

  onProgress?.(totalFiles, totalFiles);
};

// ============================================================================
// Laravel Route Extraction (procedural AST walk)
// ============================================================================

interface ExtractedRoute {
  filePath: string;
  httpMethod: string;
  routePath: string | null;
  controllerName: string | null;
  methodName: string | null;
  middleware: string[];
  prefix: string | null;
  lineNumber: number;
}

interface RouteGroupContext {
  middleware: string[];
  prefix: string | null;
  controller: string | null;
}

const ROUTE_HTTP_METHODS = new Set([
  'get', 'post', 'put', 'patch', 'delete', 'options', 'any', 'match',
]);

const ROUTE_RESOURCE_METHODS = new Set(['resource', 'apiResource']);

const RESOURCE_ACTIONS = ['index', 'create', 'store', 'show', 'edit', 'update', 'destroy'];
const API_RESOURCE_ACTIONS = ['index', 'store', 'show', 'update', 'destroy'];

function isRouteStaticCall(node: any): boolean {
  if (node.type !== 'scoped_call_expression') return false;
  const obj = node.childForFieldName?.('object') ?? node.children?.[0];
  return obj?.text === 'Route';
}

function getCallMethodName(node: any): string | null {
  const nameNode = node.childForFieldName?.('name') ??
    node.children?.find((c: any) => c.type === 'name');
  return nameNode?.text ?? null;
}

function getArguments(node: any): any {
  return node.children?.find((c: any) => c.type === 'arguments') ?? null;
}

function findClosureBody(argsNode: any): any | null {
  if (!argsNode) return null;
  for (const child of argsNode.children ?? []) {
    if (child.type === 'argument') {
      for (const inner of child.children ?? []) {
        if (inner.type === 'anonymous_function' ||
            inner.type === 'arrow_function') {
          return inner.childForFieldName?.('body') ??
            inner.children?.find((c: any) => c.type === 'compound_statement');
        }
      }
    }
    if (child.type === 'anonymous_function' ||
        child.type === 'arrow_function') {
      return child.childForFieldName?.('body') ??
        child.children?.find((c: any) => c.type === 'compound_statement');
    }
  }
  return null;
}

function findDescendant(node: any, type: string): any {
  if (node.type === type) return node;
  for (const child of (node.children ?? [])) {
    const found = findDescendant(child, type);
    if (found) return found;
  }
  return null;
}

function extractStringContent(node: any): string | null {
  if (!node) return null;
  const content = node.children?.find((c: any) => c.type === 'string_content');
  if (content) return content.text;
  if (node.type === 'string_content') return node.text;
  return null;
}

function extractFirstStringArg(argsNode: any): string | null {
  if (!argsNode) return null;
  for (const child of argsNode.children ?? []) {
    const target = child.type === 'argument' ? child.children?.[0] : child;
    if (!target) continue;
    if (target.type === 'string' || target.type === 'encapsed_string') {
      return extractStringContent(target);
    }
  }
  return null;
}

function extractMiddlewareArg(argsNode: any): string[] {
  if (!argsNode) return [];
  for (const child of argsNode.children ?? []) {
    const target = child.type === 'argument' ? child.children?.[0] : child;
    if (!target) continue;
    if (target.type === 'string' || target.type === 'encapsed_string') {
      const val = extractStringContent(target);
      return val ? [val] : [];
    }
    if (target.type === 'array_creation_expression') {
      const items: string[] = [];
      for (const el of target.children ?? []) {
        if (el.type === 'array_element_initializer') {
          const str = el.children?.find((c: any) => c.type === 'string' || c.type === 'encapsed_string');
          const val = str ? extractStringContent(str) : null;
          if (val) items.push(val);
        }
      }
      return items;
    }
  }
  return [];
}

function extractClassArg(argsNode: any): string | null {
  if (!argsNode) return null;
  for (const child of argsNode.children ?? []) {
    const target = child.type === 'argument' ? child.children?.[0] : child;
    if (target?.type === 'class_constant_access_expression') {
      return target.children?.find((c: any) => c.type === 'name')?.text ?? null;
    }
  }
  return null;
}

function extractControllerTarget(argsNode: any): { controller: string | null; method: string | null } {
  if (!argsNode) return { controller: null, method: null };

  const args: any[] = [];
  for (const child of argsNode.children ?? []) {
    if (child.type === 'argument') args.push(child.children?.[0]);
    else if (child.type !== '(' && child.type !== ')' && child.type !== ',') args.push(child);
  }

  const handlerNode = args[1];
  if (!handlerNode) return { controller: null, method: null };

  if (handlerNode.type === 'array_creation_expression') {
    let controller: string | null = null;
    let method: string | null = null;
    const elements: any[] = [];
    for (const el of handlerNode.children ?? []) {
      if (el.type === 'array_element_initializer') elements.push(el);
    }
    if (elements[0]) {
      const classAccess = findDescendant(elements[0], 'class_constant_access_expression');
      if (classAccess) {
        controller = classAccess.children?.find((c: any) => c.type === 'name')?.text ?? null;
      }
    }
    if (elements[1]) {
      const str = findDescendant(elements[1], 'string');
      method = str ? extractStringContent(str) : null;
    }
    return { controller, method };
  }

  if (handlerNode.type === 'string' || handlerNode.type === 'encapsed_string') {
    const text = extractStringContent(handlerNode);
    if (text?.includes('@')) {
      const [controller, method] = text.split('@');
      return { controller, method };
    }
  }

  if (handlerNode.type === 'class_constant_access_expression') {
    const controller = handlerNode.children?.find((c: any) => c.type === 'name')?.text ?? null;
    return { controller, method: '__invoke' };
  }

  return { controller: null, method: null };
}

interface ChainedRouteCall {
  isRouteFacade: boolean;
  terminalMethod: string;
  attributes: { method: string; argsNode: any }[];
  terminalArgs: any;
  node: any;
}

function unwrapRouteChain(node: any): ChainedRouteCall | null {
  if (node.type !== 'member_call_expression') return null;

  const terminalMethod = getCallMethodName(node);
  if (!terminalMethod) return null;

  const terminalArgs = getArguments(node);
  const attributes: { method: string; argsNode: any }[] = [];

  let current = node.children?.[0];

  while (current) {
    if (current.type === 'member_call_expression') {
      const method = getCallMethodName(current);
      const args = getArguments(current);
      if (method) attributes.unshift({ method, argsNode: args });
      current = current.children?.[0];
    } else if (current.type === 'scoped_call_expression') {
      const obj = current.childForFieldName?.('object') ?? current.children?.[0];
      if (obj?.text !== 'Route') return null;

      const method = getCallMethodName(current);
      const args = getArguments(current);
      if (method) attributes.unshift({ method, argsNode: args });

      return { isRouteFacade: true, terminalMethod, attributes, terminalArgs, node };
    } else {
      break;
    }
  }

  return null;
}

function parseArrayGroupArgs(argsNode: any): RouteGroupContext {
  const ctx: RouteGroupContext = { middleware: [], prefix: null, controller: null };
  if (!argsNode) return ctx;

  for (const child of argsNode.children ?? []) {
    const target = child.type === 'argument' ? child.children?.[0] : child;
    if (target?.type === 'array_creation_expression') {
      for (const el of target.children ?? []) {
        if (el.type !== 'array_element_initializer') continue;
        const children = el.children ?? [];
        const arrowIdx = children.findIndex((c: any) => c.type === '=>');
        if (arrowIdx === -1) continue;
        const key = extractStringContent(children[arrowIdx - 1]);
        const val = children[arrowIdx + 1];
        if (key === 'middleware') {
          if (val?.type === 'string') {
            const s = extractStringContent(val);
            if (s) ctx.middleware.push(s);
          } else if (val?.type === 'array_creation_expression') {
            for (const item of val.children ?? []) {
              if (item.type === 'array_element_initializer') {
                const str = item.children?.find((c: any) => c.type === 'string');
                const s = str ? extractStringContent(str) : null;
                if (s) ctx.middleware.push(s);
              }
            }
          }
        } else if (key === 'prefix') {
          ctx.prefix = extractStringContent(val) ?? null;
        } else if (key === 'controller') {
          if (val?.type === 'class_constant_access_expression') {
            ctx.controller = val.children?.find((c: any) => c.type === 'name')?.text ?? null;
          }
        }
      }
    }
  }
  return ctx;
}

function extractLaravelRoutes(tree: any, filePath: string): ExtractedRoute[] {
  const routes: ExtractedRoute[] = [];

  function resolveStack(stack: RouteGroupContext[]): { middleware: string[]; prefix: string | null; controller: string | null } {
    const middleware: string[] = [];
    let prefix: string | null = null;
    let controller: string | null = null;
    for (const ctx of stack) {
      middleware.push(...ctx.middleware);
      if (ctx.prefix) prefix = prefix ? `${prefix}/${ctx.prefix}`.replace(/\/+/g, '/') : ctx.prefix;
      if (ctx.controller) controller = ctx.controller;
    }
    return { middleware, prefix, controller };
  }

  function emitRoute(
    httpMethod: string,
    argsNode: any,
    lineNumber: number,
    groupStack: RouteGroupContext[],
    chainAttrs: { method: string; argsNode: any }[],
  ) {
    const effective = resolveStack(groupStack);

    for (const attr of chainAttrs) {
      if (attr.method === 'middleware') effective.middleware.push(...extractMiddlewareArg(attr.argsNode));
      if (attr.method === 'prefix') {
        const p = extractFirstStringArg(attr.argsNode);
        if (p) effective.prefix = effective.prefix ? `${effective.prefix}/${p}` : p;
      }
      if (attr.method === 'controller') {
        const cls = extractClassArg(attr.argsNode);
        if (cls) effective.controller = cls;
      }
    }

    const routePath = extractFirstStringArg(argsNode);

    if (ROUTE_RESOURCE_METHODS.has(httpMethod)) {
      const target = extractControllerTarget(argsNode);
      const actions = httpMethod === 'apiResource' ? API_RESOURCE_ACTIONS : RESOURCE_ACTIONS;
      for (const action of actions) {
        routes.push({
          filePath, httpMethod, routePath,
          controllerName: target.controller ?? effective.controller,
          methodName: action,
          middleware: [...effective.middleware],
          prefix: effective.prefix,
          lineNumber,
        });
      }
    } else {
      const target = extractControllerTarget(argsNode);
      routes.push({
        filePath, httpMethod, routePath,
        controllerName: target.controller ?? effective.controller,
        methodName: target.method,
        middleware: [...effective.middleware],
        prefix: effective.prefix,
        lineNumber,
      });
    }
  }

  function walk(node: any, groupStack: RouteGroupContext[]) {
    if (isRouteStaticCall(node)) {
      const method = getCallMethodName(node);
      if (method && (ROUTE_HTTP_METHODS.has(method) || ROUTE_RESOURCE_METHODS.has(method))) {
        emitRoute(method, getArguments(node), node.startPosition.row, groupStack, []);
        return;
      }
      if (method === 'group') {
        const argsNode = getArguments(node);
        const groupCtx = parseArrayGroupArgs(argsNode);
        const closureBody = findClosureBody(argsNode);
        if (closureBody) {
          for (const child of closureBody.children ?? []) {
            walk(child, [...groupStack, groupCtx]);
          }
        }
        return;
      }
    }

    const chain = unwrapRouteChain(node);
    if (chain?.isRouteFacade) {
      if (chain.terminalMethod === 'group') {
        const groupCtx: RouteGroupContext = { middleware: [], prefix: null, controller: null };
        for (const attr of chain.attributes) {
          if (attr.method === 'middleware') groupCtx.middleware.push(...extractMiddlewareArg(attr.argsNode));
          if (attr.method === 'prefix') groupCtx.prefix = extractFirstStringArg(attr.argsNode);
          if (attr.method === 'controller') groupCtx.controller = extractClassArg(attr.argsNode);
        }
        const closureBody = findClosureBody(chain.terminalArgs);
        if (closureBody) {
          for (const child of closureBody.children ?? []) {
            walk(child, [...groupStack, groupCtx]);
          }
        }
        return;
      }
      if (ROUTE_HTTP_METHODS.has(chain.terminalMethod) || ROUTE_RESOURCE_METHODS.has(chain.terminalMethod)) {
        emitRoute(chain.terminalMethod, chain.terminalArgs, chain.node.startPosition.row, groupStack, chain.attributes);
        return;
      }
    }

    for (const child of node.children ?? []) {
      walk(child, groupStack);
    }
  }

  walk(tree.rootNode, []);
  return routes;
}
