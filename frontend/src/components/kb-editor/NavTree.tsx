import { useState, useEffect, useRef } from 'react'
import {
  DndContext,
  DragOverlay,
  PointerSensor,
  useSensor,
  useSensors,
  closestCenter,
} from '@dnd-kit/core'
import type { DragEndEvent, DragStartEvent, DragMoveEvent } from '@dnd-kit/core'
import { SortableContext, verticalListSortingStrategy } from '@dnd-kit/sortable'
import {
  INDENT_WIDTH,
  getProjection,
  buildTree,
} from '@/lib/kb-editor/tree-utils'
import type { NavNode, FlatNode, Projection } from '@/lib/kb-editor/tree-utils'
import { useTreeNavigation } from '@/lib/kb-editor/useTreeNavigation'
import { SortableNavItem } from './SortableNavItem'
import { NavItemOverlay } from './NavItemOverlay'

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
  const [projection, setProjection] = useState<Projection | null>(null)
  const deltaXRef = useRef(0)
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
    setProjection(null)
    deltaXRef.current = 0
  }

  function handleDragMove(event: DragMoveEvent) {
    deltaXRef.current = event.delta.x
    const { active, over } = event
    if (!over || active.id === over.id) {
      setProjection(null)
      return
    }
    setProjection(
      getProjection(flatNodes, active.id as string, over.id as string, event.delta.x, pointerYRef.current)
    )
  }

  function handleDragEnd(event: DragEndEvent) {
    const { active, over } = event
    setActiveId(null)
    setProjection(null)
    deltaXRef.current = 0

    if (!over || active.id === over.id) return

    const proj = getProjection(
      flatNodes,
      active.id as string,
      over.id as string,
      deltaXRef.current,
      pointerYRef.current,
    )

    const newTree = buildTree(flatNodes, proj, active.id as string)
    onSidebarUpdate(newTree)
  }

  const activeFlat = activeId ? flatNodes.find((f) => f.id === activeId) : null
  const projectedDepth = projection?.depth ?? activeFlat?.depth ?? 0

  return (
    <DndContext
      sensors={sensors}
      collisionDetection={closestCenter}
      onDragStart={handleDragStart}
      onDragMove={handleDragMove}
      onDragEnd={handleDragEnd}
    >
      <SortableContext
        items={flatNodes.map((f) => f.id)}
        strategy={verticalListSortingStrategy}
      >
        <div className="relative">
          {flatNodes.map((flat, index) => {
            const showDropLineAbove =
              projection !== null &&
              activeId !== null &&
              projection.newIndex === index &&
              flat.id !== activeId

            const isLast = index === flatNodes.length - 1
            const showDropLineBelow =
              isLast &&
              projection !== null &&
              activeId !== null &&
              projection.newIndex >= flatNodes.length

            return (
              <div key={flat.id}>
                {showDropLineAbove && (
                  <div
                    style={{
                      height: '2px',
                      background: 'var(--color-purple-accent)',
                      marginLeft: `${projection.depth * INDENT_WIDTH + 8}px`,
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
                  flatNodes={flatNodes}
                  isDraggingActive={!!activeId}
                />
                {showDropLineBelow && (
                  <div
                    style={{
                      height: '2px',
                      background: 'var(--color-purple-accent)',
                      marginLeft: `${projection.depth * INDENT_WIDTH + 8}px`,
                      marginRight: '8px',
                      borderRadius: '1px',
                    }}
                  />
                )}
              </div>
            )
          })}
        </div>
      </SortableContext>
      <DragOverlay>
        {activeFlat && (
          <NavItemOverlay
            node={activeFlat.node}
            projectedDepth={projectedDepth}
            activeTitle={activeTitle}
            activePath={activePath}
          />
        )}
      </DragOverlay>
    </DndContext>
  )
}
