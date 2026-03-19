// ─── KB Editor: tree utilities ───────────────────────────────────────────────
// Pure functions and shared interfaces for the knowledge-base editor sidebar.
// Nothing here imports React — all functions are independently testable.

export const DOCS_BASE = '/docs/api'
export const DEFAULT_ICON = '📄'
export const INDENT_WIDTH = 12

export function getOrgSlug(): string {
  return window.location.hostname.split('.')[0]
}

export function slugify(title: string): string {
  return (
    title
      .toLowerCase()
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

export interface Projection {
  depth: number
  parentId: string | null
  newIndex: number
}

// ─── Tree transformations ─────────────────────────────────────────────────────

// Convert NavNode[] to SidebarEntry[] (strip everything except slug + children)
export function navToSidebarEntries(nodes: NavNode[]): SidebarEntry[] {
  return nodes.map((n) => ({
    slug: n.path.replace(/\.md$/, ''),
    ...(n.children?.length ? { children: navToSidebarEntries(n.children) } : {}),
  }))
}

// Add a new child slug under the node matching parentPath
export function addChildToNode(nodes: NavNode[], parentPath: string, newSlug: string): NavNode[] {
  return nodes.map((node) => {
    if (node.path.replace(/\.md$/, '') === parentPath) {
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
      set.add(n.path.replace(/\.md$/, ''))
      if (n.children) visit(n.children)
    }
  }
  visit(nodes)
  return set
}

// ─── Flat-list DnD utilities ──────────────────────────────────────────────────

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

export function getProjection(
  items: FlatNode[],
  activeId: string,
  overId: string,
  deltaX: number,
  pointerY: number,
): Projection {
  const overIndex = items.findIndex((f) => f.id === overId)
  const activeIndex = items.findIndex((f) => f.id === activeId)

  if (overIndex === -1 || activeIndex === -1) {
    const fallback = items.findIndex((f) => f.id === activeId)
    return { depth: 0, parentId: null, newIndex: fallback === -1 ? 0 : fallback }
  }

  const overItem = items[overIndex]
  const baseDepth = overItem.depth

  // Determine the maximum allowable depth (prev item depth + 1)
  // Look backwards from overIndex (skipping the active item itself)
  const itemsWithoutActive = items.filter((f) => f.id !== activeId)
  const overIndexWithoutActive = itemsWithoutActive.findIndex((f) => f.id === overId)
  const prevItem = overIndexWithoutActive > 0 ? itemsWithoutActive[overIndexWithoutActive - 1] : null
  // When there is no previous item (first in list), still allow nesting under overItem itself
  const maxDepth = prevItem ? prevItem.depth + 1 : overItem.depth + 1

  const depthOffset = Math.round(deltaX / INDENT_WIDTH)
  const projectedDepth = Math.min(Math.max(0, baseDepth + depthOffset), maxDepth)

  // Find the parentId: look backwards for the first item at projectedDepth - 1
  let parentId: string | null = null
  if (projectedDepth > 0) {
    for (let i = overIndexWithoutActive - 1; i >= 0; i--) {
      if (itemsWithoutActive[i].depth === projectedDepth - 1) {
        parentId = itemsWithoutActive[i].id
        break
      }
      if (itemsWithoutActive[i].depth < projectedDepth - 1) {
        break
      }
    }
  }

  // newIndex: position in the items-without-active list where we drop.
  // When hovering over the last item, dnd-kit cannot switch to a "next" item,
  // so we check the pointer Y against the item midpoint to decide before vs. after.
  let newIndex = overIndexWithoutActive
  const overEl = document.querySelector(`[data-flat-id="${overId}"]`)
  if (overEl) {
    const rect = overEl.getBoundingClientRect()
    const midY = rect.top + rect.height / 2
    if (pointerY > midY) {
      newIndex = overIndexWithoutActive + 1
    }
  }

  return { depth: projectedDepth, parentId, newIndex }
}

export function buildTree(flatNodes: FlatNode[], projection: Projection, activeId: string): NavNode[] {
  // Remove active node from flat list
  const withoutActive = flatNodes.filter((f) => f.id !== activeId)
  const activeFlat = flatNodes.find((f) => f.id === activeId)!

  // Insert active at newIndex with updated depth/parentId
  const inserted: FlatNode[] = [
    ...withoutActive.slice(0, projection.newIndex),
    { ...activeFlat, depth: projection.depth, parentId: projection.parentId },
    ...withoutActive.slice(projection.newIndex),
  ]

  // Rebuild hierarchical NavNode[] from the flat list
  const nodeMap = new Map<string, NavNode>()
  const roots: NavNode[] = []

  for (const flat of inserted) {
    const node: NavNode = { ...flat.node, children: [] }
    nodeMap.set(flat.id, node)

    if (flat.parentId === null) {
      roots.push(node)
    } else {
      const parent = nodeMap.get(flat.parentId)
      if (parent) {
        parent.children = parent.children ?? []
        parent.children.push(node)
      } else {
        roots.push(node)
      }
    }
  }

  // Strip empty children arrays
  function stripEmpty(nodes: NavNode[]): NavNode[] {
    return nodes.map((n) => ({
      ...n,
      children: n.children?.length ? stripEmpty(n.children) : undefined,
    }))
  }

  return stripEmpty(roots)
}
