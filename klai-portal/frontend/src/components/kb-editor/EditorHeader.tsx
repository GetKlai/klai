import Picker from '@emoji-mart/react'
import data from '@emoji-mart/data'
import { useState, useRef, useEffect } from 'react'
import { Check, Loader2, MoreHorizontal } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import * as m from '@/paraglide/messages'

export interface EditorHeaderProps {
  pageIcon: string
  editTitle: string
  saveStatus: 'idle' | 'saving' | 'saved' | 'renamed' | 'error'
  onIconChange: (icon: string) => void
  onTitleChange: (title: string) => void
  onScheduleSave: () => void
  onToggleAccessPanel: () => void
  onDeletePage: () => void
}

export function EditorHeader({
  pageIcon,
  editTitle,
  saveStatus,
  onIconChange,
  onTitleChange,
  onScheduleSave,
  onToggleAccessPanel,
  onDeletePage,
}: EditorHeaderProps) {
  const [showIconPicker, setShowIconPicker] = useState(false)
  const [showMenu, setShowMenu] = useState(false)
  const iconPickerRef = useRef<HTMLDivElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!showIconPicker) return
    const handler = (e: MouseEvent) => {
      if (iconPickerRef.current && !iconPickerRef.current.contains(e.target as Node)) {
        setShowIconPicker(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [showIconPicker])

  useEffect(() => {
    if (!showMenu) return
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setShowMenu(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [showMenu])

  return (
    <div className="flex items-center gap-3 px-5 py-3 border-b border-[var(--color-border)]">
      {/* Emoji icon zone */}
      <div className="relative shrink-0" ref={iconPickerRef}>
        <button
          className="flex items-center justify-center w-8 h-8 rounded hover:bg-[var(--color-muted-foreground)]/10 text-xl leading-none transition-colors"
          onClick={() => setShowIconPicker((v) => !v)}
          title="Pictogram kiezen"
          type="button"
        >
          {pageIcon}
        </button>
        {showIconPicker && (
          <div
            style={{
              position: 'absolute',
              top: '100%',
              left: 0,
              zIndex: 50,
            }}
          >
            <Picker
              data={data}
              onEmojiSelect={(emoji: { native: string }) => {
                onIconChange(emoji.native)
                onScheduleSave()
                setShowIconPicker(false)
              }}
              theme="light"
              locale="nl"
              previewPosition="none"
              skinTonePosition="none"
            />
          </div>
        )}
      </div>
      <Input
        value={editTitle}
        onChange={(e) => {
          onTitleChange(e.target.value)
          onScheduleSave()
        }}
        placeholder={m.docs_editor_title_placeholder()}
        className="flex-1 text-base font-medium border-none shadow-none focus-visible:ring-0 p-0 h-auto"
      />
      <div className="flex items-center gap-2 shrink-0">
        {saveStatus === 'saving' && (
          <span className="flex items-center gap-1 text-xs text-[var(--color-muted-foreground)]">
            <Loader2 size={12} className="animate-spin" />
            {m.docs_editor_saving()}
          </span>
        )}
        {saveStatus === 'saved' && (
          <span className="flex items-center gap-1 text-xs text-[var(--color-muted-foreground)]">
            <Check size={12} />
            {m.docs_editor_save()}
          </span>
        )}
        {saveStatus === 'renamed' && (
          <span className="flex items-center gap-1 text-xs text-[var(--color-muted-foreground)]">
            <Check size={12} />
            {m.docs_editor_url_updated()}
          </span>
        )}
        {saveStatus === 'error' && (
          <span className="text-xs text-[var(--color-destructive)]">Opslaan mislukt</span>
        )}
        <div className="relative" ref={menuRef}>
          <Button
            variant="ghost"
            size="sm"
            className="h-7 w-7 p-0"
            onClick={() => setShowMenu((v) => !v)}
          >
            <MoreHorizontal size={15} />
          </Button>
          {showMenu && (
            <div className="absolute right-0 top-8 z-10 w-48 rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] shadow-md py-1">
              <p className="px-3 py-1.5 text-xs text-[var(--color-muted-foreground)]">
                Paginainstellingen
              </p>
              <button
                className="w-full text-left px-3 py-1.5 text-sm text-[var(--color-foreground)] hover:bg-[var(--color-secondary)]"
                onClick={() => { setShowMenu(false); onToggleAccessPanel() }}
              >
                {m.docs_access_panel_title()}…
              </button>
              <hr className="my-1 border-[var(--color-border)]" />
              <button
                className="w-full text-left px-3 py-1.5 text-sm text-[var(--color-destructive)] hover:bg-[var(--color-secondary)]"
                onClick={() => { setShowMenu(false); onDeletePage() }}
              >
                {m.docs_page_delete()}
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
