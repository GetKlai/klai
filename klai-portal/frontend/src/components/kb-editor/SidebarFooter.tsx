import { Plus, Upload, Check, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
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
  const showRootInput = showNewPage && newPageParent === null

  return (
    <div className="px-3 py-3 border-t border-[var(--color-border)] space-y-2">
      {showRootInput ? (
        <div className="space-y-1.5">
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
            <Button
              size="sm"
              className="flex-1 h-6 text-xs"
              onClick={onNewPageConfirm}
              disabled={!newPageTitle.trim() || saveStatus === 'saving'}
            >
              <Check size={11} className="mr-1" />
              {m.docs_kb_create()}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              className="h-6 px-2"
              onClick={onNewPageCancel}
            >
              <X size={11} />
            </Button>
          </div>
        </div>
      ) : (
        <Button
          variant="outline"
          size="sm"
          className="w-full"
          onClick={onShowNewPage}
        >
          <Plus size={12} className="mr-1.5" />
          {m.docs_pages_new()}
        </Button>
      )}
      <label className="w-full block">
        <input
          type="file"
          accept=".md"
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0]
            if (file) onUpload(file)
            e.target.value = ''
          }}
        />
        <Button variant="outline" size="sm" className="w-full cursor-pointer">
          <Upload size={12} className="mr-1.5" />
          {m.docs_pages_upload()}
        </Button>
      </label>
    </div>
  )
}
