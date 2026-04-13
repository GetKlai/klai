import { parentPort } from 'node:worker_threads';
import Parser from 'tree-sitter';
import JavaScript from 'tree-sitter-javascript';
import TypeScript from 'tree-sitter-typescript';
import Python from 'tree-sitter-python';
import Java from 'tree-sitter-java';
import C from 'tree-sitter-c';
import CPP from 'tree-sitter-cpp';
import CSharp from 'tree-sitter-c-sharp';
import Go from 'tree-sitter-go';
import Rust from 'tree-sitter-rust';
import Ruby from 'tree-sitter-ruby';
import PHP from 'tree-sitter-php';
import { createRequire } from 'node:module';
import { SupportedLanguages } from '../../../config/supported-languages.js';

const _require = createRequire(import.meta.url);

// tree-sitter-kotlin is an optionalDependency — may not be installed
let Kotlin: any = null;
try { Kotlin = _require('tree-sitter-kotlin'); } catch {}

// tree-sitter-swift is an optionalDependency — may not be installed
let Swift: any = null;
try { Swift = _require('tree-sitter-swift'); } catch {}
import { LANGUAGE_QUERIES } from '../tree-sitter-queries.js';
import { getLanguageFromFilename, getDefinitionNodeFromCaptures } from '../utils.js';
import { detectFrameworkFromAST } from '../framework-detection.js';
import { generateId } from '../../../lib/utils.js';

// ============================================================================
// Types for serializable results
// ============================================================================

interface ParsedNode {
  id: string;
  label: string;
  properties: {
    name: string;
    filePath: string;
    startLine: number;
    endLine: number;
    language: string;
    isExported: boolean;
    astFrameworkMultiplier?: number;
    astFrameworkReason?: string;
    description?: string;
  };
}

interface ParsedRelationship {
  id: string;
  sourceId: string;
  targetId: string;
  type: 'DEFINES';
  confidence: number;
  reason: string;
}

interface ParsedSymbol {
  filePath: string;
  name: string;
  nodeId: string;
  type: string;
}

export interface ExtractedImport {
  filePath: string;
  rawImportPath: string;
  language: string;
}

export interface ExtractedCall {
  filePath: string;
  calledName: string;
  /** generateId of enclosing function, or generateId('File', filePath) for top-level */
  sourceId: string;
}

export interface ExtractedHeritage {
  filePath: string;
  className: string;
  parentName: string;
  /** 'extends' | 'implements' | 'trait-impl' */
  kind: string;
}

export interface ParseWorkerResult {
  nodes: ParsedNode[];
  relationships: ParsedRelationship[];
  symbols: ParsedSymbol[];
  imports: ExtractedImport[];
  calls: ExtractedCall[];
  heritage: ExtractedHeritage[];
  fileCount: number;
}

export interface ParseWorkerInput {
  path: string;
  content: string;
}

// ============================================================================
// Worker-local parser + language map
// ============================================================================

const parser = new Parser();

const languageMap: Record<string, any> = {
  [SupportedLanguages.JavaScript]: JavaScript,
  [SupportedLanguages.TypeScript]: TypeScript.typescript,
  [`${SupportedLanguages.TypeScript}:tsx`]: TypeScript.tsx,
  [SupportedLanguages.Python]: Python,
  [SupportedLanguages.Java]: Java,
  [SupportedLanguages.C]: C,
  [SupportedLanguages.CPlusPlus]: CPP,
  [SupportedLanguages.CSharp]: CSharp,
  [SupportedLanguages.Go]: Go,
  [SupportedLanguages.Rust]: Rust,
  [SupportedLanguages.PHP]: PHP.php_only,
  [SupportedLanguages.Ruby]: Ruby,
  ...(Kotlin ? { [SupportedLanguages.Kotlin]: Kotlin } : {}),
  ...(Swift ? { [SupportedLanguages.Swift]: Swift } : {}),
};

const setLanguage = (language: SupportedLanguages, filePath: string): void => {
  const key = language === SupportedLanguages.TypeScript && filePath.endsWith('.tsx')
    ? `${language}:tsx`
    : language;
  const lang = languageMap[key];
  if (!lang) throw new Error(`Unsupported language: ${language}`);
  parser.setLanguage(lang);
};

// ============================================================================
// Export detection (copied — needs AST parent traversal, can't cross threads)
// ============================================================================

const isNodeExported = (node: any, name: string, language: string): boolean => {
  let current = node;

  switch (language) {
    case 'javascript':
    case 'typescript':
      while (current) {
        const type = current.type;
        if (type === 'export_statement' ||
            type === 'export_specifier' ||
            type === 'lexical_declaration' && current.parent?.type === 'export_statement') {
          return true;
        }
        if (current.text?.startsWith('export ')) {
          return true;
        }
        current = current.parent;
      }
      return false;

    case 'python':
      return !name.startsWith('_');

    case 'java':
      while (current) {
        if (current.parent) {
          const parent = current.parent;
          for (let i = 0; i < parent.childCount; i++) {
            const child = parent.child(i);
            if (child?.type === 'modifiers' && child.text?.includes('public')) {
              return true;
            }
          }
          if (parent.type === 'method_declaration' || parent.type === 'constructor_declaration') {
            if (parent.text?.trimStart().startsWith('public')) {
              return true;
            }
          }
        }
        current = current.parent;
      }
      return false;

    case 'csharp':
      while (current) {
        if (current.type === 'modifier' || current.type === 'modifiers') {
          if (current.text?.includes('public')) return true;
        }
        current = current.parent;
      }
      return false;

    case 'go':
      if (name.length === 0) return false;
      const first = name[0];
      return first === first.toUpperCase() && first !== first.toLowerCase();

    case 'rust':
      while (current) {
        if (current.type === 'visibility_modifier') {
          if (current.text?.includes('pub')) return true;
        }
        current = current.parent;
      }
      return false;

    case 'kotlin':
      // Kotlin: default visibility is public (unlike Java).
      // visibility_modifier is inside modifiers, a sibling of the name node within the declaration.
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

    case 'c':
    case 'cpp':
      return false;

    case 'swift':
      while (current) {
        if (current.type === 'modifiers' || current.type === 'visibility_modifier') {
          const text = current.text || '';
          if (text.includes('public') || text.includes('open')) return true;
        }
        current = current.parent;
      }
      return false;

    case 'php':
      // Top-level classes/interfaces/traits are always accessible
      // Methods/properties are exported only if they have 'public' modifier
      while (current) {
        if (current.type === 'class_declaration' ||
            current.type === 'interface_declaration' ||
            current.type === 'trait_declaration' ||
            current.type === 'enum_declaration') {
          return true;
        }
        if (current.type === 'visibility_modifier') {
          return current.text === 'public';
        }
        current = current.parent;
      }
      // Top-level functions (no parent class) are globally accessible
      return true;

    // Ruby: Public if no leading underscore (convention, like Python)
    case 'ruby':
      return !name.startsWith('_');

    default:
      return false;
  }
};

// ============================================================================
// Enclosing function detection (for call extraction)
// ============================================================================

const FUNCTION_NODE_TYPES = new Set([
  'function_declaration', 'arrow_function', 'function_expression',
  'method_definition', 'generator_function_declaration',
  'function_definition', 'async_function_declaration', 'async_arrow_function',
  'method_declaration', 'constructor_declaration',
  'local_function_statement', 'function_item', 'impl_item',
  'anonymous_function_creation_expression',  // PHP anonymous functions
  'lambda_literal',                          // Kotlin lambdas
  'init_declaration', 'deinit_declaration',  // Swift initializers/deinitializers
  'method',           // Ruby def foo
  'singleton_method', // Ruby def self.foo
]);

/** Walk up AST to find enclosing function, return its generateId or null for top-level */
const findEnclosingFunctionId = (node: any, filePath: string): string | null => {
  let current = node.parent;
  while (current) {
    if (FUNCTION_NODE_TYPES.has(current.type)) {
      let funcName: string | null = null;
      let label = 'Function';

      if (current.type === 'init_declaration' || current.type === 'deinit_declaration') {
        const funcName = current.type === 'init_declaration' ? 'init' : 'deinit';
        const label = 'Constructor';
        const startLine = current.startPosition?.row ?? 0;
        return generateId(label, `${filePath}:${funcName}:${startLine}`);
      }

      if (['function_declaration', 'function_definition', 'async_function_declaration',
           'generator_function_declaration', 'function_item'].includes(current.type)) {
        const nameNode = current.childForFieldName?.('name') ||
          current.children?.find((c: any) => c.type === 'identifier' || c.type === 'property_identifier');
        funcName = nameNode?.text;
      } else if (current.type === 'impl_item') {
        const funcItem = current.children?.find((c: any) => c.type === 'function_item');
        if (funcItem) {
          const nameNode = funcItem.childForFieldName?.('name') ||
            funcItem.children?.find((c: any) => c.type === 'identifier');
          funcName = nameNode?.text;
          label = 'Method';
        }
      } else if (current.type === 'method_definition') {
        const nameNode = current.childForFieldName?.('name') ||
          current.children?.find((c: any) => c.type === 'property_identifier');
        funcName = nameNode?.text;
        label = 'Method';
      } else if (current.type === 'method_declaration' || current.type === 'constructor_declaration') {
        const nameNode = current.childForFieldName?.('name') ||
          current.children?.find((c: any) => c.type === 'identifier');
        funcName = nameNode?.text;
        label = 'Method';
      } else if (current.type === 'method' || current.type === 'singleton_method') {
        // Ruby method/singleton_method: name is an identifier child
        let nameNode = current.childForFieldName?.('name');
        if (!nameNode) {
          for (let i = 0; i < (current.childCount ?? 0); i++) {
            const c = current.child?.(i);
            if (c?.type === 'identifier') { nameNode = c; break; }
          }
        }
        funcName = nameNode?.text;
        label = 'Method';
      } else if (current.type === 'arrow_function' || current.type === 'function_expression') {
        const parent = current.parent;
        if (parent?.type === 'variable_declarator') {
          const nameNode = parent.childForFieldName?.('name') ||
            parent.children?.find((c: any) => c.type === 'identifier');
          funcName = nameNode?.text;
        }
      }

      if (funcName) {
        const startLine = current.startPosition?.row ?? 0;
        return generateId(label, `${filePath}:${funcName}:${startLine}`);
      }
    }
    current = current.parent;
  }
  return null;
};

const BUILT_INS = new Set([
  // JavaScript/TypeScript
  'console', 'log', 'warn', 'error', 'info', 'debug',
  'setTimeout', 'setInterval', 'clearTimeout', 'clearInterval',
  'parseInt', 'parseFloat', 'isNaN', 'isFinite',
  'encodeURI', 'decodeURI', 'encodeURIComponent', 'decodeURIComponent',
  'JSON', 'parse', 'stringify',
  'Object', 'Array', 'String', 'Number', 'Boolean', 'Symbol', 'BigInt',
  'Map', 'Set', 'WeakMap', 'WeakSet',
  'Promise', 'resolve', 'reject', 'then', 'catch', 'finally',
  'Math', 'Date', 'RegExp', 'Error',
  'require', 'import', 'export', 'fetch', 'Response', 'Request',
  'useState', 'useEffect', 'useCallback', 'useMemo', 'useRef', 'useContext',
  'useReducer', 'useLayoutEffect', 'useImperativeHandle', 'useDebugValue',
  'createElement', 'createContext', 'createRef', 'forwardRef', 'memo', 'lazy',
  'map', 'filter', 'reduce', 'forEach', 'find', 'findIndex', 'some', 'every',
  'includes', 'indexOf', 'slice', 'splice', 'concat', 'join', 'split',
  'push', 'pop', 'shift', 'unshift', 'sort', 'reverse',
  'keys', 'values', 'entries', 'assign', 'freeze', 'seal',
  'hasOwnProperty', 'toString', 'valueOf',
  // Python
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
  // C/C++ standard library
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
  // PHP built-ins
  'echo', 'isset', 'empty', 'unset', 'list', 'array', 'compact', 'extract',
  'count', 'strlen', 'strpos', 'strrpos', 'substr', 'strtolower', 'strtoupper', 'trim',
  'ltrim', 'rtrim', 'str_replace', 'str_contains', 'str_starts_with', 'str_ends_with',
  'sprintf', 'vsprintf', 'printf', 'number_format',
  'array_map', 'array_filter', 'array_reduce', 'array_push', 'array_pop', 'array_shift',
  'array_unshift', 'array_slice', 'array_splice', 'array_merge', 'array_keys', 'array_values',
  'array_key_exists', 'in_array', 'array_search', 'array_unique', 'usort', 'rsort',
  'json_encode', 'json_decode', 'serialize', 'unserialize',
  'intval', 'floatval', 'strval', 'boolval', 'is_null', 'is_string', 'is_int', 'is_array',
  'is_object', 'is_numeric', 'is_bool', 'is_float',
  'var_dump', 'print_r', 'var_export',
  'date', 'time', 'strtotime', 'mktime', 'microtime',
  'file_exists', 'file_get_contents', 'file_put_contents', 'is_file', 'is_dir',
  'preg_match', 'preg_match_all', 'preg_replace', 'preg_split',
  'header', 'session_start', 'session_destroy', 'ob_start', 'ob_end_clean', 'ob_get_clean',
  'dd', 'dump',
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

// ============================================================================
// Label detection from capture map
// ============================================================================

const getLabelFromCaptures = (captureMap: Record<string, any>): string | null => {
  // Skip imports (handled separately) and calls
  if (captureMap['import'] || captureMap['call']) return null;
  if (!captureMap['name']) return null;

  if (captureMap['definition.function']) return 'Function';
  if (captureMap['definition.class']) return 'Class';
  if (captureMap['definition.interface']) return 'Interface';
  if (captureMap['definition.method']) return 'Method';
  if (captureMap['definition.struct']) return 'Struct';
  if (captureMap['definition.enum']) return 'Enum';
  if (captureMap['definition.namespace']) return 'Namespace';
  if (captureMap['definition.module']) return 'Module';
  if (captureMap['definition.trait']) return 'Trait';
  if (captureMap['definition.impl']) return 'Impl';
  if (captureMap['definition.type']) return 'TypeAlias';
  if (captureMap['definition.const']) return 'Const';
  if (captureMap['definition.static']) return 'Static';
  if (captureMap['definition.typedef']) return 'Typedef';
  if (captureMap['definition.macro']) return 'Macro';
  if (captureMap['definition.union']) return 'Union';
  if (captureMap['definition.property']) return 'Property';
  if (captureMap['definition.record']) return 'Record';
  if (captureMap['definition.delegate']) return 'Delegate';
  if (captureMap['definition.annotation']) return 'Annotation';
  if (captureMap['definition.constructor']) return 'Constructor';
  if (captureMap['definition.template']) return 'Template';
  return 'CodeElement';
};

// ============================================================================
// Process a batch of files
// ============================================================================

const processBatch = (files: ParseWorkerInput[], onProgress?: (filesProcessed: number) => void): ParseWorkerResult => {
  const result: ParseWorkerResult = {
    nodes: [],
    relationships: [],
    symbols: [],
    imports: [],
    calls: [],
    heritage: [],
    fileCount: 0,
  };

  // Group by language to minimize setLanguage calls
  const byLanguage = new Map<SupportedLanguages, ParseWorkerInput[]>();
  for (const file of files) {
    const lang = getLanguageFromFilename(file.path);
    if (!lang) continue;
    let list = byLanguage.get(lang);
    if (!list) {
      list = [];
      byLanguage.set(lang, list);
    }
    list.push(file);
  }

  let totalProcessed = 0;
  let lastReported = 0;
  const PROGRESS_INTERVAL = 100; // report every 100 files

  const onFileProcessed = onProgress ? () => {
    totalProcessed++;
    if (totalProcessed - lastReported >= PROGRESS_INTERVAL) {
      lastReported = totalProcessed;
      onProgress(totalProcessed);
    }
  } : undefined;

  for (const [language, langFiles] of byLanguage) {
    const queryString = LANGUAGE_QUERIES[language];
    if (!queryString) continue;

    // Track if we need to handle tsx separately
    const tsxFiles: ParseWorkerInput[] = [];
    const regularFiles: ParseWorkerInput[] = [];

    if (language === SupportedLanguages.TypeScript) {
      for (const f of langFiles) {
        if (f.path.endsWith('.tsx')) {
          tsxFiles.push(f);
        } else {
          regularFiles.push(f);
        }
      }
    } else {
      regularFiles.push(...langFiles);
    }

    // Process regular files for this language
    if (regularFiles.length > 0) {
      try {
        setLanguage(language, regularFiles[0].path);
        processFileGroup(regularFiles, language, queryString, result, onFileProcessed);
      } catch {
        // parser unavailable — skip this language group
      }
    }

    // Process tsx files separately (different grammar)
    if (tsxFiles.length > 0) {
      try {
        setLanguage(language, tsxFiles[0].path);
        processFileGroup(tsxFiles, language, queryString, result, onFileProcessed);
      } catch {
        // parser unavailable — skip this language group
      }
    }
  }

  return result;
};

// ============================================================================
// PHP Eloquent metadata extraction
// ============================================================================

/** Eloquent model properties whose array values are worth indexing */
const ELOQUENT_ARRAY_PROPS = new Set(['fillable', 'casts', 'hidden', 'guarded', 'with', 'appends']);

/** Eloquent relationship method names */
const ELOQUENT_RELATIONS = new Set([
  'hasMany', 'hasOne', 'belongsTo', 'belongsToMany',
  'morphTo', 'morphMany', 'morphOne', 'morphToMany', 'morphedByMany',
  'hasManyThrough', 'hasOneThrough',
]);

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

/**
 * For a PHP property_declaration node, extract array values as a description string.
 * Returns null if not an Eloquent model property or no array values found.
 */
function extractPhpPropertyDescription(propName: string, propDeclNode: any): string | null {
  if (!ELOQUENT_ARRAY_PROPS.has(propName)) return null;

  const arrayNode = findDescendant(propDeclNode, 'array_creation_expression');
  if (!arrayNode) return null;

  const items: string[] = [];
  for (const child of (arrayNode.children ?? [])) {
    if (child.type !== 'array_element_initializer') continue;
    const children = child.children ?? [];
    const arrowIdx = children.findIndex((c: any) => c.type === '=>');
    if (arrowIdx !== -1) {
      // key => value pair (used in $casts)
      const key = extractStringContent(children[arrowIdx - 1]);
      const val = extractStringContent(children[arrowIdx + 1]);
      if (key && val) items.push(`${key}:${val}`);
    } else {
      // Simple value (used in $fillable, $hidden, etc.)
      const val = extractStringContent(children[0]);
      if (val) items.push(val);
    }
  }

  return items.length > 0 ? items.join(', ') : null;
}

/**
 * For a PHP method_declaration node, detect if it defines an Eloquent relationship.
 * Returns description like "hasMany(Post)" or null.
 */
function extractEloquentRelationDescription(methodNode: any): string | null {
  function findRelationCall(node: any): any {
    if (node.type === 'member_call_expression') {
      const children = node.children ?? [];
      const objectNode = children.find((c: any) => c.type === 'variable_name' && c.text === '$this');
      const nameNode = children.find((c: any) => c.type === 'name');
      if (objectNode && nameNode && ELOQUENT_RELATIONS.has(nameNode.text)) return node;
    }
    for (const child of (node.children ?? [])) {
      const found = findRelationCall(child);
      if (found) return found;
    }
    return null;
  }

  const callNode = findRelationCall(methodNode);
  if (!callNode) return null;

  const relType = callNode.children?.find((c: any) => c.type === 'name')?.text;
  const argsNode = callNode.children?.find((c: any) => c.type === 'arguments');
  let targetModel: string | null = null;
  if (argsNode) {
    const firstArg = argsNode.children?.find((c: any) => c.type === 'argument');
    if (firstArg) {
      const classConstant = firstArg.children?.find((c: any) =>
        c.type === 'class_constant_access_expression'
      );
      if (classConstant) {
        targetModel = classConstant.children?.find((c: any) => c.type === 'name')?.text ?? null;
      }
    }
  }

  if (relType && targetModel) return `${relType}(${targetModel})`;
  if (relType) return relType;
  return null;
}

const processFileGroup = (
  files: ParseWorkerInput[],
  language: SupportedLanguages,
  queryString: string,
  result: ParseWorkerResult,
  onFileProcessed?: () => void,
): void => {
  let query: any;
  try {
    const lang = parser.getLanguage();
    query = new Parser.Query(lang, queryString);
  } catch {
    return;
  }

  for (const file of files) {
    // Skip very large files — they can crash tree-sitter or cause OOM
    if (file.content.length > 512 * 1024) continue;

    let tree;
    try {
      tree = parser.parse(file.content, undefined, { bufferSize: 1024 * 256 });
    } catch {
      continue;
    }

    result.fileCount++;
    onFileProcessed?.();

    let matches;
    try {
      matches = query.matches(tree.rootNode);
    } catch {
      continue;
    }

    for (const match of matches) {
      const captureMap: Record<string, any> = {};
      for (const c of match.captures) {
        captureMap[c.name] = c.node;
      }

      // Extract import paths before skipping
      if (captureMap['import'] && captureMap['import.source']) {
        let rawImportPath = captureMap['import.source'].text.replace(/['"<>]/g, '');
        // Kotlin: detect wildcard imports (import com.example.*)
        // The tree-sitter grammar captures the identifier without the .* suffix,
        // so we check for a ".*" child node in the import node.
        if (language === SupportedLanguages.Kotlin && !rawImportPath.endsWith('.*')) {
          const importNode = captureMap['import'];
          for (let ci = 0; ci < importNode.childCount; ci++) {
            const child = importNode.child(ci);
            if (child && child.type === '.*') {
              rawImportPath += '.*';
              break;
            }
          }
        }
        result.imports.push({
          filePath: file.path,
          rawImportPath,
          language: language,
        });
        continue;
      }

      // Extract call sites
      if (captureMap['call']) {
        const callNameNode = captureMap['call.name'];
        if (callNameNode) {
          const calledName = callNameNode.text;

          // Ruby: route require/require_relative as imports, not calls
          if (language === SupportedLanguages.Ruby &&
              (calledName === 'require' || calledName === 'require_relative')) {
            const callNode = captureMap['call'];
            const argList = callNode.childForFieldName?.('arguments');
            const stringNode = argList?.children?.find((c: any) => c.type === 'string');
            const contentNode = stringNode?.children?.find((c: any) => c.type === 'string_content');
            if (contentNode) {
              let importPath: string = contentNode.text;
              if (importPath && importPath.length <= 1024 && !/[\x00-\x1f]/.test(importPath)) {
                if (calledName === 'require_relative' && !importPath.startsWith('.')) {
                  importPath = './' + importPath;
                }
                result.imports.push({
                  filePath: file.path,
                  rawImportPath: importPath,
                  language: language,
                });
              }
            }
            continue;
          }

          // Ruby: route include/extend/prepend as heritage (mixin), not calls
          if (language === SupportedLanguages.Ruby &&
              (calledName === 'include' || calledName === 'extend' || calledName === 'prepend')) {
            const callNode = captureMap['call'];
            let enclosingClass: string | null = null;
            let current = callNode.parent;
            let depth = 0;
            while (current && ++depth <= 50) {
              if (current.type === 'class' || current.type === 'module') {
                const nameNode = current.childForFieldName?.('name');
                if (nameNode) { enclosingClass = nameNode.text; break; }
              }
              current = current.parent;
            }
            if (enclosingClass) {
              const argList = callNode.childForFieldName?.('arguments');
              for (const arg of (argList?.children ?? [])) {
                if (arg.type === 'constant' || arg.type === 'scope_resolution') {
                  result.heritage.push({
                    filePath: file.path,
                    className: enclosingClass,
                    parentName: arg.text,
                    kind: calledName,
                  });
                }
              }
            }
            continue;
          }

          if (!BUILT_INS.has(calledName)) {
            const callNode = captureMap['call'];
            const sourceId = findEnclosingFunctionId(callNode, file.path)
              || generateId('File', file.path);
            result.calls.push({ filePath: file.path, calledName, sourceId });
          }
        }
        continue;
      }

      // Extract heritage (extends/implements)
      if (captureMap['heritage.class']) {
        if (captureMap['heritage.extends']) {
          result.heritage.push({
            filePath: file.path,
            className: captureMap['heritage.class'].text,
            parentName: captureMap['heritage.extends'].text,
            kind: 'extends',
          });
        }
        if (captureMap['heritage.implements']) {
          result.heritage.push({
            filePath: file.path,
            className: captureMap['heritage.class'].text,
            parentName: captureMap['heritage.implements'].text,
            kind: 'implements',
          });
        }
        if (captureMap['heritage.trait']) {
          result.heritage.push({
            filePath: file.path,
            className: captureMap['heritage.class'].text,
            parentName: captureMap['heritage.trait'].text,
            kind: 'trait-impl',
          });
        }
        if (captureMap['heritage.extends'] || captureMap['heritage.implements'] || captureMap['heritage.trait']) {
          continue;
        }
      }

      const nodeLabel = getLabelFromCaptures(captureMap);
      if (!nodeLabel) continue;

      const nameNode = captureMap['name'];
      const nodeName = nameNode.text;
      const nodeId = generateId(nodeLabel, `${file.path}:${nodeName}`);

      let description: string | undefined;
      if (language === SupportedLanguages.PHP) {
        if (nodeLabel === 'Property' && captureMap['definition.property']) {
          description = extractPhpPropertyDescription(nodeName, captureMap['definition.property']) ?? undefined;
        } else if (nodeLabel === 'Method' && captureMap['definition.method']) {
          description = extractEloquentRelationDescription(captureMap['definition.method']) ?? undefined;
        }
      }

      const definitionNode = getDefinitionNodeFromCaptures(captureMap);
      const frameworkHint = definitionNode
        ? detectFrameworkFromAST(language, definitionNode.text || '')
        : null;

      result.nodes.push({
        id: nodeId,
        label: nodeLabel,
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
          ...(description !== undefined ? { description } : {}),
        },
      });

      result.symbols.push({
        filePath: file.path,
        name: nodeName,
        nodeId,
        type: nodeLabel,
      });

      const fileId = generateId('File', file.path);
      const relId = generateId('DEFINES', `${fileId}->${nodeId}`);
      result.relationships.push({
        id: relId,
        sourceId: fileId,
        targetId: nodeId,
        type: 'DEFINES',
        confidence: 1.0,
        reason: '',
      });
    }
  }
};

// ============================================================================
// Worker message handler — supports sub-batch streaming
// ============================================================================

/** Accumulated result across sub-batches */
let accumulated: ParseWorkerResult = {
  nodes: [], relationships: [], symbols: [],
  imports: [], calls: [], heritage: [], fileCount: 0,
};
let cumulativeProcessed = 0;

const mergeResult = (target: ParseWorkerResult, src: ParseWorkerResult) => {
  target.nodes.push(...src.nodes);
  target.relationships.push(...src.relationships);
  target.symbols.push(...src.symbols);
  target.imports.push(...src.imports);
  target.calls.push(...src.calls);
  target.heritage.push(...src.heritage);
  target.fileCount += src.fileCount;
};

parentPort!.on('message', (msg: any) => {
  try {
    // Sub-batch mode: { type: 'sub-batch', files: [...] }
    if (msg && msg.type === 'sub-batch') {
      const result = processBatch(msg.files, (filesProcessed) => {
        parentPort!.postMessage({ type: 'progress', filesProcessed: cumulativeProcessed + filesProcessed });
      });
      cumulativeProcessed += result.fileCount;
      mergeResult(accumulated, result);
      // Signal ready for next sub-batch
      parentPort!.postMessage({ type: 'sub-batch-done' });
      return;
    }

    // Flush: send accumulated results
    if (msg && msg.type === 'flush') {
      parentPort!.postMessage({ type: 'result', data: accumulated });
      // Reset for potential reuse
      accumulated = { nodes: [], relationships: [], symbols: [], imports: [], calls: [], heritage: [], fileCount: 0 };
      cumulativeProcessed = 0;
      return;
    }

    // Legacy single-message mode (backward compat): array of files
    if (Array.isArray(msg)) {
      const result = processBatch(msg, (filesProcessed) => {
        parentPort!.postMessage({ type: 'progress', filesProcessed });
      });
      parentPort!.postMessage({ type: 'result', data: result });
      return;
    }
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    parentPort!.postMessage({ type: 'error', error: message });
  }
});
