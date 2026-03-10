import { createFileRoute, Outlet, useNavigate } from '@tanstack/react-router'
import { useEffect } from 'react'
import { useAuth } from 'react-oidc-context'
import { MessageSquare, Mic, FileText } from 'lucide-react'
import { Sidebar } from '@/components/layout/Sidebar'

const appNav = [
  { to: '/app/chat', label: 'Chat', icon: MessageSquare },
  { to: '/app/transcribe', label: 'Transcriberen', icon: Mic },
  { to: '/app/scribe', label: 'Scribe', icon: FileText },
]

export const Route = createFileRoute('/app')({
  component: AppLayout,
})

function AppLayout() {
  const auth = useAuth()
  const navigate = useNavigate()

  useEffect(() => {
    if (!auth.isLoading && !auth.isAuthenticated) {
      navigate({ to: '/' })
    }
  }, [auth.isLoading, auth.isAuthenticated, navigate])

  if (auth.isLoading || !auth.isAuthenticated) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[var(--color-off-white)]">
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-[var(--color-purple-accent)] border-t-transparent" />
      </div>
    )
  }

  return (
    <div className="flex h-screen overflow-hidden bg-[var(--color-background)]">
      <Sidebar navItems={appNav} />
      <main className="flex-1 overflow-y-auto">
        <Outlet />
      </main>
    </div>
  )
}
