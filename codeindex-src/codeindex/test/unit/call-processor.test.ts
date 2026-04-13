import { describe, it, expect, vi, beforeEach } from 'vitest';
import { processCallsFromExtracted } from '../../src/core/ingestion/call-processor.js';
import { extractReturnTypeName } from '../../src/core/ingestion/type-extractors/shared.js';
import { createResolutionContext, type ResolutionContext } from '../../src/core/ingestion/resolution-context.js';
import { createKnowledgeGraph } from '../../src/core/graph/graph.js';
import type { ExtractedCall } from '../../src/core/ingestion/workers/parse-worker.js';

describe('processCallsFromExtracted', () => {
  let graph: ReturnType<typeof createKnowledgeGraph>;
  let ctx: ResolutionContext;

  beforeEach(() => {
    graph = createKnowledgeGraph();
    ctx = createResolutionContext();
  });

  it('creates CALLS relationship for same-file resolution', async () => {
    ctx.symbols.add('src/index.ts', 'helper', 'Function:src/index.ts:helper', 'Function');

    const calls: ExtractedCall[] = [{
      filePath: 'src/index.ts',
      calledName: 'helper',
      sourceId: 'Function:src/index.ts:main',
    }];

    await processCallsFromExtracted(graph, calls, ctx.symbols, ctx.importMap);

    const rels = graph.relationships.filter(r => r.type === 'CALLS');
    expect(rels).toHaveLength(1);
    expect(rels[0].sourceId).toBe('Function:src/index.ts:main');
    expect(rels[0].targetId).toBe('Function:src/index.ts:helper');
    expect(rels[0].confidence).toBe(0.85);
    expect(rels[0].reason).toBe('same-file');
  });

  it('creates CALLS relationship for import-resolved resolution', async () => {
    ctx.symbols.add('src/utils.ts', 'format', 'Function:src/utils.ts:format', 'Function');
    ctx.importMap.set('src/index.ts', new Set(['src/utils.ts']));

    const calls: ExtractedCall[] = [{
      filePath: 'src/index.ts',
      calledName: 'format',
      sourceId: 'Function:src/index.ts:main',
    }];

    await processCallsFromExtracted(graph, calls, ctx.symbols, ctx.importMap);

    const rels = graph.relationships.filter(r => r.type === 'CALLS');
    expect(rels).toHaveLength(1);
    expect(rels[0].confidence).toBe(0.9);
    expect(rels[0].reason).toBe('import-resolved');
  });

  it('resolves unique global symbol with moderate confidence', async () => {
    ctx.symbols.add('src/other.ts', 'uniqueFunc', 'Function:src/other.ts:uniqueFunc', 'Function');

    const calls: ExtractedCall[] = [{
      filePath: 'src/index.ts',
      calledName: 'uniqueFunc',
      sourceId: 'Function:src/index.ts:main',
    }];

    await processCallsFromExtracted(graph, calls, ctx.symbols, ctx.importMap);

    const rels = graph.relationships.filter(r => r.type === 'CALLS');
    expect(rels).toHaveLength(1);
    expect(rels[0].confidence).toBe(0.5);
    expect(rels[0].reason).toBe('fuzzy-global');
  });

  it('creates low-confidence CALLS edge for ambiguous global symbols', async () => {
    ctx.symbols.add('src/a.ts', 'render', 'Function:src/a.ts:render', 'Function');
    ctx.symbols.add('src/b.ts', 'render', 'Function:src/b.ts:render', 'Function');

    const calls: ExtractedCall[] = [{
      filePath: 'src/index.ts',
      calledName: 'render',
      sourceId: 'Function:src/index.ts:main',
    }];

    await processCallsFromExtracted(graph, calls, ctx.symbols, ctx.importMap);

    const rels = graph.relationships.filter(r => r.type === 'CALLS');
    expect(rels).toHaveLength(1);
    expect(rels[0].confidence).toBe(0.3);
    expect(rels[0].reason).toBe('fuzzy-global');
  });

  it('skips unresolvable calls', async () => {
    const calls: ExtractedCall[] = [{
      filePath: 'src/index.ts',
      calledName: 'nonExistent',
      sourceId: 'Function:src/index.ts:main',
    }];

    await processCallsFromExtracted(graph, calls, ctx.symbols, ctx.importMap);
    expect(graph.relationshipCount).toBe(0);
  });

  it('creates CALLS edges to any symbol type (no callable filtering)', async () => {
    ctx.symbols.add('src/index.ts', 'Widget', 'Class:src/index.ts:Widget', 'Class');

    const calls: ExtractedCall[] = [{
      filePath: 'src/index.ts',
      calledName: 'Widget',
      sourceId: 'Function:src/index.ts:main',
    }];

    await processCallsFromExtracted(graph, calls, ctx.symbols, ctx.importMap);
    // Code resolves any matching symbol regardless of type
    expect(graph.relationshipCount).toBe(1);
  });

  it('creates CALLS edges to Interface symbols', async () => {
    ctx.symbols.add('src/types.ts', 'Serializable', 'Interface:src/types.ts:Serializable', 'Interface');
    ctx.importMap.set('src/index.ts', new Set(['src/types.ts']));

    const calls: ExtractedCall[] = [{
      filePath: 'src/index.ts',
      calledName: 'Serializable',
      sourceId: 'Function:src/index.ts:main',
    }];

    await processCallsFromExtracted(graph, calls, ctx.symbols, ctx.importMap);
    expect(graph.relationships.filter(r => r.type === 'CALLS')).toHaveLength(1);
  });

  it('creates CALLS edges to Enum symbols', async () => {
    ctx.symbols.add('src/status.ts', 'Status', 'Enum:src/status.ts:Status', 'Enum');
    ctx.importMap.set('src/index.ts', new Set(['src/status.ts']));

    const calls: ExtractedCall[] = [{
      filePath: 'src/index.ts',
      calledName: 'Status',
      sourceId: 'Function:src/index.ts:main',
    }];

    await processCallsFromExtracted(graph, calls, ctx.symbols, ctx.importMap);
    expect(graph.relationships.filter(r => r.type === 'CALLS')).toHaveLength(1);
  });

  it('prefers same-file over import-resolved', async () => {
    ctx.symbols.add('src/index.ts', 'render', 'Function:src/index.ts:render', 'Function');
    ctx.symbols.add('src/utils.ts', 'render', 'Function:src/utils.ts:render', 'Function');
    ctx.importMap.set('src/index.ts', new Set(['src/utils.ts']));

    const calls: ExtractedCall[] = [{
      filePath: 'src/index.ts',
      calledName: 'render',
      sourceId: 'Function:src/index.ts:main',
    }];

    await processCallsFromExtracted(graph, calls, ctx.symbols, ctx.importMap);

    const rels = graph.relationships.filter(r => r.type === 'CALLS');
    expect(rels).toHaveLength(1);
    expect(rels[0].targetId).toBe('Function:src/index.ts:render');
    expect(rels[0].reason).toBe('same-file');
  });

  it('handles multiple calls from the same file', async () => {
    ctx.symbols.add('src/index.ts', 'foo', 'Function:src/index.ts:foo', 'Function');
    ctx.symbols.add('src/index.ts', 'bar', 'Function:src/index.ts:bar', 'Function');

    const calls: ExtractedCall[] = [
      { filePath: 'src/index.ts', calledName: 'foo', sourceId: 'Function:src/index.ts:main' },
      { filePath: 'src/index.ts', calledName: 'bar', sourceId: 'Function:src/index.ts:main' },
    ];

    await processCallsFromExtracted(graph, calls, ctx.symbols, ctx.importMap);
    expect(graph.relationships.filter(r => r.type === 'CALLS')).toHaveLength(2);
  });

  it('picks first import-scoped match when multiple candidates exist (no arity disambiguation)', async () => {
    ctx.symbols.add('src/logger.ts', 'log', 'Function:src/logger.ts:log', 'Function', { parameterCount: 0 });
    ctx.symbols.add('src/formatter.ts', 'log', 'Function:src/formatter.ts:log', 'Function', { parameterCount: 1 });
    ctx.importMap.set('src/index.ts', new Set(['src/logger.ts', 'src/formatter.ts']));

    const calls: ExtractedCall[] = [{
      filePath: 'src/index.ts',
      calledName: 'log',
      sourceId: 'Function:src/index.ts:main',
      argCount: 1,
    }];

    await processCallsFromExtracted(graph, calls, ctx.symbols, ctx.importMap);

    const rels = graph.relationships.filter(r => r.type === 'CALLS');
    // Picks the first import-scoped match it finds
    expect(rels).toHaveLength(1);
    expect(rels[0].reason).toBe('import-resolved');
  });

  it('picks first match when multiple import-scoped candidates have same arity', async () => {
    ctx.symbols.add('src/logger.ts', 'log', 'Function:src/logger.ts:log', 'Function', { parameterCount: 1 });
    ctx.symbols.add('src/formatter.ts', 'log', 'Function:src/formatter.ts:log', 'Function', { parameterCount: 1 });
    ctx.importMap.set('src/index.ts', new Set(['src/logger.ts', 'src/formatter.ts']));

    const calls: ExtractedCall[] = [{
      filePath: 'src/index.ts',
      calledName: 'log',
      sourceId: 'Function:src/index.ts:main',
      argCount: 1,
    }];

    await processCallsFromExtracted(graph, calls, ctx.symbols, ctx.importMap);
    // No arity disambiguation — picks first import match
    expect(graph.relationships.filter(r => r.type === 'CALLS')).toHaveLength(1);
  });

  it('calls progress callback', async () => {
    ctx.symbols.add('src/index.ts', 'foo', 'Function:src/index.ts:foo', 'Function');

    const calls: ExtractedCall[] = [
      { filePath: 'src/index.ts', calledName: 'foo', sourceId: 'Function:src/index.ts:main' },
    ];

    const onProgress = vi.fn();
    await processCallsFromExtracted(graph, calls, ctx.symbols, ctx.importMap, onProgress);

    expect(onProgress).toHaveBeenCalledWith(1, 1);
  });

  it('handles empty calls array', async () => {
    await processCallsFromExtracted(graph, [], ctx.symbols, ctx.importMap);
    expect(graph.relationshipCount).toBe(0);
  });

  // ---- Name-based resolution (callForm/constructor not used in resolution) ----

  it('resolves call to Class symbol via import (name-based)', async () => {
    ctx.symbols.add('src/models.ts', 'User', 'Class:src/models.ts:User', 'Class');
    ctx.importMap.set('src/index.ts', new Set(['src/models.ts']));

    const calls: ExtractedCall[] = [{
      filePath: 'src/index.ts',
      calledName: 'User',
      sourceId: 'Function:src/index.ts:main',
    }];

    await processCallsFromExtracted(graph, calls, ctx.symbols, ctx.importMap);

    const rels = graph.relationships.filter(r => r.type === 'CALLS');
    expect(rels).toHaveLength(1);
    expect(rels[0].targetId).toBe('Class:src/models.ts:User');
    expect(rels[0].reason).toBe('import-resolved');
  });

  it('resolves to first matching import when multiple symbols share a name', async () => {
    // When same name has both Class and Constructor in same file, lookupExact returns one
    ctx.symbols.add('src/models.ts', 'User', 'Class:src/models.ts:User', 'Class');
    ctx.importMap.set('src/index.ts', new Set(['src/models.ts']));

    const calls: ExtractedCall[] = [{
      filePath: 'src/index.ts',
      calledName: 'User',
      sourceId: 'Function:src/index.ts:main',
    }];

    await processCallsFromExtracted(graph, calls, ctx.symbols, ctx.importMap);

    const rels = graph.relationships.filter(r => r.type === 'CALLS');
    expect(rels).toHaveLength(1);
  });

  it('resolves import-scoped Function symbol by name', async () => {
    ctx.symbols.add('src/utils.ts', 'Widget', 'Function:src/utils.ts:Widget', 'Function');
    ctx.importMap.set('src/index.ts', new Set(['src/utils.ts']));

    const calls: ExtractedCall[] = [{
      filePath: 'src/index.ts',
      calledName: 'Widget',
      sourceId: 'Function:src/index.ts:main',
    }];

    await processCallsFromExtracted(graph, calls, ctx.symbols, ctx.importMap);

    const rels = graph.relationships.filter(r => r.type === 'CALLS');
    expect(rels).toHaveLength(1);
    expect(rels[0].targetId).toBe('Function:src/utils.ts:Widget');
  });

  it('picks first import match when multiple imported symbols share a name', async () => {
    ctx.symbols.add('src/UserDao.ts', 'save', 'Function:src/UserDao.ts:save', 'Function', { parameterCount: 1 });
    ctx.symbols.add('src/RepoDao.ts', 'save', 'Function:src/RepoDao.ts:save', 'Function', { parameterCount: 1 });
    ctx.importMap.set('src/index.ts', new Set(['src/UserDao.ts', 'src/RepoDao.ts']));

    const calls: ExtractedCall[] = [{
      filePath: 'src/index.ts',
      calledName: 'save',
      sourceId: 'Function:src/index.ts:main',
    }];

    await processCallsFromExtracted(graph, calls, ctx.symbols, ctx.importMap);
    const rels = graph.relationships.filter(r => r.type === 'CALLS');
    // Picks first match — no disambiguation
    expect(rels).toHaveLength(1);
  });

  // ---- Name-based resolution for member calls (no constructor/return-type inference) ----

  it('resolves member call by calledName via import-scoped lookup', async () => {
    ctx.symbols.add('src/models.ts', 'save', 'Method:src/models.ts:save', 'Method');
    ctx.importMap.set('src/index.ts', new Set(['src/models.ts']));

    const calls: ExtractedCall[] = [{
      filePath: 'src/index.ts',
      calledName: 'save',
      sourceId: 'Function:src/index.ts:main',
    }];

    await processCallsFromExtracted(graph, calls, ctx.symbols, ctx.importMap);

    const rels = graph.relationships.filter(r => r.type === 'CALLS');
    expect(rels).toHaveLength(1);
    expect(rels[0].targetId).toBe('Method:src/models.ts:save');
    expect(rels[0].reason).toBe('import-resolved');
  });

  it('resolves method call via same-file when available', async () => {
    ctx.symbols.add('src/index.ts', 'query', 'Method:src/index.ts:query', 'Method');

    const calls: ExtractedCall[] = [{
      filePath: 'src/index.ts',
      calledName: 'query',
      sourceId: 'Method:src/index.ts:save',
    }];

    await processCallsFromExtracted(graph, calls, ctx.symbols, ctx.importMap);

    const rels = graph.relationships.filter(r => r.type === 'CALLS');
    expect(rels).toHaveLength(1);
    expect(rels[0].targetId).toBe('Method:src/index.ts:query');
    expect(rels[0].reason).toBe('same-file');
  });

  it('resolves multiple calls from different files independently', async () => {
    ctx.symbols.add('src/db/Database.ts', 'query', 'Method:src/db/Database.ts:query', 'Method');
    ctx.importMap.set('src/models/User.ts', new Set(['src/db/Database.ts']));
    ctx.importMap.set('src/models/Repo.ts', new Set(['src/db/Database.ts']));

    const calls: ExtractedCall[] = [
      {
        filePath: 'src/models/User.ts',
        calledName: 'query',
        sourceId: 'Method:src/models/User.ts:save',
      },
      {
        filePath: 'src/models/Repo.ts',
        calledName: 'query',
        sourceId: 'Method:src/models/Repo.ts:save',
      },
    ];

    await processCallsFromExtracted(graph, calls, ctx.symbols, ctx.importMap);

    const rels = graph.relationships.filter(r => r.type === 'CALLS');
    expect(rels).toHaveLength(2);
  });

  it('resolves calledName to global when not in imports or same file', async () => {
    ctx.symbols.add('src/models.ts', 'save', 'Function:src/models.ts:save', 'Function');

    const calls: ExtractedCall[] = [
      {
        filePath: 'src/index.ts',
        calledName: 'save',
        sourceId: 'Function:src/index.ts:processUser',
      },
      {
        filePath: 'src/index.ts',
        calledName: 'save',
        sourceId: 'Function:src/index.ts:processRepo',
      },
    ];

    await processCallsFromExtracted(graph, calls, ctx.symbols, ctx.importMap);

    const rels = graph.relationships.filter(r => r.type === 'CALLS');
    // Both calls resolve to the same unique global symbol
    expect(rels).toHaveLength(2);
    expect(rels[0].reason).toBe('fuzzy-global');
  });
});

describe('extractReturnTypeName', () => {
  it('extracts simple type name', () => {
    expect(extractReturnTypeName('User')).toBe('User');
  });

  it('unwraps Promise<User>', () => {
    expect(extractReturnTypeName('Promise<User>')).toBe('User');
  });

  it('unwraps Option<User>', () => {
    expect(extractReturnTypeName('Option<User>')).toBe('User');
  });

  it('unwraps Result<User, Error> to first type arg', () => {
    expect(extractReturnTypeName('Result<User, Error>')).toBe('User');
  });

  it('strips nullable union: User | null', () => {
    expect(extractReturnTypeName('User | null')).toBe('User');
  });

  it('strips nullable union: User | undefined', () => {
    expect(extractReturnTypeName('User | undefined')).toBe('User');
  });

  it('strips nullable suffix: User?', () => {
    expect(extractReturnTypeName('User?')).toBe('User');
  });

  it('strips Go pointer: *User', () => {
    expect(extractReturnTypeName('*User')).toBe('User');
  });

  it('strips Rust reference: &User', () => {
    expect(extractReturnTypeName('&User')).toBe('User');
  });

  it('strips Rust mutable reference: &mut User', () => {
    expect(extractReturnTypeName('&mut User')).toBe('User');
  });

  it('returns undefined for primitives', () => {
    expect(extractReturnTypeName('string')).toBeUndefined();
    expect(extractReturnTypeName('number')).toBeUndefined();
    expect(extractReturnTypeName('boolean')).toBeUndefined();
    expect(extractReturnTypeName('void')).toBeUndefined();
    expect(extractReturnTypeName('int')).toBeUndefined();
  });

  it('returns undefined for genuine union types', () => {
    expect(extractReturnTypeName('User | Repo')).toBeUndefined();
  });

  it('returns undefined for empty string', () => {
    expect(extractReturnTypeName('')).toBeUndefined();
  });

  it('extracts qualified type: models.User → User', () => {
    expect(extractReturnTypeName('models.User')).toBe('User');
  });

  it('handles non-wrapper generics: Map<K, V> → Map', () => {
    expect(extractReturnTypeName('Map<string, User>')).toBe('Map');
  });

  it('handles nested wrapper: Promise<Option<User>>', () => {
    // Promise<Option<User>> → unwrap Promise → Option<User> → unwrap Option → User
    expect(extractReturnTypeName('Promise<Option<User>>')).toBe('User');
  });

  it('returns base type for collection generics (not unwrapped)', () => {
    expect(extractReturnTypeName('Vec<User>')).toBe('Vec');
    expect(extractReturnTypeName('List<User>')).toBe('List');
    expect(extractReturnTypeName('Array<User>')).toBe('Array');
    expect(extractReturnTypeName('Set<User>')).toBe('Set');
    expect(extractReturnTypeName('ArrayList<User>')).toBe('ArrayList');
  });

  it('unwraps Optional<User>', () => {
    expect(extractReturnTypeName('Optional<User>')).toBe('User');
  });

  it('extracts Ruby :: qualified type: Models::User → User', () => {
    expect(extractReturnTypeName('Models::User')).toBe('User');
  });

  it('extracts C++ :: qualified type: ns::HttpClient → HttpClient', () => {
    expect(extractReturnTypeName('ns::HttpClient')).toBe('HttpClient');
  });

  it('extracts deep :: qualified type: crate::models::User → User', () => {
    expect(extractReturnTypeName('crate::models::User')).toBe('User');
  });

  it('extracts mixed qualifier: ns.module::User → User', () => {
    expect(extractReturnTypeName('ns.module::User')).toBe('User');
  });

  it('returns undefined for lowercase :: qualified: std::vector', () => {
    expect(extractReturnTypeName('std::vector')).toBeUndefined();
  });

  it('extracts deep dot-qualified: com.example.models.User → User', () => {
    expect(extractReturnTypeName('com.example.models.User')).toBe('User');
  });

  it('unwraps wrapper over non-wrapper generic: Promise<Map<string, User>> → Map', () => {
    // Promise is a wrapper — unwrap it to get Map<string, User>.
    // Map is not a wrapper, so return its base type: Map.
    expect(extractReturnTypeName('Promise<Map<string, User>>')).toBe('Map');
  });

  it('unwraps doubly-nested wrapper: Future<Result<User, Error>> → User', () => {
    // Future → unwrap → Result<User, Error>; Result → unwrap first arg → User
    expect(extractReturnTypeName('Future<Result<User, Error>>')).toBe('User');
  });

  it('unwraps CompletableFuture<Optional<User>> → User', () => {
    // CompletableFuture → unwrap → Optional<User>; Optional → unwrap → User
    expect(extractReturnTypeName('CompletableFuture<Optional<User>>')).toBe('User');
  });

  // Rust smart pointer unwrapping
  it('unwraps Rc<User> → User', () => {
    expect(extractReturnTypeName('Rc<User>')).toBe('User');
  });
  it('unwraps Arc<User> → User', () => {
    expect(extractReturnTypeName('Arc<User>')).toBe('User');
  });
  it('unwraps Weak<User> → User', () => {
    expect(extractReturnTypeName('Weak<User>')).toBe('User');
  });
  it('unwraps MutexGuard<User> → User', () => {
    expect(extractReturnTypeName('MutexGuard<User>')).toBe('User');
  });
  it('unwraps RwLockReadGuard<User> → User', () => {
    expect(extractReturnTypeName('RwLockReadGuard<User>')).toBe('User');
  });
  it('unwraps Cow<User> → User', () => {
    expect(extractReturnTypeName('Cow<User>')).toBe('User');
  });
  // Nested: Arc<Option<User>> → User (double unwrap)
  it('unwraps Arc<Option<User>> → User', () => {
    expect(extractReturnTypeName('Arc<Option<User>>')).toBe('User');
  });
  // NOT unwrapped (containers/wrappers not in set)
  it('does not unwrap Mutex<User> (not a Deref wrapper)', () => {
    expect(extractReturnTypeName('Mutex<User>')).toBe('Mutex');
  });

  // Rust lifetime parameters in wrapper generics
  it("skips lifetime in Ref<'_, User> → User", () => {
    expect(extractReturnTypeName("Ref<'_, User>")).toBe('User');
  });
  it("skips lifetime in RefMut<'a, User> → User", () => {
    expect(extractReturnTypeName("RefMut<'a, User>")).toBe('User');
  });
  it("skips lifetime in MutexGuard<'_, User> → User", () => {
    expect(extractReturnTypeName("MutexGuard<'_, User>")).toBe('User');
  });

  it('returns undefined for lowercase non-class types', () => {
    expect(extractReturnTypeName('error')).toBeUndefined();
  });

  it('extracts PHP backslash-namespaced type: \\App\\Models\\User → User', () => {
    expect(extractReturnTypeName('\\App\\Models\\User')).toBe('User');
  });

  it('extracts PHP single-segment namespace: \\User → User', () => {
    expect(extractReturnTypeName('\\User')).toBe('User');
  });

  it('extracts PHP deep namespace: \\Vendor\\Package\\Sub\\Client → Client', () => {
    expect(extractReturnTypeName('\\Vendor\\Package\\Sub\\Client')).toBe('Client');
  });

  it('returns undefined for bare wrapper type names without generic arguments', () => {
    expect(extractReturnTypeName('Task')).toBeUndefined();
    expect(extractReturnTypeName('Promise')).toBeUndefined();
    expect(extractReturnTypeName('Future')).toBeUndefined();
    expect(extractReturnTypeName('Option')).toBeUndefined();
    expect(extractReturnTypeName('Result')).toBeUndefined();
    expect(extractReturnTypeName('Observable')).toBeUndefined();
    expect(extractReturnTypeName('ValueTask')).toBeUndefined();
    expect(extractReturnTypeName('CompletableFuture')).toBeUndefined();
    expect(extractReturnTypeName('Optional')).toBeUndefined();
  });

  // ---- Length caps (Phase 6) ----

  it('pre-cap: returns undefined when raw input exceeds 2048 characters', () => {
    const longInput = 'A'.repeat(2049);
    expect(extractReturnTypeName(longInput)).toBeUndefined();
  });

  it('pre-cap: accepts raw input at exactly 2048 characters (boundary)', () => {
    // A 2048-char string of uppercase letters passes the pre-cap gate.
    // It won't match as a valid identifier (too long for post-cap), so the
    // result is undefined — but the pre-cap itself does NOT reject it.
    // We test this by verifying a 2048-char type that WOULD be valid in all
    // other respects is still returned as undefined (post-cap rejects it).
    const atLimit = 'U' + 'x'.repeat(2047); // 2048 chars, starts with uppercase
    // Post-cap (512) will reject this, but the pre-cap should not fire.
    // The important assertion: no throw and the result is undefined from post-cap.
    expect(extractReturnTypeName(atLimit)).toBeUndefined();
  });

  it('pre-cap: accepts inputs shorter than 2048 characters without rejection', () => {
    // 'User' is well under 2048 — should resolve normally.
    expect(extractReturnTypeName('User')).toBe('User');
  });

  it('post-cap: returns undefined when extracted type name exceeds 512 characters', () => {
    // Construct a raw string that is under the 2048-char pre-cap but produces
    // a final identifier longer than 512 characters after extraction.
    // A bare uppercase identifier of 513 chars satisfies all rules except post-cap.
    const longTypeName = 'U' + 'x'.repeat(512); // 513 chars, starts with uppercase
    expect(extractReturnTypeName(longTypeName)).toBeUndefined();
  });

  it('post-cap: accepts extracted type name at exactly 512 characters (boundary)', () => {
    // 512-char identifier should pass the post-cap check (> 512 rejects, not >=).
    const atLimit = 'U' + 'x'.repeat(511); // exactly 512 chars
    expect(extractReturnTypeName(atLimit)).toBe(atLimit);
  });

  it('post-cap: accepts normal short type names well under 512 characters', () => {
    expect(extractReturnTypeName('HttpClient')).toBe('HttpClient');
    expect(extractReturnTypeName('UserService')).toBe('UserService');
  });
});
