import { Lock, Users, X, Check, Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import * as m from '@/paraglide/messages'

export interface AccessControlPanelProps {
  accessMode: 'org' | 'specific'
  accessUsers: string[]
  newUserId: string
  accessSaveStatus: 'idle' | 'saving' | 'saved' | 'error'
  onClose: () => void
  onAccessModeChange: (mode: 'org' | 'specific') => void
  onNewUserIdChange: (val: string) => void
  onAddUser: (uid: string) => void
  onRemoveUser: (uid: string) => void
  onSave: () => void
}

export function AccessControlPanel({
  accessMode,
  accessUsers,
  newUserId,
  accessSaveStatus,
  onClose,
  onAccessModeChange,
  onNewUserIdChange,
  onAddUser,
  onRemoveUser,
  onSave,
}: AccessControlPanelProps) {
  return (
    <div className="border-b border-[var(--color-border)] bg-[var(--color-muted)] px-5 py-4">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <Lock size={14} className="text-[var(--color-foreground)]" />
          <span className="text-sm font-medium text-[var(--color-foreground)]">
            {m.docs_access_panel_title()}
          </span>
        </div>
        <button
          className="text-xs text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)]"
          onClick={onClose}
        >
          {m.docs_access_close()}
        </button>
      </div>
      <div className="space-y-3 max-w-sm">
        <div className="space-y-1.5">
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="radio"
              name="access-mode"
              value="org"
              checked={accessMode === 'org'}
              onChange={() => onAccessModeChange('org')}
              className="accent-[var(--color-accent)]"
            />
            <span className="text-sm text-[var(--color-foreground)]">
              <Users size={13} className="inline mr-1" />
              {m.docs_access_everyone()}
            </span>
          </label>
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="radio"
              name="access-mode"
              value="specific"
              checked={accessMode === 'specific'}
              onChange={() => onAccessModeChange('specific')}
              className="accent-[var(--color-accent)]"
            />
            <span className="text-sm text-[var(--color-foreground)]">
              <Lock size={13} className="inline mr-1" />
              {m.docs_access_specific()}
            </span>
          </label>
        </div>
        {accessMode === 'specific' && (
          <div className="space-y-2">
            <div className="flex gap-2">
              <Input
                id="docs-access-add-user"
                value={newUserId}
                onChange={(e) => onNewUserIdChange(e.target.value)}
                placeholder={m.docs_access_add_placeholder()}
                className="h-7 text-xs"
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && newUserId.trim()) {
                    onAddUser(newUserId.trim())
                  }
                }}
              />
              <Button
                size="sm"
                className="h-7 px-2 text-xs shrink-0"
                disabled={!newUserId.trim()}
                onClick={() => { if (newUserId.trim()) onAddUser(newUserId.trim()) }}
              >
                {m.docs_access_add_button()}
              </Button>
            </div>
            {accessUsers.length > 0 && (
              <ul className="space-y-1">
                {accessUsers.map((uid) => (
                  <li key={uid} className="flex items-center justify-between rounded bg-[var(--color-card)] border border-[var(--color-border)] px-2 py-1">
                    <span className="text-xs text-[var(--color-foreground)] truncate">{uid}</span>
                    <button
                      className="ml-2 text-xs text-[var(--color-destructive)] hover:opacity-70 shrink-0"
                      onClick={() => onRemoveUser(uid)}
                      aria-label={m.docs_access_remove()}
                    >
                      <X size={12} />
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
        <div className="flex items-center gap-3 pt-1">
          <Button
            size="sm"
            className="h-7 text-xs"
            disabled={accessSaveStatus === 'saving'}
            onClick={onSave}
          >
            {accessSaveStatus === 'saving' ? (
              <><Loader2 size={11} className="mr-1 animate-spin" />{m.docs_access_saving()}</>
            ) : (
              m.docs_access_save()
            )}
          </Button>
          {accessSaveStatus === 'saved' && (
            <span className="flex items-center gap-1 text-xs text-[var(--color-muted-foreground)]">
              <Check size={11} />{m.docs_access_saved()}
            </span>
          )}
          {accessSaveStatus === 'error' && (
            <span className="text-xs text-[var(--color-destructive)]">{m.docs_access_error_save()}</span>
          )}
        </div>
      </div>
    </div>
  )
}
