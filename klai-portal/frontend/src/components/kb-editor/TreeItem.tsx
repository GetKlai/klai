import { useState } from 'react'
import { ChevronRight, Plus, Check, X, MoreHorizontal, GripVertical } from 'lucide-react'
import { Input } from '@/components/ui/input'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
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
  onDeletePage: (path: string) => void
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
  onDeletePage,
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

  const setNodeRef = (el: HTMLDivElement | null) => {
    setDragRef(el)
    setDropRef(el)
  }

  const paddingLeft = depth * INDENT_WIDTH + 6

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
    <div className="py-0.5 pr-2" style={{ paddingLeft: `${paddingLeft + 20}px` }}>
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
          className="shrink-0 text-[var(--color-foreground)] hover:opacity-70 disabled:opacity-30"
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
        className={`flex w-full items-center py-1 mx-1 rounded-[5px] text-sm transition-colors group ${
          isInsideTarget
            ? 'bg-[var(--color-foreground)]/[0.06]'
            : isSelected && !isDir
              ? 'bg-[var(--color-foreground)]/[0.04]'
              : 'hover:bg-[var(--color-foreground)]/[0.03]'
        } ${isFocused ? 'bg-[var(--color-foreground)]/[0.06]' : ''}`}
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
      >
        <span className="shrink-0" style={{ width: `${paddingLeft}px` }} />

        {/* Chevron / icon area */}
        <div className="w-5 h-5 shrink-0 flex items-center justify-center">
          {hasChildren ? (
            <button
              className="flex items-center justify-center w-full h-full rounded hover:bg-[var(--color-foreground)]/[0.06] transition-colors"
              onClick={(e) => { e.stopPropagation(); onToggleCollapse(node.path) }}
              tabIndex={-1}
              aria-label={isCollapsed ? m.docs_tree_expand() : m.docs_tree_collapse()}
              type="button"
            >
              <ChevronRight
                size={12}
                strokeWidth={1.5}
                className={`text-[var(--color-muted-foreground)] transition-transform duration-150 ${isCollapsed ? '' : 'rotate-90'}`}
              />
            </button>
          ) : (
            <span className="shrink-0 text-sm leading-none select-none opacity-70">{node.icon ?? DEFAULT_ICON}</span>
          )}
        </div>

        {/* Title */}
        <button
          className="flex flex-1 items-center min-w-0 text-left ml-0.5 text-[var(--color-foreground)]"
          onClick={() => { if (!isDir) onSelect(node) }}
          disabled={isDir && !hasChildren}
        >
          <span className={`truncate ${isSelected && !isDir ? 'font-medium' : 'opacity-80'}`}>
            {displayTitle}
          </span>
        </button>

        {/* Actions - only on hover */}
        <div className="flex items-center gap-0 mr-1 shrink-0">
          {(hovered || menuOpen) && !isDraggingActive && (
            <>
              <button
                type="button"
                className="flex items-center justify-center w-5 h-5 rounded hover:bg-[var(--color-foreground)]/[0.06] text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)] transition-colors"
                onClick={() => onAddSubpage(nodePath)}
                title={m.docs_pages_add_subpage()}
                aria-label={m.docs_pages_add_subpage()}
              >
                <Plus size={12} strokeWidth={1.5} />
              </button>
              <DropdownMenu open={menuOpen} onOpenChange={setMenuOpen}>
                <DropdownMenuTrigger asChild>
                  <button
                    type="button"
                    className="flex items-center justify-center w-5 h-5 rounded hover:bg-[var(--color-foreground)]/[0.06] text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)] transition-colors"
                    aria-label={m.docs_tree_more_options()}
                  >
                    <MoreHorizontal size={12} strokeWidth={1.5} />
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
                  {!isDir && (
                    <>
                      <DropdownMenuSeparator />
                      <DropdownMenuItem
                        className="text-xs text-[var(--color-destructive)]"
                        onSelect={() => onDeletePage(nodePath)}
                      >
                        {m.docs_page_delete()}
                      </DropdownMenuItem>
                    </>
                  )}
                </DropdownMenuContent>
              </DropdownMenu>
            </>
          )}
          {(hovered || menuOpen) && !isDraggingActive && (
            <span className="cursor-grab touch-none" {...attributes} {...listeners}>
              <GripVertical size={12} strokeWidth={1.5} className="text-[var(--color-muted-foreground)] opacity-30" />
            </span>
          )}
        </div>
      </div>
      {subpageInput}
    </div>
  )
}
