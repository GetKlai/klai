// ─── KB Editor: tree utilities ───────────────────────────────────────────────
// Pure functions and shared interfaces for the knowledge-base editor sidebar.
// Nothing here imports React — all functions are independently testable.

export const DOCS_BASE = '/api/docs/api'
export const DEFAULT_ICON = '📄'
export const INDENT_WIDTH = 24

export function getOrgSlug(): string {
  return window.location.hostname.split('.')[0]
}

export function stripMdExt(path: string): string {
  return path.replace(/\.md$/, '')
}

export function slugify(title: string): string {
  return (
    title
      .toLowerCase()
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '')
      .replace(/[^a-z0-9\s-]/g, '')
      .trim()
      .replace(/\s+/g, '-')
      .replace(/-+/g, '-') || 'untitled'
  )
}

// ─── Shared types ─────────────────────────────────────────────────────────────

export interface NavNode {
  slug: string
  title: string
  icon?: string
  path: string
  type: 'file' | 'dir'
  children?: NavNode[]
}

export interface SidebarEntry {
  slug: string
  children?: SidebarEntry[]
}

export interface FlatNode {
  id: string          // node.path
  depth: number
  parentId: string | null
  node: NavNode
}

export type DropIntent = 'before' | 'inside' | 'after'

export interface DropTarget {
  targetId: string
  intent: DropIntent
}

// ─── Tree transformations ─────────────────────────────────────────────────────

// Convert NavNode[] to SidebarEntry[] (strip everything except slug + children)
export function navToSidebarEntries(nodes: NavNode[]): SidebarEntry[] {
  return nodes.map((n) => ({
    slug: stripMdExt(n.path),
    ...(n.children?.length ? { children: navToSidebarEntries(n.children) } : {}),
  }))
}

// Add a new child slug under the node matching parentPath
export function addChildToNode(nodes: NavNode[], parentPath: string, newSlug: string): NavNode[] {
  return nodes.map((node) => {
    if (stripMdExt(node.path) === parentPath) {
      return {
        ...node,
        children: [
          ...(node.children ?? []),
          { slug: newSlug, title: newSlug, path: `${newSlug}.md`, type: 'file' as const, icon: DEFAULT_ICON },
        ],
      }
    }
    if (node.children) {
      return { ...node, children: addChildToNode(node.children, parentPath, newSlug) }
    }
    return node
  })
}

// Collect all page slugs in a tree (recursive)
export function collectSlugs(nodes: NavNode[]): Set<string> {
  const set = new Set<string>()
  const visit = (ns: NavNode[]) => {
    for (const n of ns) {
      set.add(stripMdExt(n.path))
      if (n.children) visit(n.children)
    }
  }
  visit(nodes)
  return set
}

// ─── Flat-list utilities ────────────────────────────────────────────────────

export function flattenTree(
  nodes: NavNode[],
  collapsed: Set<string>,
  depth = 0,
  parentId: string | null = null,
): FlatNode[] {
  const result: FlatNode[] = []
  for (const node of nodes) {
    result.push({ id: node.path, depth, parentId, node })
    if (node.children?.length && !collapsed.has(node.path)) {
      result.push(...flattenTree(node.children, collapsed, depth + 1, node.path))
    }
  }
  return result
}

// ─── Drag-and-drop: three-zone detection ────────────────────────────────────

/**
 * Industry-standard 25/50/25 zone split for tree drag-and-drop:
 * - Top 25%    = "before" (insert as sibling above)
 * - Middle 50% = "inside" (insert as child)
 * - Bottom 25% = "after"  (insert as sibling below)
 *
 * For "after" drops on nested items, the pointer X position determines the
 * depth: cursor left = shallower (root), cursor right = same level as target.
 * This is the same pattern used by react-arborist and VS Code.
 */
export function getDropTarget(
  flatNodes: FlatNode[],
  activeId: string,
  overId: string,
  pointerX: number,
  pointerY: number,
): DropTarget | null {
  // Sentinel: drop at end of root level
  if (overId === '__root-end__') {
    return { targetId: '__root-end__', intent: 'after' }
  }

  if (activeId === overId) return null

  const activeIdx = flatNodes.findIndex((f) => f.id === activeId)
  const overIdx = flatNodes.findIndex((f) => f.id === overId)
  if (activeIdx === -1 || overIdx === -1) return null

  // Prevent dropping on a descendant of the dragged item
  if (isDescendantInFlat(flatNodes, activeIdx, overIdx)) return null

  // Get the DOM rect of the hovered item for zone calculation
  const overEl = document.querySelector(`[data-flat-id="${overId}"]`)
  if (!overEl) return null
  const rect = overEl.getBoundingClientRect()
  const relY = pointerY - rect.top
  const quarter = rect.height / 4

  let intent: DropIntent
  if (relY < quarter) intent = 'before'
  else if (relY > rect.height - quarter) intent = 'after'
  else intent = 'inside'

  const overItem = flatNodes[overIdx]

  // For "after" on nested items, use pointer X to determine the drop depth.
  // Moving the cursor to the left means the user wants a shallower level.
  if (intent === 'after' && overItem.depth > 0) {
    const relX = pointerX - rect.left
    const cursorDepth = Math.max(0, Math.round((relX - 8) / INDENT_WIDTH))
    const clampedDepth = Math.min(cursorDepth, overItem.depth)

    if (clampedDepth < overItem.depth) {
      // Walk backward in the flat list to find the ancestor at the desired depth
      for (let i = overIdx; i >= 0; i--) {
        if (flatNodes[i].id === activeId) continue
        if (flatNodes[i].depth === clampedDepth) {
          return { targetId: flatNodes[i].id, intent: 'after' }
        }
        if (flatNodes[i].depth < clampedDepth) break
      }
    }
  }

  return { targetId: overId, intent }
}

function isDescendantInFlat(
  flatNodes: FlatNode[],
  ancestorIdx: number,
  candidateIdx: number,
): boolean {
  if (candidateIdx <= ancestorIdx) return false
  const ancestorDepth = flatNodes[ancestorIdx].depth
  for (let i = ancestorIdx + 1; i <= candidateIdx; i++) {
    if (flatNodes[i].depth <= ancestorDepth) return false
  }
  return true
}

// ─── Tree mutations (work directly on the tree, preserving collapsed children) ─

/** Apply a drag-and-drop operation directly on the tree structure. */
export function applyDrop(tree: NavNode[], activeId: string, target: DropTarget): NavNode[] {
  // Sentinel: move to end of root level
  if (target.targetId === '__root-end__') {
    return moveToRoot(tree, activeId)
  }
  const { tree: treeWithout, removed } = removeFromTree(tree, activeId)
  if (!removed) return tree
  const { result, found } = findAndInsert(treeWithout, removed, target.targetId, target.intent)
  return found ? result : tree
}

/** Move a node to the end of the root level. */
export function moveToRoot(tree: NavNode[], nodeId: string): NavNode[] {
  const { tree: treeWithout, removed } = removeFromTree(tree, nodeId)
  if (!removed) return tree
  return [...treeWithout, removed]
}

/** Promote a node one level up (insert after its current parent). */
export function promoteNode(tree: NavNode[], nodeId: string): NavNode[] {
  const parentPath = findParentPath(tree, nodeId)
  if (parentPath === null) return tree // already at root
  const { tree: treeWithout, removed } = removeFromTree(tree, nodeId)
  if (!removed) return tree
  const { result, found } = findAndInsert(treeWithout, removed, parentPath, 'after')
  return found ? result : tree
}

// ─── Internal helpers ───────────────────────────────────────────────────────

function removeFromTree(
  nodes: NavNode[],
  nodeId: string,
): { tree: NavNode[]; removed: NavNode | null } {
  let removed: NavNode | null = null
  const result: NavNode[] = []

  for (const node of nodes) {
    if (node.path === nodeId) {
      removed = node
      continue
    }
    if (node.children?.length) {
      const sub = removeFromTree(node.children, nodeId)
      if (sub.removed) removed = sub.removed
      result.push({ ...node, children: sub.tree.length ? sub.tree : undefined })
    } else {
      result.push(node)
    }
  }

  return { tree: result, removed }
}

function findAndInsert(
  nodes: NavNode[],
  item: NavNode,
  targetId: string,
  intent: DropIntent,
): { result: NavNode[]; found: boolean } {
  const idx = nodes.findIndex((n) => n.path === targetId)

  if (idx !== -1) {
    const result = [...nodes]
    switch (intent) {
      case 'before':
        result.splice(idx, 0, item)
        break
      case 'after':
        result.splice(idx + 1, 0, item)
        break
      case 'inside': {
        const target = result[idx]
        result[idx] = { ...target, children: [...(target.children ?? []), item] }
        break
      }
    }
    return { result, found: true }
  }

  // Recurse into children
  for (let i = 0; i < nodes.length; i++) {
    const node = nodes[i]
    if (!node.children?.length) continue
    const sub = findAndInsert(node.children, item, targetId, intent)
    if (sub.found) {
      const result = [...nodes]
      result[i] = { ...node, children: sub.result }
      return { result, found: true }
    }
  }

  return { result: nodes, found: false }
}

function findParentPath(nodes: NavNode[], nodeId: string): string | null {
  for (const node of nodes) {
    if (node.children?.some((c) => c.path === nodeId)) return node.path
    if (node.children?.length) {
      const found = findParentPath(node.children, nodeId)
      if (found !== null) return found
    }
  }
  return null
}
