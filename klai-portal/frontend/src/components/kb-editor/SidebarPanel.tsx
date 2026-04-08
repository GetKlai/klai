import { Link } from '@tanstack/react-router'
import { ArrowLeft } from 'lucide-react'
import * as m from '@/paraglide/messages'
import type { NavNode } from '@/lib/kb-editor/tree-utils'
import { NavTree } from './NavTree'
import { SidebarFooter } from './SidebarFooter'

export interface SidebarPanelProps {
  displayTree: NavNode[]
  selectedPath: string | null
  editTitle: string
  showNewPage: boolean
  newPageParent: string | null
  newPageTitle: string
  saveStatus: 'idle' | 'saving' | 'saved' | 'renamed' | 'error'
  onSelect: (node: NavNode) => void | Promise<void>
  onSidebarUpdate: (newTree: NavNode[]) => void
  onAddSubpage: (parentPath: string) => void
  onDeletePage: (path: string) => void
  onShowNewPage: () => void
  onNewPageTitleChange: (val: string) => void
  onNewPageConfirm: (parentPath: string | null) => void
  onNewPageCancel: () => void
  onUpload: (file: File) => void
}

export function SidebarPanel({
  displayTree,
  selectedPath,
  editTitle,
  showNewPage,
  newPageParent,
  newPageTitle,
  saveStatus,
  onSelect,
  onSidebarUpdate,
  onAddSubpage,
  onDeletePage,
  onShowNewPage,
  onNewPageTitleChange,
  onNewPageConfirm,
  onNewPageCancel,
  onUpload,
}: SidebarPanelProps) {
  return (
    <aside className="w-60 shrink-0 border-r border-[var(--color-border)] bg-[var(--color-sidebar)] flex flex-col">
      <div className="px-4 py-3">
        <Link
          to="/app/docs"
          className="flex items-center gap-1.5 text-[11px] text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)] transition-colors"
        >
          <ArrowLeft size={11} strokeWidth={1.5} />
          {m.docs_editor_back()}
        </Link>
      </div>
      <div className="flex-1 overflow-y-auto px-1 py-1">
        {displayTree.length === 0 ? (
          <p className="px-3 py-2 text-xs text-[var(--color-muted-foreground)]">
            {m.docs_pages_empty()}
          </p>
        ) : (
          <NavTree
            nodes={displayTree}
            selectedPath={selectedPath}
            onSelect={onSelect}
            activeTitle={editTitle}
            activePath={selectedPath}
            onSidebarUpdate={onSidebarUpdate}
            onAddSubpage={onAddSubpage}
            onDeletePage={onDeletePage}
            addingSubpageUnder={showNewPage ? newPageParent : null}
            newPageTitle={newPageTitle}
            onNewPageTitleChange={onNewPageTitleChange}
            onNewPageConfirm={onNewPageConfirm}
            onNewPageCancel={onNewPageCancel}
          />
        )}
      </div>
      <SidebarFooter
        showNewPage={showNewPage}
        newPageParent={newPageParent}
        newPageTitle={newPageTitle}
        saveStatus={saveStatus}
        onShowNewPage={onShowNewPage}
        onNewPageTitleChange={onNewPageTitleChange}
        onNewPageConfirm={() => onNewPageConfirm(null)}
        onNewPageCancel={onNewPageCancel}
        onUpload={onUpload}
      />
    </aside>
  )
}
