import type { SyntaxNode } from '../utils.js';

/** Extracts type bindings from a declaration node into the env map */
export type TypeBindingExtractor = (node: SyntaxNode, env: Map<string, string>) => void;

/** Extracts type bindings from a parameter node into the env map */
export type ParameterExtractor = (node: SyntaxNode, env: Map<string, string>) => void;

/** Minimal interface for checking whether a name is a known class/struct.
 *  Narrower than ReadonlySet — only `.has()` is used by extractors. */
export type ClassNameLookup = { has(name: string): boolean };

/** Extracts type bindings from a constructor-call initializer, with access to known class names */
export type InitializerExtractor = (node: SyntaxNode, env: Map<string, string>, classNames: ClassNameLookup) => void;

/** Scans an AST node for untyped `var = callee()` patterns for return-type inference.
 *  Returns { varName, calleeName } if the node matches, undefined otherwise.
 *  `receiverClassName` — optional hint for method calls on known receivers
 *  (e.g. $this->getUser() in PHP provides the enclosing class name). */
export type ConstructorBindingScanner = (node: SyntaxNode) => { varName: string; calleeName: string; receiverClassName?: string } | undefined;

/** Extracts a return type string from a method/function definition node.
 *  Used for languages where return types are expressed in comments (e.g. YARD @return [Type])
 *  rather than in AST fields. Returns undefined if no return type can be determined. */
export type ReturnTypeExtractor = (node: SyntaxNode) => string | undefined;

/** Narrow lookup interface for resolving a callee name → return type name.
 *  Backed by SymbolTable.lookupFuzzyCallable; passed via ForLoopExtractorContext.
 *  Conservative: returns undefined when the callee is ambiguous (0 or 2+ matches). */
export interface ReturnTypeLookup {
  /** Processed type name after stripping wrappers (e.g., 'User' from 'Promise<User>').
   *  Use for call-result variable bindings (`const b = foo()`). */
  lookupReturnType(callee: string): string | undefined;
  /** Raw return type as declared in the symbol (e.g., '[]User', 'List<User>').
   *  Use for iterable-element extraction (`for v := range foo()`). */
  lookupRawReturnType(callee: string): string | undefined;
}

/** Context object passed to ForLoopExtractor.
 *  Groups the four parameters that were previously positional. */
export interface ForLoopExtractorContext {
  /** Mutable type-env for the current scope — extractor writes bindings here */
  scopeEnv: Map<string, string>;
  /** Maps `scope\0varName` to the declaration's type annotation AST node */
  declarationTypeNodes: ReadonlyMap<string, SyntaxNode>;
  /** Current scope key, e.g. `"process@42"` */
  scope: string;
  /** Resolves a callee name to its declared return type (undefined = unknown/ambiguous) */
  returnTypeLookup: ReturnTypeLookup;
}

/** Extracts loop variable type binding from a for-each statement. */
export type ForLoopExtractor = (node: SyntaxNode, ctx: ForLoopExtractorContext) => void;

/** Discriminated union for pending Tier-2 propagation items.
 *  - `copy`       — `const b = a` (identifier alias, propagate a's type to b)
 *  - `callResult` — `const b = foo()` (bind b to foo's declared return type) */
export type PendingAssignment =
  | { kind: 'copy'; lhs: string; rhs: string }
  | { kind: 'callResult'; lhs: string; callee: string };

/** Extracts a pending assignment for Tier 2 propagation.
 *  Returns a PendingAssignment when the RHS is a bare identifier (`copy`) or a
 *  call expression (`callResult`) and the LHS has no resolved type yet.
 *  Returns undefined if the node is not a matching assignment. */
export type PendingAssignmentExtractor = (
  node: SyntaxNode,
  scopeEnv: ReadonlyMap<string, string>,
) => PendingAssignment | undefined;

/** Extracts a typed variable binding from a pattern-matching construct.
 *  Returns { varName, typeName } for patterns that introduce NEW variables.
 *  Examples: `if let Some(user) = opt` (Rust), `x instanceof User user` (Java).
 *  Conservative: returns undefined when the source variable's type is unknown.
 *
 *  @param scopeEnv   Read-only view of already-resolved type bindings in the current scope.
 *  @param declarationTypeNodes  Maps `scope\0varName` to the original declaration's type
 *    annotation AST node. Allows extracting generic type arguments (e.g., T from Result<T,E>)
 *    that are stripped during normal TypeEnv extraction.
 *  @param scope  Current scope key (e.g. `"process@42"`) for declarationTypeNodes lookups. */
export type PatternBindingExtractor = (
  node: SyntaxNode,
  scopeEnv: ReadonlyMap<string, string>,
  declarationTypeNodes: ReadonlyMap<string, SyntaxNode>,
  scope: string,
) => { varName: string; typeName: string } | undefined;

/** Per-language type extraction configuration */
export interface LanguageTypeConfig {
  /** Allow pattern binding to overwrite existing scopeEnv entries.
   *  WARNING: Enables function-scope type pollution. Only for languages with
   *  smart-cast semantics (e.g., Kotlin `when/is`) where the subject variable
   *  already exists in scopeEnv from its declaration. */
  readonly allowPatternBindingOverwrite?: boolean;
  /** Node types that represent typed declarations for this language */
  declarationNodeTypes: ReadonlySet<string>;
  /** AST node types for for-each/for-in statements with explicit element types. */
  forLoopNodeTypes?: ReadonlySet<string>;
  /** Optional allowlist of AST node types on which extractPatternBinding should run.
   *  When present, extractPatternBinding is only invoked for nodes whose type is in this set,
   *  short-circuiting the call for all other node types. When absent, every node is passed to
   *  extractPatternBinding (legacy behaviour). */
  patternBindingNodeTypes?: ReadonlySet<string>;
  /** Extract a (varName → typeName) binding from a declaration node */
  extractDeclaration: TypeBindingExtractor;
  /** Extract a (varName → typeName) binding from a parameter node */
  extractParameter: ParameterExtractor;
  /** Extract a (varName → typeName) binding from a constructor-call initializer.
   *  Called as fallback when extractDeclaration produces no binding for a declaration node.
   *  Only for languages with syntactic constructor markers (new, composite_literal, ::new).
   *  Receives classNames — the set of class/struct names visible in the current file's AST. */
  extractInitializer?: InitializerExtractor;
  /** Scan for untyped `var = callee()` assignments for return-type inference.
   *  Called on every AST node during buildTypeEnv walk; returns undefined for non-matches.
   *  The callee binding is unverified — the caller must confirm against the SymbolTable. */
  scanConstructorBinding?: ConstructorBindingScanner;
  /** Extract return type from comment-based annotations (e.g. YARD @return [Type]).
   *  Called as fallback when extractMethodSignature finds no AST-based return type. */
  extractReturnType?: ReturnTypeExtractor;
  /** Extract loop variable → type binding from a for-each AST node. */
  extractForLoopBinding?: ForLoopExtractor;
  /** Extract pending assignment for Tier 2 propagation.
   *  Called on declaration/assignment nodes; returns a PendingAssignment when the RHS
   *  is a bare identifier (copy) or call expression (callResult) and the LHS has no
   *  resolved type yet. Language-specific because AST shapes differ widely. */
  extractPendingAssignment?: PendingAssignmentExtractor;
  /** Extract a typed variable binding from a pattern-matching construct.
   *  Called on every AST node; returns { varName, typeName } when the node introduces a new
   *  typed variable via pattern matching (e.g. `if let Some(x) = opt`, `x instanceof T t`).
   *  The extractor receives the current scope's resolved bindings (read-only) to look up the
   *  source variable's type. Returns undefined for non-matching nodes or unknown source types. */
  extractPatternBinding?: PatternBindingExtractor;
}
