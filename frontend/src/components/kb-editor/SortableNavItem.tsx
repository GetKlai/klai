import { useState, useEffect, useRef } from 'react'
import { ChevronRight, FolderOpen, Plus, Check, X, MoreHorizontal, GripVertical } from 'lucide-react'
import { Input } from '@/components/ui/input'
import { useSortable } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import * as m from '@/paraglide/messages'
import {
  INDENT_WIDTH,
  DEFAULT_ICON,
  buildTree,
} from '@/lib/kb-editor/tree-utils'
import type { NavNode, FlatNode, Projection } from '@/lib/kb-editor/tree-utils'

export interface SortableNavItemProps {
  flat: FlatNode
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
  isCollapsed: boolean
  onToggleCollapse: (id: string) => void
  flatNodes: FlatNode[]
  isDraggingActive: boolean
}

export function SortableNavItem({
  flat,
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
  isCollapsed,
  onToggleCollapse,
  flatNodes,
  isDraggingActive,
}: SortableNavItemProps) {
  const { node, depth } = flat
  const [hovered, setHovered] = useState(false)
  const [showContextMenu, setShowContextMenu] = useState(false)
  const contextMenuRef = useRef<HTMLDivElement>(null)

  const hasChildren = !!(node.children && node.children.length > 0)
  const isDir = node.type === 'dir'
  const isSelected = selectedPath === node.path.replace(/\.md$/, '')
  const nodePath = node.path.replace(/\.md$/, '')
  const isAddingSubpageHere = addingSubpageUnder === nodePath

  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: node.path })

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0 : 1,
  }

  // Close context menu on outside click
  useEffect(() => {
    if (!showContextMenu) return
    const handler = (e: MouseEvent) => {
      if (contextMenuRef.current && !contextMenuRef.current.contains(e.target as Node)) {
        setShowContextMenu(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [showContextMenu])

  const paddingLeft = depth * INDENT_WIDTH + 8

  const displayTitle =
    activePath && activePath === node.path.replace(/\.md$/, '')
      ? (activeTitle ?? node.title)
      : node.title

  function handleMoveToRoot() {
    setShowContextMenu(false)
    const proj: Projection = { depth: 0, parentId: null, newIndex: flatNodes.length - 1 }
    const newTree = buildTree(flatNodes, proj, node.path)
    onSidebarUpdate(newTree)
  }

  function handlePromote() {
    setShowContextMenu(false)
    if (flat.depth === 0) return
    const currentIndex = flatNodes.findIndex((f) => f.id === node.path)
    let newParentId: string | null = null
    if (flat.depth >= 2) {
      for (let i = currentIndex - 1; i >= 0; i--) {
        if (flatNodes[i].depth === flat.depth - 2) {
          newParentId = flatNodes[i].id
          break
        }
      }
    }
    const proj: Projection = {
      depth: flat.depth - 1,
      parentId: newParentId,
      newIndex: currentIndex,
    }
    const newTree = buildTree(flatNodes, proj, node.path)
    onSidebarUpdate(newTree)
  }

  const subpageInput = isAddingSubpageHere && (
    <div className="py-1 pr-2" style={{ paddingLeft: `${paddingLeft + 12}px` }}>
      <div className="flex gap-1 items-center">
        <Input
          value={newPageTitle}
          onChange={(e) => onNewPageTitleChange(e.target.value)}
          placeholder={m.docs_editor_title_placeholder()}
          className="h-6 text-xs flex-1"
          autoFocus
          onKeyDown={(e) => {
            if (e.key === 'Enter') onNewPageConfirm(nodePath)
            if (e.key === 'Escape') onNewPageCancel()
          }}
        />
        <button
          type="button"
          className="shrink-0 text-[var(--color-purple-deep)] hover:opacity-70 disabled:opacity-30"
          disabled={!newPageTitle.trim()}
          onClick={() => onNewPageConfirm(nodePath)}
          aria-label={m.docs_kb_create()}
        >
          <Check size={12} />
        </button>
        <button
          type="button"
          className="shrink-0 text-[var(--color-muted-foreground)] hover:opacity-70"
          onClick={onNewPageCancel}
          aria-label="Annuleren"
        >
          <X size={12} />
        </button>
      </div>
    </div>
  )

  return (
    <div ref={setNodeRef} style={style} data-flat-id={flat.id}>
      <div
        className={`flex w-full items-center py-1 text-xs transition-colors group ${
          isSelected && !isDir
            ? 'bg-[var(--color-purple-accent)]/10 text-[var(--color-purple-deep)] font-medium'
            : 'text-[var(--color-foreground)] hover:bg-[var(--color-muted-foreground)]/5'
        }`}
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
      >
        <span style={{ width: `${paddingLeft}px`, flexShrink: 0 }} />

        <div className="relative w-5 h-5 shrink-0 flex items-center justify-center mr-1">
          {hasChildren ? (
            <>
              {isDir
                ? <FolderOpen size={13} className="shrink-0 transition-opacity group-hover:opacity-0" />
                : <span className="text-sm leading-none transition-opacity group-hover:opacity-0 select-none">{node.icon ?? DEFAULT_ICON}</span>
              }
              <button
                className="absolute inset-0 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity"
                onClick={(e) => { e.stopPropagation(); onToggleCollapse(node.path) }}
                tabIndex={-1}
                aria-label={isCollapsed ? 'Uitklappen' : 'Inklappen'}
                type="button"
              >
                <ChevronRight
                  size={12}
                  className={`text-[var(--color-muted-foreground)] transition-transform ${isCollapsed ? '' : 'rotate-90'}`}
                />
              </button>
            </>
          ) : (
            isDir
              ? <FolderOpen size={13} className="shrink-0 text-[var(--color-muted-foreground)]" />
              : <span className="shrink-0 text-sm leading-none select-none">{node.icon ?? DEFAULT_ICON}</span>
          )}
        </div>

        <button
          className={`flex flex-1 items-center min-w-0 text-left ${
            isDir
              ? 'font-medium text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)]'
              : isSelected
                ? 'text-[var(--color-purple-deep)] font-medium'
                : 'text-[var(--color-foreground)]'
          }`}
          onClick={() => { if (!isDir) onSelect(node) }}
          disabled={isDir && !hasChildren}
        >
          <span className="truncate">{displayTitle}</span>
        </button>

        <div className="flex items-center gap-0.5 mr-1.5 shrink-0">
          {(hovered || showContextMenu) && !isDraggingActive && (
            <>
              <button
                type="button"
                className="flex items-center justify-center w-4 h-4 rounded hover:bg-[var(--color-muted-foreground)]/15 text-[var(--color-muted-foreground)] hover:text-[var(--color-purple-deep)]"
                onClick={() => onAddSubpage(nodePath)}
                title={m.docs_pages_add_subpage()}
                aria-label={m.docs_pages_add_subpage()}
              >
                <Plus size={10} />
              </button>
              <div className="relative" ref={contextMenuRef}>
                <button
                  type="button"
                  className="flex items-center justify-center w-4 h-4 rounded hover:bg-[var(--color-muted-foreground)]/15 text-[var(--color-muted-foreground)] hover:text-[var(--color-purple-deep)]"
                  onClick={() => setShowContextMenu((v) => !v)}
                  aria-label="Meer opties"
                >
                  <MoreHorizontal size={10} />
                </button>
                {showContextMenu && (
                  <div className="absolute right-0 top-5 z-20 w-44 rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] shadow-md py-1">
                    {flat.depth > 0 && (
                      <button
                        className="w-full text-left px-3 py-1.5 text-xs text-[var(--color-foreground)] hover:bg-[var(--color-secondary)]"
                        onClick={handlePromote}
                      >
                        {m.docs_tree_promote()}
                      </button>
                    )}
                    {flat.depth > 0 && (
                      <button
                        className="w-full text-left px-3 py-1.5 text-xs text-[var(--color-foreground)] hover:bg-[var(--color-secondary)]"
                        onClick={handleMoveToRoot}
                      >
                        {m.docs_tree_move_to_root()}
                      </button>
                    )}
                    {flat.depth === 0 && (
                      <p className="px-3 py-1.5 text-xs text-[var(--color-muted-foreground)] italic">
                        Al op rootniveau
                      </p>
                    )}
                  </div>
                )}
              </div>
            </>
          )}
          <span className="cursor-grab touch-none" {...attributes} {...listeners}>
            <GripVertical size={12} className="text-[var(--color-muted-foreground)] opacity-40 flex-shrink-0" />
          </span>
        </div>
      </div>
      {subpageInput}
    </div>
  )
}
