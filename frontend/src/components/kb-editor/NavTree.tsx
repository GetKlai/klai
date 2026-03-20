import { useState, useEffect, useRef } from 'react'
import {
  DndContext,
  DragOverlay,
  PointerSensor,
  useSensor,
  useSensors,
  pointerWithin,
  closestCenter,
  useDroppable,
} from '@dnd-kit/core'
import type { DragEndEvent, DragStartEvent, DragMoveEvent, CollisionDetection } from '@dnd-kit/core'
import { INDENT_WIDTH, getDropTarget, applyDrop } from '@/lib/kb-editor/tree-utils'
import type { NavNode, FlatNode, DropTarget, DropIntent } from '@/lib/kb-editor/tree-utils'
import { useTreeNavigation } from '@/lib/kb-editor/useTreeNavigation'
import { SortableNavItem } from './SortableNavItem'
import { NavItemOverlay } from './NavItemOverlay'

// Prefer pointerWithin (item the pointer is literally in), fall back to closestCenter
const treeCollisionDetection: CollisionDetection = (args) => {
  const pw = pointerWithin(args)
  if (pw.length > 0) return pw
  return closestCenter(args)
}

export interface NavTreeProps {
  nodes: NavNode[]
  selectedPath: string | null
  onSelect: (node: NavNode) => void
  activeTitle?: string
  activePath?: string | null
  onSidebarUpdate: (newTree: NavNode[]) => void
  onAddSubpage: (parentPath: string) => void
  addingSubpageUnder: string | null
  newPageTitle: string
  onNewPageTitleChange: (val: string) => void
  onNewPageConfirm: (parentPath: string) => void
  onNewPageCancel: () => void
}

export function NavTree({
  nodes,
  selectedPath,
  onSelect,
  activeTitle,
  activePath,
  onSidebarUpdate,
  onAddSubpage,
  addingSubpageUnder,
  newPageTitle,
  onNewPageTitleChange,
  onNewPageConfirm,
  onNewPageCancel,
}: NavTreeProps) {
  const { collapsedIds, flatNodes, toggleCollapse } = useTreeNavigation(nodes)
  const [activeId, setActiveId] = useState<string | null>(null)
  const [dropTarget, setDropTarget] = useState<DropTarget | null>(null)
  const pointerRef = useRef({ x: 0, y: 0 })

  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      pointerRef.current = { x: e.clientX, y: e.clientY }
    }
    window.addEventListener('mousemove', onMove)
    return () => window.removeEventListener('mousemove', onMove)
  }, [])

  // Sentinel droppable at the bottom of the tree -- catches drops below all items
  const { setNodeRef: setSentinelRef } = useDroppable({ id: '__root-end__' })

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } })
  )

  function handleDragStart(event: DragStartEvent) {
    setActiveId(event.active.id as string)
    setDropTarget(null)
  }

  function handleDragMove(event: DragMoveEvent) {
    const { active, over } = event
    if (!over || !activeId) {
      setDropTarget(null)
      return
    }
    setDropTarget(
      getDropTarget(flatNodes, active.id as string, over.id as string, pointerRef.current.x, pointerRef.current.y)
    )
  }

  function handleDragEnd(_event: DragEndEvent) {
    const target = dropTarget
    const draggedId = activeId
    setActiveId(null)
    setDropTarget(null)

    if (!target || !draggedId) return
    const newTree = applyDrop(nodes, draggedId, target)
    onSidebarUpdate(newTree)
  }

  const activeFlat = activeId ? flatNodes.find((f) => f.id === activeId) : null

  // Compute visual drop indicator position
  // For "after" drops, the line must appear after the LAST VISIBLE DESCENDANT
  // of the target, not directly after the target itself.
  let dropLineBefore: string | null = null
  let dropLineAfter: string | null = null
  let dropHighlight: string | null = null
  let dropLineDepth = 0

  if (dropTarget && activeId) {
    if (dropTarget.targetId === '__root-end__') {
      // Sentinel: show line at the very bottom at root depth
      const lastVisible = findLastVisible(flatNodes, activeId)
      if (lastVisible) {
        dropLineAfter = lastVisible.id
        dropLineDepth = 0
      }
    } else {
      const targetIdx = flatNodes.findIndex((f) => f.id === dropTarget.targetId)
      if (targetIdx !== -1) {
        const targetFlat = flatNodes[targetIdx]

        switch (dropTarget.intent) {
          case 'before':
            dropLineBefore = dropTarget.targetId
            dropLineDepth = targetFlat.depth
            break
          case 'inside':
            dropHighlight = dropTarget.targetId
            break
          case 'after': {
            // Find last visible descendant of the target
            let lastDescId = dropTarget.targetId
            for (let i = targetIdx + 1; i < flatNodes.length; i++) {
              if (flatNodes[i].id === activeId) continue
              if (flatNodes[i].depth <= targetFlat.depth) break
              lastDescId = flatNodes[i].id
            }
            dropLineAfter = lastDescId
            dropLineDepth = targetFlat.depth
            break
          }
        }
      }
    }
  }

  // Projected depth for the drag overlay ghost
  let overlayDepth = activeFlat?.depth ?? 0
  if (dropTarget) {
    if (dropTarget.targetId === '__root-end__') {
      overlayDepth = 0
    } else {
      const targetFlat = flatNodes.find((f) => f.id === dropTarget.targetId)
      if (targetFlat) {
        overlayDepth = dropTarget.intent === 'inside' ? targetFlat.depth + 1 : targetFlat.depth
      }
    }
  }

  return (
    <DndContext
      sensors={sensors}
      collisionDetection={treeCollisionDetection}
      onDragStart={handleDragStart}
      onDragMove={handleDragMove}
      onDragEnd={handleDragEnd}
    >
      <div className="relative">
        {flatNodes.map((flat) => (
          <div key={flat.id}>
            {dropLineBefore === flat.id && (
              <div
                style={{
                  height: '2px',
                  background: 'var(--color-purple-accent)',
                  marginLeft: `${dropLineDepth * INDENT_WIDTH + 8}px`,
                  marginRight: '8px',
                  borderRadius: '1px',
                }}
              />
            )}
            <SortableNavItem
              flat={flat}
              selectedPath={selectedPath}
              onSelect={onSelect}
              activeTitle={activeTitle}
              activePath={activePath}
              onSidebarUpdate={onSidebarUpdate}
              onAddSubpage={onAddSubpage}
              addingSubpageUnder={addingSubpageUnder}
              newPageTitle={newPageTitle}
              onNewPageTitleChange={onNewPageTitleChange}
              onNewPageConfirm={onNewPageConfirm}
              onNewPageCancel={onNewPageCancel}
              isCollapsed={collapsedIds.has(flat.id)}
              onToggleCollapse={toggleCollapse}
              isDraggingActive={!!activeId}
              dropIntent={dropHighlight === flat.id ? 'inside' : null}
              nodes={nodes}
            />
            {dropLineAfter === flat.id && (
              <div
                style={{
                  height: '2px',
                  background: 'var(--color-purple-accent)',
                  marginLeft: `${dropLineDepth * INDENT_WIDTH + 8}px`,
                  marginRight: '8px',
                  borderRadius: '1px',
                }}
              />
            )}
          </div>
        ))}
        {/* Sentinel: invisible droppable below all items, catches drops below the tree */}
        <div ref={setSentinelRef} data-flat-id="__root-end__" style={{ minHeight: '24px' }} />
      </div>
      <DragOverlay>
        {activeFlat && (
          <NavItemOverlay
            node={activeFlat.node}
            projectedDepth={overlayDepth}
            activeTitle={activeTitle}
            activePath={activePath}
          />
        )}
      </DragOverlay>
    </DndContext>
  )
}

/** Find the last flat node that is not the active (dragged) item. */
function findLastVisible(flatNodes: FlatNode[], activeId: string): FlatNode | null {
  for (let i = flatNodes.length - 1; i >= 0; i--) {
    if (flatNodes[i].id !== activeId) return flatNodes[i]
  }
  return null
}
