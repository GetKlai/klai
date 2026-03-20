import { useState } from 'react'
import { ChevronRight, FolderOpen, Plus, Check, X, MoreHorizontal, GripVertical } from 'lucide-react'
import { Input } from '@/components/ui/input'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { useDraggable, useDroppable } from '@dnd-kit/core'
import * as m from '@/paraglide/messages'
import {
  INDENT_WIDTH,
  DEFAULT_ICON,
  stripMdExt,
  moveToRoot,
  promoteNode,
} from '@/lib/kb-editor/tree-utils'
import type { NavNode, FlatNode, DropIntent } from '@/lib/kb-editor/tree-utils'

export interface TreeItemProps {
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
  isDraggingActive: boolean
  dropIntent: DropIntent | null
  isFocused: boolean
  nodes: NavNode[]
}

export function TreeItem({
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
  isDraggingActive,
  dropIntent,
  isFocused,
  nodes,
}: TreeItemProps) {
  const { node, depth } = flat
  const [hovered, setHovered] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)

  const hasChildren = !!(node.children && node.children.length > 0)
  const isDir = node.type === 'dir'
  const isSelected = selectedPath === stripMdExt(node.path)
  const nodePath = stripMdExt(node.path)
  const isAddingSubpageHere = addingSubpageUnder === nodePath

  const {
    attributes,
    listeners,
    setNodeRef: setDragRef,
    isDragging,
  } = useDraggable({ id: node.path })

  const { setNodeRef: setDropRef } = useDroppable({ id: node.path, disabled: isDragging })

  // Combine drag and drop refs on the same element
  const setNodeRef = (el: HTMLDivElement | null) => {
    setDragRef(el)
    setDropRef(el)
  }

  const paddingLeft = depth * INDENT_WIDTH + 8

  const displayTitle =
    activePath && activePath === stripMdExt(node.path)
      ? (activeTitle ?? node.title)
      : node.title

  function handleMoveToRoot() {
    const newTree = moveToRoot(nodes, node.path)
    onSidebarUpdate(newTree)
  }

  function handlePromote() {
    if (flat.depth === 0) return
    const newTree = promoteNode(nodes, node.path)
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
          aria-label={m.docs_tree_cancel()}
        >
          <X size={12} />
        </button>
      </div>
    </div>
  )

  // Visual highlight for "drop inside" intent
  const isInsideTarget = dropIntent === 'inside'

  return (
    <div
      ref={setNodeRef}
      data-flat-id={flat.id}
      role="treeitem"
      aria-level={depth + 1}
      aria-selected={isSelected && !isDir}
      {...(hasChildren ? { 'aria-expanded': !isCollapsed } : {})}
      style={{ opacity: isDragging ? 0 : 1 }}
    >
      <div
        className={`flex w-full items-center py-1 text-xs transition-colors group ${
          isInsideTarget
            ? 'bg-[var(--color-purple-accent)]/20 ring-1 ring-[var(--color-purple-accent)] rounded'
            : isSelected && !isDir
              ? 'bg-[var(--color-purple-accent)]/10 text-[var(--color-purple-deep)] font-medium'
              : 'text-[var(--color-foreground)] hover:bg-[var(--color-muted-foreground)]/5'
        } ${isFocused ? 'ring-1 ring-[var(--color-purple-accent)] rounded' : ''}`}
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
      >
        <span className="relative shrink-0" style={{ width: `${paddingLeft}px` }}>
          {Array.from({ length: depth }, (_, i) => (
            <span
              key={i}
              className="absolute top-0 bottom-0 w-px bg-[var(--color-border)]"
              style={{ left: `${i * INDENT_WIDTH + 8 + INDENT_WIDTH / 2}px` }}
            />
          ))}
        </span>

        <div className="w-5 h-5 shrink-0 flex items-center justify-center mr-1">
          {hasChildren ? (
            <button
              className="flex items-center justify-center w-full h-full"
              onClick={(e) => { e.stopPropagation(); onToggleCollapse(node.path) }}
              tabIndex={-1}
              aria-label={isCollapsed ? m.docs_tree_expand() : m.docs_tree_collapse()}
              type="button"
            >
              <ChevronRight
                size={12}
                className={`text-[var(--color-muted-foreground)] transition-transform ${isCollapsed ? '' : 'rotate-90'}`}
              />
            </button>
          ) : (
            isDir
              ? <FolderOpen size={13} className="shrink-0 text-[var(--color-muted-foreground)]" />
              : <span className="shrink-0 text-sm leading-none select-none">{node.icon ?? DEFAULT_ICON}</span>
          )}
        </div>

        <button
          className={`flex flex-1 items-center gap-1.5 min-w-0 text-left ${
            isDir
              ? 'font-medium text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)]'
              : isSelected
                ? 'text-[var(--color-purple-deep)] font-medium'
                : 'text-[var(--color-foreground)]'
          }`}
          onClick={() => { if (!isDir) onSelect(node) }}
          disabled={isDir && !hasChildren}
        >
          {isDir
            ? <FolderOpen size={13} className="shrink-0" />
            : <span className="shrink-0 text-sm leading-none select-none">{node.icon ?? DEFAULT_ICON}</span>
          }
          <span className="truncate">{displayTitle}</span>
        </button>

        <div className="flex items-center gap-0.5 mr-1.5 shrink-0">
          {(hovered || menuOpen) && !isDraggingActive && (
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
              <DropdownMenu open={menuOpen} onOpenChange={setMenuOpen}>
                <DropdownMenuTrigger asChild>
                  <button
                    type="button"
                    className="flex items-center justify-center w-4 h-4 rounded hover:bg-[var(--color-muted-foreground)]/15 text-[var(--color-muted-foreground)] hover:text-[var(--color-purple-deep)]"
                    aria-label={m.docs_tree_more_options()}
                  >
                    <MoreHorizontal size={10} />
                  </button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end" className="w-44">
                  {flat.depth > 0 ? (
                    <>
                      <DropdownMenuItem className="text-xs" onSelect={handlePromote}>
                        {m.docs_tree_promote()}
                      </DropdownMenuItem>
                      <DropdownMenuItem className="text-xs" onSelect={handleMoveToRoot}>
                        {m.docs_tree_move_to_root()}
                      </DropdownMenuItem>
                    </>
                  ) : (
                    <DropdownMenuItem className="text-xs text-[var(--color-muted-foreground)] italic" disabled>
                      {m.docs_tree_already_at_root()}
                    </DropdownMenuItem>
                  )}
                </DropdownMenuContent>
              </DropdownMenu>
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
