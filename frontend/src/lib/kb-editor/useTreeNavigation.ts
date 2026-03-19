import { useState, useMemo, useCallback } from 'react'
import { flattenTree } from './tree-utils'
import type { NavNode, FlatNode } from './tree-utils'

export function useTreeNavigation(nodes: NavNode[]) {
  const [collapsedIds, setCollapsedIds] = useState<Set<string>>(new Set())

  const flatNodes: FlatNode[] = useMemo(
    () => flattenTree(nodes, collapsedIds),
    [nodes, collapsedIds],
  )

  const toggleCollapse = useCallback((id: string) => {
    setCollapsedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  return { collapsedIds, flatNodes, toggleCollapse }
}
