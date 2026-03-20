import { useState, useEffect, useRef } from 'react'
import {
  DndContext,
  DragOverlay,
  PointerSensor,
  useSensor,
  useSensors,
  pointerWithin,
  closestCenter,
} from '@dnd-kit/core'
import type { DragEndEvent, DragStartEvent, DragMoveEvent, CollisionDetection } from '@dnd-kit/core'
import { INDENT_WIDTH, getDropTarget, applyDrop } from '@/lib/kb-editor/tree-utils'
import type { NavNode, DropTarget, DropIntent } from '@/lib/kb-editor/tree-utils'
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
  const pointerYRef = useRef<number>(0)

  useEffect(() => {
    const onMove = (e: MouseEvent) => { pointerYRef.current = e.clientY }
    window.addEventListener('mousemove', onMove)
    return () => window.removeEventListener('mousemove', onMove)
  }, [])

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
      getDropTarget(flatNodes, active.id as string, over.id as string, pointerYRef.current)
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

  // Projected depth for the drag overlay ghost
  let overlayDepth = activeFlat?.depth ?? 0
  if (dropTarget) {
    const targetFlat = flatNodes.find((f) => f.id === dropTarget.targetId)
    if (targetFlat) {
      overlayDepth = dropTarget.intent === 'inside' ? targetFlat.depth + 1 : targetFlat.depth
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
        {flatNodes.map((flat) => {
          const isTarget = dropTarget?.targetId === flat.id
          const intent: DropIntent | null = isTarget ? dropTarget!.intent : null
          const lineDepth = flat.depth

          return (
            <div key={flat.id}>
              {intent === 'before' && (
                <div
                  style={{
                    height: '2px',
                    background: 'var(--color-purple-accent)',
                    marginLeft: `${lineDepth * INDENT_WIDTH + 8}px`,
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
                dropIntent={intent}
                nodes={nodes}
              />
              {intent === 'after' && (
                <div
                  style={{
                    height: '2px',
                    background: 'var(--color-purple-accent)',
                    marginLeft: `${lineDepth * INDENT_WIDTH + 8}px`,
                    marginRight: '8px',
                    borderRadius: '1px',
                  }}
                />
              )}
            </div>
          )
        })}
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
