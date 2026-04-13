import type { SyntaxNode } from '../utils.js';
import type { LanguageTypeConfig, ParameterExtractor, TypeBindingExtractor, InitializerExtractor, ClassNameLookup, ConstructorBindingScanner, ForLoopExtractor, PendingAssignmentExtractor, PatternBindingExtractor } from './types.js';
import { extractSimpleTypeName, extractVarName, findChildByType, extractGenericTypeArgs, resolveIterableElementType, methodToTypeArgPosition, extractElementTypeFromString, type TypeArgPosition } from './shared.js';

// ── Java ──────────────────────────────────────────────────────────────────

const JAVA_DECLARATION_NODE_TYPES: ReadonlySet<string> = new Set([
  'local_variable_declaration',
  'field_declaration',
]);

/** Java: Type x = ...; Type x; */
const extractJavaDeclaration: TypeBindingExtractor = (node: SyntaxNode, env: Map<string, string>): void => {
  const typeNode = node.childForFieldName('type');
  if (!typeNode) return;
  const typeName = extractSimpleTypeName(typeNode);
  if (!typeName || typeName === 'var') return; // skip Java 10 var — handled by extractInitializer

  // Find variable_declarator children
  for (let i = 0; i < node.namedChildCount; i++) {
    const child = node.namedChild(i);
    if (child?.type !== 'variable_declarator') continue;
    const nameNode = child.childForFieldName('name');
    if (nameNode) {
      const varName = extractVarName(nameNode);
      if (varName) env.set(varName, typeName);
    }
  }
};

/** Java 10+: var x = new User() — infer type from object_creation_expression */
const extractJavaInitializer: InitializerExtractor = (node: SyntaxNode, env: Map<string, string>, _classNames: ClassNameLookup): void => {
  for (let i = 0; i < node.namedChildCount; i++) {
    const child = node.namedChild(i);
    if (child?.type !== 'variable_declarator') continue;
    const nameNode = child.childForFieldName('name');
    const valueNode = child.childForFieldName('value');
    if (!nameNode || !valueNode) continue;
    // Skip declarators that already have a binding from extractDeclaration
    const varName = extractVarName(nameNode);
    if (!varName || env.has(varName)) continue;
    if (valueNode.type !== 'object_creation_expression') continue;
    const ctorType = valueNode.childForFieldName('type');
    if (!ctorType) continue;
    const typeName = extractSimpleTypeName(ctorType);
    if (typeName) env.set(varName, typeName);
  }
};

/** Java: formal_parameter → type name */
const extractJavaParameter: ParameterExtractor = (node: SyntaxNode, env: Map<string, string>): void => {
  let nameNode: SyntaxNode | null = null;
  let typeNode: SyntaxNode | null = null;

  if (node.type === 'formal_parameter') {
    typeNode = node.childForFieldName('type');
    nameNode = node.childForFieldName('name');
  } else {
    // Generic fallback
    nameNode = node.childForFieldName('name') ?? node.childForFieldName('pattern');
    typeNode = node.childForFieldName('type');
  }

  if (!nameNode || !typeNode) return;
  const varName = extractVarName(nameNode);
  const typeName = extractSimpleTypeName(typeNode);
  if (varName && typeName) env.set(varName, typeName);
};

/** Java: var x = SomeFactory.create() — constructor binding for `var` with method_invocation */
const scanJavaConstructorBinding: ConstructorBindingScanner = (node) => {
  if (node.type !== 'local_variable_declaration') return undefined;
  const typeNode = node.childForFieldName('type');
  if (!typeNode) return undefined;
  if (typeNode.text !== 'var') return undefined;
  const declarator = findChildByType(node, 'variable_declarator');
  if (!declarator) return undefined;
  const nameNode = declarator.childForFieldName('name');
  const value = declarator.childForFieldName('value');
  if (!nameNode || !value) return undefined;
  if (value.type === 'object_creation_expression') return undefined;
  if (value.type !== 'method_invocation') return undefined;
  const methodName = value.childForFieldName('name');
  if (!methodName) return undefined;
  return { varName: nameNode.text, calleeName: methodName.text };
};

const JAVA_FOR_LOOP_NODE_TYPES: ReadonlySet<string> = new Set([
  'enhanced_for_statement',
]);

/** Extract element type from a Java type annotation AST node.
 *  Handles generic_type (List<User>), array_type (User[]). */
const extractJavaElementTypeFromTypeNode = (typeNode: SyntaxNode, pos: TypeArgPosition = 'last'): string | undefined => {
  if (typeNode.type === 'generic_type') {
    const args = extractGenericTypeArgs(typeNode);
    if (args.length >= 1) return pos === 'first' ? args[0] : args[args.length - 1];
  }
  if (typeNode.type === 'array_type') {
    const elemNode = typeNode.firstNamedChild;
    if (elemNode) return extractSimpleTypeName(elemNode);
  }
  return undefined;
};

/** Walk up from a for-each to the enclosing method_declaration and search parameters. */
const findJavaParamElementType = (iterableName: string, startNode: SyntaxNode, pos: TypeArgPosition = 'last'): string | undefined => {
  let current: SyntaxNode | null = startNode.parent;
  while (current) {
    if (current.type === 'method_declaration' || current.type === 'constructor_declaration') {
      const paramsNode = current.childForFieldName('parameters');
      if (paramsNode) {
        for (let i = 0; i < paramsNode.namedChildCount; i++) {
          const param = paramsNode.namedChild(i);
          if (!param || param.type !== 'formal_parameter') continue;
          const nameNode = param.childForFieldName('name');
          if (nameNode?.text !== iterableName) continue;
          const typeNode = param.childForFieldName('type');
          if (typeNode) return extractJavaElementTypeFromTypeNode(typeNode, pos);
        }
      }
      break;
    }
    current = current.parent;
  }
  return undefined;
};

/** Java: for (User user : users) — extract loop variable binding.
 *  Tier 1c: for `for (var user : users)`, resolves element type from iterable. */
const extractJavaForLoopBinding: ForLoopExtractor = (node,  { scopeEnv, declarationTypeNodes, scope, returnTypeLookup }): void => {
  const typeNode = node.childForFieldName('type');
  const nameNode = node.childForFieldName('name');
  if (!typeNode || !nameNode) return;
  const varName = extractVarName(nameNode);
  if (!varName) return;

  // Explicit type (existing behavior): for (User user : users)
  const typeName = extractSimpleTypeName(typeNode);
  if (typeName && typeName !== 'var') {
    scopeEnv.set(varName, typeName);
    return;
  }

  // Tier 1c: var — resolve from iterable's container type
  const iterableNode = node.childForFieldName('value');
  if (!iterableNode) return;

  let iterableName: string | undefined;
  let methodName: string | undefined;
  let callExprElementType: string | undefined;
  if (iterableNode.type === 'identifier') {
    iterableName = iterableNode.text;
  } else if (iterableNode.type === 'field_access') {
    const field = iterableNode.childForFieldName('field');
    if (field) iterableName = field.text;
  } else if (iterableNode.type === 'method_invocation') {
    // data.keySet() → method_invocation > object: identifier + name: identifier
    // Also handles this.data.values() → object is field_access, extract inner field name
    const obj = iterableNode.childForFieldName('object');
    const name = iterableNode.childForFieldName('name');
    if (obj?.type === 'identifier') {
      iterableName = obj.text;
    } else if (obj?.type === 'field_access') {
      const innerField = obj.childForFieldName('field');
      if (innerField) iterableName = innerField.text;
    } else if (!obj && name) {
      // Direct function call: for (var u : getUsers()) — no receiver object
      const rawReturn = returnTypeLookup.lookupRawReturnType(name.text);
      if (rawReturn) callExprElementType = extractElementTypeFromString(rawReturn);
    }
    if (name) methodName = name.text;
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
      extractJavaElementTypeFromTypeNode, findJavaParamElementType,
      typeArgPos,
    );
  }
  if (elementType) scopeEnv.set(varName, elementType);
};

/** Java: var alias = u → local_variable_declaration > variable_declarator with name/value */
const extractJavaPendingAssignment: PendingAssignmentExtractor = (node, scopeEnv) => {
  for (let i = 0; i < node.namedChildCount; i++) {
    const child = node.namedChild(i);
    if (!child || child.type !== 'variable_declarator') continue;
    const nameNode = child.childForFieldName('name');
    const valueNode = child.childForFieldName('value');
    if (!nameNode || !valueNode) continue;
    const lhs = nameNode.text;
    if (scopeEnv.has(lhs)) continue;
    if (valueNode.type === 'identifier' || valueNode.type === 'simple_identifier') return { kind: 'copy', lhs, rhs: valueNode.text };
  }
  return undefined;
};

/**
 * Java 16+ `instanceof` pattern variable: `x instanceof User user`
 *
 * AST structure:
 *   instanceof_expression
 *     left: expression (the variable being tested)
 *     instanceof keyword
 *     right: type (the type to test against)
 *     name: identifier (the pattern variable — optional, Java 16+)
 *
 * Conservative: returns undefined when the `name` field is absent (plain instanceof
 * without pattern variable, e.g. `x instanceof User`) or when the type cannot be
 * extracted. The source variable's existing type is NOT used — the pattern explicitly
 * declares the new type, so no scopeEnv lookup is needed.
 */
const extractJavaPatternBinding: PatternBindingExtractor = (node) => {
  if (node.type === 'type_pattern') {
    // Java 17+ switch pattern: case User u -> ...
    // type_pattern has positional children (NO named fields):
    //   namedChild(0) = type (type_identifier, e.g., User)
    //   namedChild(1) = identifier (e.g., u)
    const typeNode = node.namedChild(0);
    const nameNode = node.namedChild(1);
    if (!typeNode || !nameNode) return undefined;
    const typeName = extractSimpleTypeName(typeNode);
    const varName = extractVarName(nameNode);
    if (!typeName || !varName) return undefined;
    return { varName, typeName };
  }
  if (node.type !== 'instanceof_expression') return undefined;
  const nameNode = node.childForFieldName('name');
  if (!nameNode) return undefined;
  const typeNode = node.childForFieldName('right');
  if (!typeNode) return undefined;
  const typeName = extractSimpleTypeName(typeNode);
  const varName = extractVarName(nameNode);
  if (!typeName || !varName) return undefined;
  return { varName, typeName };
};

export const javaTypeConfig: LanguageTypeConfig = {
  declarationNodeTypes: JAVA_DECLARATION_NODE_TYPES,
  forLoopNodeTypes: JAVA_FOR_LOOP_NODE_TYPES,
  patternBindingNodeTypes: new Set(['instanceof_expression', 'type_pattern']),
  extractDeclaration: extractJavaDeclaration,
  extractParameter: extractJavaParameter,
  extractInitializer: extractJavaInitializer,
  scanConstructorBinding: scanJavaConstructorBinding,
  extractForLoopBinding: extractJavaForLoopBinding,
  extractPendingAssignment: extractJavaPendingAssignment,
  extractPatternBinding: extractJavaPatternBinding,
};

// ── Kotlin ────────────────────────────────────────────────────────────────

const KOTLIN_DECLARATION_NODE_TYPES: ReadonlySet<string> = new Set([
  'property_declaration',
  'variable_declaration',
]);

/** Kotlin: val x: Foo = ... */
const extractKotlinDeclaration: TypeBindingExtractor = (node: SyntaxNode, env: Map<string, string>): void => {
  if (node.type === 'property_declaration') {
    // Kotlin property_declaration: name/type are inside a variable_declaration child
    const varDecl = findChildByType(node, 'variable_declaration');
    if (varDecl) {
      const nameNode = findChildByType(varDecl, 'simple_identifier');
      const typeNode = findChildByType(varDecl, 'user_type');
      if (!nameNode || !typeNode) return;
      const varName = extractVarName(nameNode);
      const typeName = extractSimpleTypeName(typeNode);
      if (varName && typeName) env.set(varName, typeName);
      return;
    }
    // Fallback: try direct fields
    const nameNode = node.childForFieldName('name')
      ?? findChildByType(node, 'simple_identifier');
    const typeNode = node.childForFieldName('type')
      ?? findChildByType(node, 'user_type');
    if (!nameNode || !typeNode) return;
    const varName = extractVarName(nameNode);
    const typeName = extractSimpleTypeName(typeNode);
    if (varName && typeName) env.set(varName, typeName);
  } else if (node.type === 'variable_declaration') {
    // variable_declaration directly inside functions
    const nameNode = findChildByType(node, 'simple_identifier');
    const typeNode = findChildByType(node, 'user_type');
    if (nameNode && typeNode) {
      const varName = extractVarName(nameNode);
      const typeName = extractSimpleTypeName(typeNode);
      if (varName && typeName) env.set(varName, typeName);
    }
  }
};

/** Kotlin: parameter / formal_parameter → type name.
 *  Kotlin's tree-sitter grammar uses positional children (simple_identifier, user_type)
 *  rather than named fields (name, type) on `parameter` nodes, so we fall back to
 *  findChildByType when childForFieldName returns null. */
const extractKotlinParameter: ParameterExtractor = (node: SyntaxNode, env: Map<string, string>): void => {
  let nameNode: SyntaxNode | null = null;
  let typeNode: SyntaxNode | null = null;

  if (node.type === 'formal_parameter') {
    typeNode = node.childForFieldName('type');
    nameNode = node.childForFieldName('name');
  } else {
    nameNode = node.childForFieldName('name') ?? node.childForFieldName('pattern');
    typeNode = node.childForFieldName('type');
  }

  // Fallback: Kotlin `parameter` nodes use positional children, not named fields
  if (!nameNode) nameNode = findChildByType(node, 'simple_identifier');
  if (!typeNode) typeNode = findChildByType(node, 'user_type');

  if (!nameNode || !typeNode) return;
  const varName = extractVarName(nameNode);
  const typeName = extractSimpleTypeName(typeNode);
  if (varName && typeName) env.set(varName, typeName);
};

/** Kotlin: val user = User() — infer type from call_expression when callee is a known class.
 *  Kotlin constructors are syntactically identical to function calls, so we verify
 *  against classNames (which may include cross-file SymbolTable lookups). */
const extractKotlinInitializer: InitializerExtractor = (node: SyntaxNode, env: Map<string, string>, classNames: ClassNameLookup): void => {
  if (node.type !== 'property_declaration') return;
  // Skip if there's an explicit type annotation — Tier 0 already handled it
  const varDecl = findChildByType(node, 'variable_declaration');
  if (varDecl && findChildByType(varDecl, 'user_type')) return;

  // Get the initializer value — the call_expression after '='
  const value = node.childForFieldName('value')
    ?? findChildByType(node, 'call_expression');
  if (!value || value.type !== 'call_expression') return;

  // The callee is the first child of call_expression (simple_identifier for direct calls)
  const callee = value.firstNamedChild;
  if (!callee || callee.type !== 'simple_identifier') return;

  const calleeName = callee.text;
  if (!calleeName || !classNames.has(calleeName)) return;

  // Extract the variable name from the variable_declaration inside property_declaration
  const nameNode = varDecl
    ? findChildByType(varDecl, 'simple_identifier')
    : findChildByType(node, 'simple_identifier');
  if (!nameNode) return;

  const varName = extractVarName(nameNode);
  if (varName) env.set(varName, calleeName);
};

/** Kotlin: val x = User(...) — constructor binding for property_declaration with call_expression */
const scanKotlinConstructorBinding: ConstructorBindingScanner = (node) => {
  if (node.type !== 'property_declaration') return undefined;
  const varDecl = findChildByType(node, 'variable_declaration');
  if (!varDecl) return undefined;
  if (findChildByType(varDecl, 'user_type')) return undefined;
  const callExpr = findChildByType(node, 'call_expression');
  if (!callExpr) return undefined;
  const callee = callExpr.firstNamedChild;
  if (!callee) return undefined;

  let calleeName: string | undefined;
  if (callee.type === 'simple_identifier') {
    calleeName = callee.text;
  } else if (callee.type === 'navigation_expression') {
    // Extract method name from qualified call: service.getUser() → getUser
    const suffix = callee.lastNamedChild;
    if (suffix?.type === 'navigation_suffix') {
      const methodName = suffix.lastNamedChild;
      if (methodName?.type === 'simple_identifier') {
        calleeName = methodName.text;
      }
    }
  }
  if (!calleeName) return undefined;
  const nameNode = findChildByType(varDecl, 'simple_identifier');
  if (!nameNode) return undefined;
  return { varName: nameNode.text, calleeName };
};

const KOTLIN_FOR_LOOP_NODE_TYPES: ReadonlySet<string> = new Set([
  'for_statement',
]);

/** Extract element type from a Kotlin type annotation AST node (user_type wrapping generic).
 *  Kotlin: user_type → [type_identifier, type_arguments → [type_projection → user_type]]
 *  Handles the type_projection wrapper that Kotlin uses for generic type arguments. */
const extractKotlinElementTypeFromTypeNode = (typeNode: SyntaxNode, pos: TypeArgPosition = 'last'): string | undefined => {
  if (typeNode.type === 'user_type') {
    const argsNode = findChildByType(typeNode, 'type_arguments');
    if (argsNode && argsNode.namedChildCount >= 1) {
      const targetArg = pos === 'first'
        ? argsNode.namedChild(0)
        : argsNode.namedChild(argsNode.namedChildCount - 1);
      if (!targetArg) return undefined;
      // Kotlin wraps type args in type_projection — unwrap to get the inner type
      const inner = targetArg.type === 'type_projection'
        ? targetArg.firstNamedChild
        : targetArg;
      if (inner) return extractSimpleTypeName(inner);
    }
  }
  return undefined;
};

/** Walk up from a for-loop to the enclosing function_declaration and search parameters.
 *  Kotlin parameters use positional children (simple_identifier, user_type), not named fields. */
const findKotlinParamElementType = (iterableName: string, startNode: SyntaxNode, pos: TypeArgPosition = 'last'): string | undefined => {
  let current: SyntaxNode | null = startNode.parent;
  while (current) {
    if (current.type === 'function_declaration') {
      const paramsNode = findChildByType(current, 'function_value_parameters');
      if (paramsNode) {
        for (let i = 0; i < paramsNode.namedChildCount; i++) {
          const param = paramsNode.namedChild(i);
          if (!param || param.type !== 'parameter') continue;
          const nameNode = findChildByType(param, 'simple_identifier');
          if (nameNode?.text !== iterableName) continue;
          const typeNode = findChildByType(param, 'user_type');
          if (typeNode) return extractKotlinElementTypeFromTypeNode(typeNode, pos);
        }
      }
      break;
    }
    current = current.parent;
  }
  return undefined;
};

/** Kotlin: for (user: User in users) — extract loop variable binding.
 *  Tier 1c: for `for (user in users)` without annotation, resolves from iterable. */
const extractKotlinForLoopBinding: ForLoopExtractor = (node, ctx): void => {
  const { scopeEnv, declarationTypeNodes, scope, returnTypeLookup } = ctx;
  const varDecl = findChildByType(node, 'variable_declaration');
  if (!varDecl) return;
  const nameNode = findChildByType(varDecl, 'simple_identifier');
  if (!nameNode) return;
  const varName = extractVarName(nameNode);
  if (!varName) return;

  // Explicit type annotation (existing behavior): for (user: User in users)
  const typeNode = findChildByType(varDecl, 'user_type');
  if (typeNode) {
    const typeName = extractSimpleTypeName(typeNode);
    if (typeName) scopeEnv.set(varName, typeName);
    return;
  }

  // Tier 1c: no annotation — resolve from iterable's container type
  // Kotlin for-loop children: [variable_declaration, iterable_expr, control_structure_body]
  // The iterable is the second named child of the for_statement (after variable_declaration)
  let iterableName: string | undefined;
  let methodName: string | undefined;
  let fallbackIterableName: string | undefined;
  let callExprElementType: string | undefined;
  let foundVarDecl = false;
  for (let i = 0; i < node.namedChildCount; i++) {
    const child = node.namedChild(i);
    if (child === varDecl) { foundVarDecl = true; continue; }
    if (!foundVarDecl || !child) continue;
    if (child.type === 'simple_identifier') {
      iterableName = child.text;
      break;
    }
    if (child.type === 'navigation_expression') {
      // data.keys → navigation_expression > simple_identifier(data) + navigation_suffix > simple_identifier(keys)
      const obj = child.firstNamedChild;
      const suffix = findChildByType(child, 'navigation_suffix');
      const prop = suffix ? findChildByType(suffix, 'simple_identifier') : null;
      const hasCallSuffix = suffix ? findChildByType(suffix, 'call_suffix') !== null : false;
      // Always try object as iterable + property as method first (handles data.values, data.keys).
      // For bare property access without call_suffix, also save property as fallback
      // (handles this.users, repo.items where the property IS the iterable).
      if (obj?.type === 'simple_identifier') iterableName = obj.text;
      if (prop) methodName = prop.text;
      if (!hasCallSuffix && prop) {
        fallbackIterableName = prop.text;
      }
      break;
    }
    if (child.type === 'call_expression') {
      // data.values() → call_expression > navigation_expression > simple_identifier + navigation_suffix
      const callee = child.firstNamedChild;
      if (callee?.type === 'navigation_expression') {
        const obj = callee.firstNamedChild;
        if (obj?.type === 'simple_identifier') iterableName = obj.text;
        const suffix = findChildByType(callee, 'navigation_suffix');
        if (suffix) {
          const prop = findChildByType(suffix, 'simple_identifier');
          if (prop) methodName = prop.text;
        }
      } else if (callee?.type === 'simple_identifier') {
        // Direct function call: for (u in getUsers())
        const rawReturn = returnTypeLookup.lookupRawReturnType(callee.text);
        if (rawReturn) callExprElementType = extractElementTypeFromString(rawReturn);
      }
      break;
    }
  }
  if (!iterableName && !callExprElementType) return;

  let elementType: string | undefined;
  if (callExprElementType) {
    elementType = callExprElementType;
  } else {
    let containerTypeName = scopeEnv.get(iterableName!);
    // Fallback: if object has no type in scope, try the property as the iterable name.
    // Handles patterns like this.users where the property itself is the iterable variable.
    if (!containerTypeName && fallbackIterableName) {
      iterableName = fallbackIterableName;
      methodName = undefined;
      containerTypeName = scopeEnv.get(iterableName);
    }
    const typeArgPos = methodToTypeArgPosition(methodName, containerTypeName);
    elementType = resolveIterableElementType(
      iterableName!, node, scopeEnv, declarationTypeNodes, scope,
      extractKotlinElementTypeFromTypeNode, findKotlinParamElementType,
      typeArgPos,
    );
  }
  if (elementType) scopeEnv.set(varName, elementType);
};

/** Kotlin: val alias = u → property_declaration or variable_declaration.
 *  property_declaration has: binding_pattern_kind("val"), variable_declaration("alias"),
 *  "=", and the RHS value (simple_identifier "u").
 *  variable_declaration appears directly inside functions and has simple_identifier children. */
const extractKotlinPendingAssignment: PendingAssignmentExtractor = (node, scopeEnv) => {
  if (node.type === 'property_declaration') {
    // Find the variable name from variable_declaration child
    const varDecl = findChildByType(node, 'variable_declaration');
    if (!varDecl) return undefined;
    const nameNode = varDecl.firstNamedChild;
    if (!nameNode || nameNode.type !== 'simple_identifier') return undefined;
    const lhs = nameNode.text;
    if (scopeEnv.has(lhs)) return undefined;
    // Find the RHS: a simple_identifier sibling after the "=" token
    let foundEq = false;
    for (let i = 0; i < node.childCount; i++) {
      const child = node.child(i);
      if (!child) continue;
      if (child.type === '=') { foundEq = true; continue; }
      if (foundEq && child.type === 'simple_identifier') {
        return { kind: 'copy', lhs, rhs: child.text };
      }
    }
    return undefined;
  }

  if (node.type === 'variable_declaration') {
    // variable_declaration directly inside functions: simple_identifier children
    const nameNode = findChildByType(node, 'simple_identifier');
    if (!nameNode) return undefined;
    const lhs = nameNode.text;
    if (scopeEnv.has(lhs)) return undefined;
    // Look for RHS simple_identifier after "=" in the parent (property_declaration)
    // variable_declaration itself doesn't contain "=" — it's in the parent
    const parent = node.parent;
    if (!parent) return undefined;
    let foundEq = false;
    for (let i = 0; i < parent.childCount; i++) {
      const child = parent.child(i);
      if (!child) continue;
      if (child.type === '=') { foundEq = true; continue; }
      if (foundEq && child.type === 'simple_identifier') {
        return { kind: 'copy', lhs, rhs: child.text };
      }
    }
    return undefined;
  }

  return undefined;
};

/** Walk up from a node to find an ancestor of a given type. */
const findAncestorByType = (node: SyntaxNode, type: string): SyntaxNode | undefined => {
  let current = node.parent;
  while (current) {
    if (current.type === type) return current;
    current = current.parent;
  }
  return undefined;
};

const extractKotlinPatternBinding: PatternBindingExtractor = (node) => {
  if (node.type !== 'type_test') return undefined;
  const typeNode = node.lastNamedChild;
  if (!typeNode) return undefined;
  const typeName = extractSimpleTypeName(typeNode);
  if (!typeName) return undefined;
  const whenExpr = findAncestorByType(node, 'when_expression');
  if (!whenExpr) return undefined;
  const whenSubject = whenExpr.namedChild(0);
  const subject = whenSubject?.firstNamedChild ?? whenSubject;
  if (!subject) return undefined;
  const varName = extractVarName(subject);
  if (!varName) return undefined;
  return { varName, typeName };
};

export const kotlinTypeConfig: LanguageTypeConfig = {
  allowPatternBindingOverwrite: true,
  declarationNodeTypes: KOTLIN_DECLARATION_NODE_TYPES,
  forLoopNodeTypes: KOTLIN_FOR_LOOP_NODE_TYPES,
  patternBindingNodeTypes: new Set(['type_test']),
  extractDeclaration: extractKotlinDeclaration,
  extractParameter: extractKotlinParameter,
  extractInitializer: extractKotlinInitializer,
  scanConstructorBinding: scanKotlinConstructorBinding,
  extractForLoopBinding: extractKotlinForLoopBinding,
  extractPendingAssignment: extractKotlinPendingAssignment,
  extractPatternBinding: extractKotlinPatternBinding,
};
