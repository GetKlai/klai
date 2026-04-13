import type { SyntaxNode } from '../utils.js';
import type { ConstructorBindingScanner, ForLoopExtractor, LanguageTypeConfig, ParameterExtractor, TypeBindingExtractor, PendingAssignmentExtractor, PatternBindingExtractor } from './types.js';
import { extractSimpleTypeName, extractVarName, findChildByType, unwrapAwait, extractGenericTypeArgs, resolveIterableElementType, methodToTypeArgPosition, extractElementTypeFromString, type TypeArgPosition } from './shared.js';

/** Known container property accessors that operate on the container itself (e.g., dict.Keys, dict.Values) */
const KNOWN_CONTAINER_PROPS: ReadonlySet<string> = new Set(['Keys', 'Values']);

const DECLARATION_NODE_TYPES: ReadonlySet<string> = new Set([
  'local_declaration_statement',
  'variable_declaration',
  'field_declaration',
]);

/** C#: Type x = ...; var x = new Type(); */
const extractDeclaration: TypeBindingExtractor = (node: SyntaxNode, env: Map<string, string>): void => {
  // C# tree-sitter: local_declaration_statement > variable_declaration > ...
  // Recursively descend through wrapper nodes
  for (let i = 0; i < node.namedChildCount; i++) {
    const child = node.namedChild(i);
    if (!child) continue;
    if (child.type === 'variable_declaration' || child.type === 'local_declaration_statement') {
      extractDeclaration(child, env);
      return;
    }
  }

  // At variable_declaration level: first child is type, rest are variable_declarators
  let typeNode: SyntaxNode | null = null;
  const declarators: SyntaxNode[] = [];

  for (let i = 0; i < node.namedChildCount; i++) {
    const child = node.namedChild(i);
    if (!child) continue;

    if (!typeNode && child.type !== 'variable_declarator' && child.type !== 'equals_value_clause') {
      // First non-declarator child is the type (identifier, implicit_type, generic_name, etc.)
      typeNode = child;
    }
    if (child.type === 'variable_declarator') {
      declarators.push(child);
    }
  }

  if (!typeNode || declarators.length === 0) return;

  // Handle 'var x = new Foo()' — infer from object_creation_expression
  let typeName: string | undefined;
  if (typeNode.type === 'implicit_type' && typeNode.text === 'var') {
    // Try to infer from initializer: var x = new Foo()
    // tree-sitter-c-sharp may put object_creation_expression as direct child
    // or inside equals_value_clause depending on grammar version
    if (declarators.length === 1) {
      const initializer = findChildByType(declarators[0], 'object_creation_expression')
        ?? findChildByType(declarators[0], 'equals_value_clause')?.firstNamedChild;
      if (initializer?.type === 'object_creation_expression') {
        const ctorType = initializer.childForFieldName('type');
        if (ctorType) typeName = extractSimpleTypeName(ctorType);
      }
    }
  } else {
    typeName = extractSimpleTypeName(typeNode);
  }

  if (!typeName) return;
  for (const decl of declarators) {
    const nameNode = decl.childForFieldName('name') ?? decl.firstNamedChild;
    if (nameNode) {
      const varName = extractVarName(nameNode);
      if (varName) env.set(varName, typeName);
    }
  }
};

/** C#: parameter → type name */
const extractParameter: ParameterExtractor = (node: SyntaxNode, env: Map<string, string>): void => {
  let nameNode: SyntaxNode | null = null;
  let typeNode: SyntaxNode | null = null;

  if (node.type === 'parameter') {
    typeNode = node.childForFieldName('type');
    nameNode = node.childForFieldName('name');
  } else {
    nameNode = node.childForFieldName('name') ?? node.childForFieldName('pattern');
    typeNode = node.childForFieldName('type');
  }

  if (!nameNode || !typeNode) return;
  const varName = extractVarName(nameNode);
  const typeName = extractSimpleTypeName(typeNode);
  if (varName && typeName) env.set(varName, typeName);
};

/** C#: var x = SomeFactory(...) → bind x to SomeFactory (constructor-like call) */
const scanConstructorBinding: ConstructorBindingScanner = (node) => {
  if (node.type !== 'variable_declaration') return undefined;
  // Find type and declarator children by iterating (C# grammar doesn't expose 'type' as a named field)
  let typeNode: SyntaxNode | null = null;
  let declarator: SyntaxNode | null = null;
  for (let i = 0; i < node.namedChildCount; i++) {
    const child = node.namedChild(i);
    if (!child) continue;
    if (child.type === 'variable_declarator') { if (!declarator) declarator = child; }
    else if (!typeNode) { typeNode = child; }
  }
  // Only handle implicit_type (var) — explicit types handled by extractDeclaration
  if (!typeNode || typeNode.type !== 'implicit_type') return undefined;
  if (!declarator) return undefined;
  const nameNode = declarator.childForFieldName('name') ?? declarator.firstNamedChild;
  if (!nameNode || nameNode.type !== 'identifier') return undefined;
  // Find the initializer value: either inside equals_value_clause or as a direct child
  // (tree-sitter-c-sharp puts invocation_expression directly inside variable_declarator)
  let value: SyntaxNode | null = null;
  for (let i = 0; i < declarator.namedChildCount; i++) {
    const child = declarator.namedChild(i);
    if (!child) continue;
    if (child.type === 'equals_value_clause') { value = child.firstNamedChild; break; }
    if (child.type === 'invocation_expression' || child.type === 'object_creation_expression' || child.type === 'await_expression') { value = child; break; }
  }
  if (!value) return undefined;
  // Unwrap await: `var user = await svc.GetUserAsync()` → await_expression wraps invocation_expression
  value = unwrapAwait(value);
  if (!value) return undefined;
  // Skip object_creation_expression (new User()) — handled by extractInitializer
  if (value.type === 'object_creation_expression') return undefined;
  if (value.type !== 'invocation_expression') return undefined;
  const func = value.firstNamedChild;
  if (!func) return undefined;
  const calleeName = extractSimpleTypeName(func);
  if (!calleeName) return undefined;
  return { varName: nameNode.text, calleeName };
};

const FOR_LOOP_NODE_TYPES: ReadonlySet<string> = new Set([
  'foreach_statement',
]);

/** Extract element type from a C# type annotation AST node.
 *  Handles generic_name (List<User>), array_type (User[]), nullable_type (?).
 *  `pos` selects which type arg: 'first' for keys, 'last' for values (default). */
const extractCSharpElementTypeFromTypeNode = (typeNode: SyntaxNode, pos: TypeArgPosition = 'last', depth = 0): string | undefined => {
  if (depth > 50) return undefined;
  // generic_name: List<User>, IEnumerable<User>, Dictionary<string, User>
  // C# uses generic_name (not generic_type)
  if (typeNode.type === 'generic_name') {
    const argList = findChildByType(typeNode, 'type_argument_list');
    if (argList && argList.namedChildCount >= 1) {
      if (pos === 'first') {
        const firstArg = argList.namedChild(0);
        if (firstArg) return extractSimpleTypeName(firstArg);
      } else {
        const lastArg = argList.namedChild(argList.namedChildCount - 1);
        if (lastArg) return extractSimpleTypeName(lastArg);
      }
    }
  }
  // array_type: User[]
  if (typeNode.type === 'array_type') {
    const elemNode = typeNode.firstNamedChild;
    if (elemNode) return extractSimpleTypeName(elemNode);
  }
  // nullable_type: unwrap and recurse (List<User>? → List<User> → User)
  if (typeNode.type === 'nullable_type') {
    const inner = typeNode.firstNamedChild;
    if (inner) return extractCSharpElementTypeFromTypeNode(inner, pos, depth + 1);
  }
  return undefined;
};

/** Walk up from a foreach to the enclosing method and search parameters. */
const findCSharpParamElementType = (iterableName: string, startNode: SyntaxNode, pos: TypeArgPosition = 'last'): string | undefined => {
  let current: SyntaxNode | null = startNode.parent;
  while (current) {
    if (current.type === 'method_declaration' || current.type === 'local_function_statement') {
      const paramsNode = current.childForFieldName('parameters');
      if (paramsNode) {
        for (let i = 0; i < paramsNode.namedChildCount; i++) {
          const param = paramsNode.namedChild(i);
          if (!param || param.type !== 'parameter') continue;
          const nameNode = param.childForFieldName('name');
          if (nameNode?.text !== iterableName) continue;
          const typeNode = param.childForFieldName('type');
          if (typeNode) return extractCSharpElementTypeFromTypeNode(typeNode, pos);
        }
      }
      break;
    }
    current = current.parent;
  }
  return undefined;
};

/** C#: foreach (User user in users) — extract loop variable binding.
 *  Tier 1c: for `foreach (var user in users)`, resolves element type from iterable. */
const extractForLoopBinding: ForLoopExtractor = (node, { scopeEnv, declarationTypeNodes, scope, returnTypeLookup }): void => {
  const typeNode = node.childForFieldName('type');
  const nameNode = node.childForFieldName('left');
  if (!typeNode || !nameNode) return;
  const varName = extractVarName(nameNode);
  if (!varName) return;

  // Explicit type (existing behavior): foreach (User user in users)
  if (!(typeNode.type === 'implicit_type' && typeNode.text === 'var')) {
    const typeName = extractSimpleTypeName(typeNode);
    if (typeName) scopeEnv.set(varName, typeName);
    return;
  }

  // Tier 1c: implicit type (var) — resolve from iterable's container type
  const rightNode = node.childForFieldName('right');
  let iterableName: string | undefined;
  let methodName: string | undefined;
  let callExprElementType: string | undefined;

  if (rightNode?.type === 'identifier') {
    iterableName = rightNode.text;
  } else if (rightNode?.type === 'member_access_expression') {
    // C# property access: data.Keys, data.Values → member_access_expression
    // Also handles bare member access: this.users, repo.users → use property as iterableName
    const obj = rightNode.childForFieldName('expression');
    const prop = rightNode.childForFieldName('name');
    const propText = prop?.type === 'identifier' ? prop.text : undefined;
    if (propText && KNOWN_CONTAINER_PROPS.has(propText)) {
      if (obj?.type === 'identifier') {
        iterableName = obj.text;
      } else if (obj?.type === 'member_access_expression') {
        // Nested member access: this.data.Values → obj is "this.data", extract "data"
        const innerProp = obj.childForFieldName('name');
        if (innerProp) iterableName = innerProp.text;
      }
      methodName = propText;
    } else if (propText) {
      // Bare member access: this.users → use property name for scopeEnv lookup
      iterableName = propText;
    }
  } else if (rightNode?.type === 'invocation_expression') {
    // C# method call: data.Select(...) → invocation_expression > member_access_expression
    // Direct function call: GetUsers() → invocation_expression > identifier
    const fn = rightNode.firstNamedChild;
    if (fn?.type === 'member_access_expression') {
      const obj = fn.childForFieldName('expression');
      const prop = fn.childForFieldName('name');
      if (obj?.type === 'identifier') iterableName = obj.text;
      if (prop?.type === 'identifier') methodName = prop.text;
    } else if (fn?.type === 'identifier') {
      // Direct function call: foreach (var u in GetUsers())
      const rawReturn = returnTypeLookup.lookupRawReturnType(fn.text);
      if (rawReturn) callExprElementType = extractElementTypeFromString(rawReturn);
    }
  }
  if (!iterableName && !callExprElementType) return;

  let elementType: string | undefined;
  if (callExprElementType) {
    elementType = callExprElementType;
  } else {
    const containerTypeName = scopeEnv.get(iterableName!);
    const typeArgPos = methodToTypeArgPosition(methodName, containerTypeName);
    elementType = resolveIterableElementType(
      iterableName!, node, scopeEnv, declarationTypeNodes, scope,
      extractCSharpElementTypeFromTypeNode, findCSharpParamElementType,
      typeArgPos,
    );
  }
  if (elementType) scopeEnv.set(varName, elementType);
};

/**
 * C# pattern binding extractor for `obj is Type variable` (type pattern).
 *
 * AST structure:
 *   is_pattern_expression
 *     expression: (the variable being tested)
 *     pattern: declaration_pattern
 *       type: (the declared type)
 *       name: single_variable_designation > identifier (the new variable name)
 *
 * Conservative: returns undefined when the pattern field is absent, is not a
 * declaration_pattern, or when the type/name cannot be extracted.
 * No scopeEnv lookup is needed — the pattern explicitly declares the new variable's type.
 */
const extractPatternBinding: PatternBindingExtractor = (node) => {
  // is_pattern_expression: `obj is User user` — has a declaration_pattern child
  if (node.type === 'is_pattern_expression') {
    const pattern = node.childForFieldName('pattern');
    if (pattern?.type !== 'declaration_pattern' && pattern?.type !== 'recursive_pattern') return undefined;
    const typeNode = pattern.childForFieldName('type');
    const nameNode = pattern.childForFieldName('name');
    if (!typeNode || !nameNode) return undefined;
    const typeName = extractSimpleTypeName(typeNode);
    const varName = extractVarName(nameNode);
    if (!typeName || !varName) return undefined;
    return { varName, typeName };
  }
  // declaration_pattern / recursive_pattern: standalone in switch statements and switch expressions
  // `case User u:` or `User u =>` or `User { Name: "Alice" } u =>`
  // Both use the same 'type' and 'name' fields.
  if (node.type === 'declaration_pattern' || node.type === 'recursive_pattern') {
    const typeNode = node.childForFieldName('type');
    const nameNode = node.childForFieldName('name');
    if (!typeNode || !nameNode) return undefined;
    const typeName = extractSimpleTypeName(typeNode);
    const varName = extractVarName(nameNode);
    if (!typeName || !varName) return undefined;
    return { varName, typeName };
  }
  return undefined;
};

/** C#: var alias = u → variable_declarator with name + equals_value_clause.
 *  Only local_declaration_statement and variable_declaration contain variable_declarator children;
 *  is_pattern_expression and field_declaration never do — skip them early. */
const extractPendingAssignment: PendingAssignmentExtractor = (node, scopeEnv) => {
  if (node.type === 'is_pattern_expression' || node.type === 'field_declaration') return undefined;
  for (let i = 0; i < node.namedChildCount; i++) {
    const child = node.namedChild(i);
    if (!child || child.type !== 'variable_declarator') continue;
    const nameNode = child.childForFieldName('name');
    if (!nameNode) continue;
    const lhs = nameNode.text;
    if (scopeEnv.has(lhs)) continue;
    // C# wraps value in equals_value_clause; fall back to last named child
    let evc: SyntaxNode | null = null;
    for (let j = 0; j < child.childCount; j++) {
      if (child.child(j)?.type === 'equals_value_clause') { evc = child.child(j); break; }
    }
    const valueNode = evc?.firstNamedChild ?? child.namedChild(child.namedChildCount - 1);
    if (valueNode && valueNode !== nameNode && (valueNode.type === 'identifier' || valueNode.type === 'simple_identifier')) {
      return { kind: 'copy', lhs, rhs: valueNode.text };
    }
  }
  return undefined;
};

export const typeConfig: LanguageTypeConfig = {
  declarationNodeTypes: DECLARATION_NODE_TYPES,
  forLoopNodeTypes: FOR_LOOP_NODE_TYPES,
  patternBindingNodeTypes: new Set(['is_pattern_expression', 'declaration_pattern', 'recursive_pattern']),
  extractDeclaration,
  extractParameter,
  scanConstructorBinding,
  extractForLoopBinding,
  extractPendingAssignment,
  extractPatternBinding,
};
