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

  const expandNode = useCallback((id: string) => {
    setCollapsedIds((prev) => {
      if (!prev.has(id)) return prev
      const next = new Set(prev)
      next.delete(id)
      return next
    })
  }, [])

  const collapseNode = useCallback((id: string) => {
    setCollapsedIds((prev) => {
      if (prev.has(id)) return prev
      const next = new Set(prev)
      next.add(id)
      return next
    })
  }, [])

  return { collapsedIds, flatNodes, toggleCollapse, expandNode, collapseNode }
}
