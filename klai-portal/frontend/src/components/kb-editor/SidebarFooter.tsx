import { Plus, Upload, Check, X } from 'lucide-react'
import { useRef } from 'react'
import { Input } from '@/components/ui/input'
import * as m from '@/paraglide/messages'

export interface SidebarFooterProps {
  showNewPage: boolean
  newPageParent: string | null
  newPageTitle: string
  saveStatus: 'idle' | 'saving' | 'saved' | 'renamed' | 'error'
  onShowNewPage: () => void
  onNewPageTitleChange: (val: string) => void
  onNewPageConfirm: () => void
  onNewPageCancel: () => void
  onUpload: (file: File) => void
}

export function SidebarFooter({
  showNewPage,
  newPageParent,
  newPageTitle,
  saveStatus,
  onShowNewPage,
  onNewPageTitleChange,
  onNewPageConfirm,
  onNewPageCancel,
  onUpload,
}: SidebarFooterProps) {
  const fileInputRef = useRef<HTMLInputElement>(null)
  const showRootInput = showNewPage && newPageParent === null

  return (
    <div className="px-2 py-2 border-t border-[var(--color-border)] space-y-1">
      {showRootInput ? (
        <div className="space-y-1.5 px-1">
          <Input
            value={newPageTitle}
            onChange={(e) => onNewPageTitleChange(e.target.value)}
            placeholder={m.docs_editor_title_placeholder()}
            className="h-7 text-xs"
            autoFocus
            onKeyDown={(e) => {
              if (e.key === 'Enter') onNewPageConfirm()
              if (e.key === 'Escape') onNewPageCancel()
            }}
          />
          <div className="flex gap-1">
            <button
              className="flex-1 h-7 flex items-center justify-center gap-1 text-xs rounded-md bg-[var(--color-foreground)] text-[var(--color-background)] hover:opacity-90 transition-opacity disabled:opacity-40"
              onClick={onNewPageConfirm}
              disabled={!newPageTitle.trim() || saveStatus === 'saving'}
            >
              <Check size={11} />
              {m.docs_kb_create()}
            </button>
            <button
              className="h-7 px-2 flex items-center justify-center rounded-md text-[var(--color-muted-foreground)] hover:bg-[var(--color-foreground)]/[0.04] transition-colors"
              onClick={onNewPageCancel}
            >
              <X size={11} />
            </button>
          </div>
        </div>
      ) : (
        <button
          className="w-full flex items-center gap-2 px-3 py-1.5 text-[15px] text-[var(--color-muted-foreground)] hover:bg-[var(--color-foreground)]/[0.04] hover:text-[var(--color-foreground)] rounded-md transition-colors"
          onClick={onShowNewPage}
        >
          <Plus size={14} strokeWidth={1.5} />
          {m.docs_pages_new()}
        </button>
      )}
      <input
        ref={fileInputRef}
        type="file"
        accept=".md"
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0]
          if (file) onUpload(file)
          e.target.value = ''
        }}
      />
      <button
        className="w-full flex items-center gap-2 px-3 py-1.5 text-sm text-[var(--color-muted-foreground)] hover:bg-[var(--color-foreground)]/[0.04] hover:text-[var(--color-foreground)] rounded-md transition-colors"
        onClick={() => fileInputRef.current?.click()}
      >
        <Upload size={14} strokeWidth={1.5} />
        {m.docs_pages_upload()}
      </button>
    </div>
  )
}
