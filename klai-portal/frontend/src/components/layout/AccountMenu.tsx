import { Link, useLocation } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { LayoutGrid, LogOut, Shield, User, UserCircle } from 'lucide-react'
import { useAuth } from '@/lib/auth'
import { useCurrentUser } from '@/hooks/useCurrentUser'
import { apiFetch } from '@/lib/apiFetch'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import * as m from '@/paraglide/messages'

interface AccountFeedbackUpdatesResponse {
  unread_count: number
}

interface AccountPlatformMessagesResponse {
  unread_count: number
}

// Initials avatar is the dominant SaaS fallback when there is no profile photo
// (Google, Atlassian, Notion). Falls back to a neutral user icon when no name
// is known. Swap the `{initials || <User .../>}` line for just `<User .../>`
// to use a plain icon everywhere.
function getInitials(name: string | null): string {
  if (!name) return ''
  const parts = name.trim().split(/\s+/).filter(Boolean)
  if (parts.length === 0) return ''
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase()
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase()
}

export function AccountMenu() {
  const auth = useAuth()
  const location = useLocation()
  const { user } = useCurrentUser()
  const { data: feedbackUpdates } = useQuery({
    queryKey: ['account-feedback-updates'],
    queryFn: () => apiFetch<AccountFeedbackUpdatesResponse>('/api/app/account/feedback-updates'),
    enabled: auth.isAuthenticated,
  })
  const { data: platformMessages } = useQuery({
    queryKey: ['account-platform-messages'],
    queryFn: () => apiFetch<AccountPlatformMessagesResponse>('/api/app/account/messages'),
    enabled: auth.isAuthenticated,
  })

  const inAdmin = location.pathname.startsWith('/admin')
  const isAdmin = inAdmin || user?.isAdmin === true
  const feedbackUnreadCount = feedbackUpdates?.unread_count ?? 0
  const messageUnreadCount = platformMessages?.unread_count ?? 0
  const unreadCount = feedbackUnreadCount + messageUnreadCount

  const name = auth.user?.profile.name ?? auth.user?.profile.preferred_username ?? null
  const email = auth.user?.profile.email ?? null
  const initials = getInitials(name)

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          data-testid="user-menu"
          aria-label={m.sidebar_account()}
          className="relative inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-[var(--color-rl-dark)] text-[11px] font-medium text-white outline-none transition-opacity hover:opacity-90 focus-visible:ring-2 focus-visible:ring-[var(--color-rl-accent)]"
        >
          {initials || <User className="h-4 w-4" strokeWidth={1.75} />}
          {unreadCount > 0 && (
            <span
              className="absolute -right-0.5 -top-0.5 h-2.5 w-2.5 rounded-full bg-[var(--color-success)] ring-2 ring-[var(--color-sidebar)]"
              aria-label={m.account_unread_items()}
            />
          )}
        </button>
      </DropdownMenuTrigger>

      <DropdownMenuContent align="end" sideOffset={8} className="w-60">
        {(name || email) && (
          <>
            <DropdownMenuLabel className="font-normal">
              {name && <p className="truncate text-sm font-medium text-gray-900">{name}</p>}
              {email && <p className="truncate text-xs text-gray-400">{email}</p>}
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
          </>
        )}

        {isAdmin && (
          <DropdownMenuItem asChild>
            <Link to={inAdmin ? '/app' : '/admin'} className="cursor-pointer">
              {inAdmin ? <LayoutGrid className="h-4 w-4" /> : <Shield className="h-4 w-4" />}
              {inAdmin ? m.sidebar_go_to_app() : m.sidebar_go_to_admin()}
            </Link>
          </DropdownMenuItem>
        )}

        <DropdownMenuItem asChild>
          <Link to="/app/account" className="cursor-pointer">
            <UserCircle className="h-4 w-4" />
            {m.sidebar_account()}
          {unreadCount > 0 && (
              <span className="ml-auto inline-flex min-w-5 items-center justify-center rounded-full bg-[var(--color-success)] px-1.5 text-[11px] font-medium leading-5 text-white">
                {unreadCount}
              </span>
            )}
          </Link>
        </DropdownMenuItem>

        <DropdownMenuSeparator />

        <DropdownMenuItem
          onSelect={() => {
            // BFF logout via signoutRedirect → removeUser does the full flow:
            // revoke server-side session, clear cookies, navigate to Zitadel
            // end_session. Matches the previous sidebar logout behavior.
            void auth.signoutRedirect()
          }}
          className="cursor-pointer text-[var(--color-destructive)] focus:text-[var(--color-destructive)]"
        >
          <LogOut className="h-4 w-4" />
          {m.sidebar_logout()}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
