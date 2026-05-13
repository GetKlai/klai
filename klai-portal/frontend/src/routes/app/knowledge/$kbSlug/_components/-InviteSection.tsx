import { useRef, type ReactNode } from 'react'
import { Search } from 'lucide-react'
import { Input } from '@/components/ui/input'
import * as m from '@/paraglide/messages'

export interface OrgGroup {
  id: number
  name: string
}

export interface OrgUser {
  zitadel_user_id: string
  display_name: string
  email: string
}

interface InviteSectionProps {
  kind: 'groups' | 'users'
  title: string
  isOwner: boolean
  search: string
  onSearchChange: (value: string) => void
  focused: boolean
  onFocusedChange: (focused: boolean) => void
  options: OrgGroup[] | OrgUser[]
  error: unknown
  onInviteGroup?: (groupId: number) => void
  onInviteUser?: (email: string) => void
  children: ReactNode
  emptyReadOnlyMessage: string
  isEmpty: boolean
}

export function InviteSection({
  kind,
  title,
  isOwner,
  search,
  onSearchChange,
  focused,
  onFocusedChange,
  options,
  error,
  onInviteGroup,
  onInviteUser,
  children,
  emptyReadOnlyMessage,
  isEmpty,
}: InviteSectionProps) {
  const rootRef = useRef<HTMLDivElement>(null)
  const placeholder =
    kind === 'groups'
      ? m.knowledge_sharing_search_group()
      : m.knowledge_sharing_search_person()
  const errorMessage = formatError(error)

  return (
    <div className="space-y-2">
      <h2 className="text-sm font-semibold text-gray-900">{title}</h2>
      {isOwner && (
        <div
          className="relative"
          ref={rootRef}
          onFocusCapture={() => onFocusedChange(true)}
          onBlurCapture={(e) => {
            if (!rootRef.current?.contains(e.relatedTarget as Node)) {
              onFocusedChange(false)
            }
          }}
        >
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" />
          <Input
            value={search}
            onChange={(e) => onSearchChange(e.target.value)}
            placeholder={placeholder}
            className="pl-9"
          />
          {focused && options.length > 0 && (
            <div className="absolute z-10 mt-1 w-full rounded-lg border border-gray-200 bg-[var(--color-card)] shadow-md max-h-40 overflow-y-auto">
              {kind === 'groups'
                ? (options as OrgGroup[]).map((group) => (
                    <button
                      key={group.id}
                      type="button"
                      onMouseDown={(e) => e.preventDefault()}
                      onClick={() => onInviteGroup?.(group.id)}
                      className="w-full px-3 py-2 text-left text-sm text-gray-900 hover:bg-gray-50 transition-colors"
                    >
                      {group.name}
                    </button>
                  ))
                : (options as OrgUser[]).map((user) => (
                    <button
                      key={user.zitadel_user_id}
                      type="button"
                      onMouseDown={(e) => e.preventDefault()}
                      onClick={() => onInviteUser?.(user.email)}
                      className="w-full px-3 py-2 text-left text-sm hover:bg-gray-50 transition-colors"
                    >
                      <span className="text-gray-900">{user.display_name}</span>
                      <span className="ml-2 text-xs text-gray-400">{user.email}</span>
                    </button>
                  ))}
            </div>
          )}
        </div>
      )}

      {errorMessage && (
        <p className="text-sm text-[var(--color-destructive)]">{errorMessage}</p>
      )}

      {children}

      {isEmpty && !isOwner && (
        <p className="text-sm text-gray-400">{emptyReadOnlyMessage}</p>
      )}
    </div>
  )
}

function formatError(error: unknown): string | null {
  if (!error) return null
  if (error instanceof Error) return error.message
  if (typeof error === 'string') return error
  return m.knowledge_members_invite_error()
}
