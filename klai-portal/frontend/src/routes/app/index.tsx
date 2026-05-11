import { createFileRoute } from '@tanstack/react-router'
import { useAuth } from '@/lib/auth'
import { MessageSquare, Mic, BookMarked, Brain } from 'lucide-react'
import * as m from '@/paraglide/messages'
import { useCurrentUser } from '@/hooks/useCurrentUser'

export const Route = createFileRoute('/app/')({
  component: AppHome,
})

function getGreeting(name: string): string {
  const hour = new Date().getHours()
  if (hour >= 6 && hour < 12) return m.app_home_greeting_morning({ name })
  if (hour >= 12 && hour < 18) return m.app_home_greeting_afternoon({ name })
  return m.app_home_greeting_evening({ name })
}

function AppHome() {
  const auth = useAuth()
  const { user } = useCurrentUser()
  const userName = auth.user?.profile.given_name ?? auth.user?.profile.name ?? m.app_home_user_fallback()

  // SPEC-PORTAL-PROFILES-001 P3.1 follow-up: tools-grid mirrors sidebar gating.
  // Tiles for products the user does not have are HIDDEN (not greyed-out).
  // Admin-bypass is intentionally absent — admins see exactly what their tenant
  // has enabled, same as everyone else.
  // SPEC-PORTAL-UNIFY-KB-001: Focus tile removed; Knowledge replaces it.
  // SPEC-PORTAL-PROFILES-001 Phase 2: docs is its own product (was: knowledge).
  const tools = [
    {
      title: m.app_tool_chat_title(),
      description: m.app_tool_chat_description(),
      icon: MessageSquare,
      href: '/app/chat',
      helpId: 'home-tool-chat',
      product: 'chat',
    },
    {
      title: m.app_tool_transcribe_title(),
      description: m.app_tool_transcribe_description(),
      icon: Mic,
      href: '/app/transcribe',
      helpId: 'home-tool-transcribe',
      product: 'scribe',
    },
    {
      title: m.app_tool_knowledge_title(),
      description: m.app_tool_knowledge_description(),
      icon: Brain,
      href: '/app/knowledge',
      helpId: 'home-tool-knowledge',
      product: 'knowledge',
    },
    {
      title: m.app_tool_docs_title(),
      description: m.app_tool_docs_description(),
      icon: BookMarked,
      href: '/app/docs',
      helpId: 'home-tool-docs',
      product: 'docs',
    },
  ]

  const products = user?.products ?? []
  const accessibleTools = tools.filter((tool) => products.includes(tool.product))

  return (
    <div className="mx-auto max-w-3xl px-6 py-10 space-y-8">
      <div className="space-y-1" data-help-id="home-greeting">
        <h1 className="page-title text-[26px] font-display-bold text-gray-900">
          {getGreeting(userName)}
        </h1>
        <p className="text-sm text-gray-400">
          {m.app_home_subtitle()}
        </p>
      </div>

      {accessibleTools.length > 0 && (
        <div>
          <h2 className="mb-4 text-sm font-semibold text-gray-900">{m.app_home_tools()}</h2>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            {accessibleTools.map((tool) => (
              <a
                key={tool.title}
                href={tool.href}
                data-help-id={tool.helpId}
                className="group flex flex-col gap-3 rounded-xl border bg-[var(--color-card)] p-5 transition-shadow hover:shadow-md"
              >
                <tool.icon
                  size={20}
                  strokeWidth={1.5}
                  className="text-gray-400"
                />
                <div>
                  <p className="text-sm font-medium text-gray-900 group-hover:text-[var(--color-rl-accent)] transition-colors">
                    {tool.title}
                  </p>
                  <p className="mt-0.5 text-xs text-gray-400">
                    {tool.description}
                  </p>
                </div>
              </a>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
