import { createFileRoute } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { MessageSquare, Mic, FileText, BookOpen } from 'lucide-react'
import * as m from '@/paraglide/messages'

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
  const userName = auth.user?.profile.given_name ?? auth.user?.profile.name ?? m.app_home_user_fallback()

  const tools = [
    {
      title: m.app_tool_chat_title(),
      description: m.app_tool_chat_description(),
      icon: MessageSquare,
      href: '/app/chat',
    },
    {
      title: m.app_tool_transcribe_title(),
      description: m.app_tool_transcribe_description(),
      icon: Mic,
      href: '/app/transcribe',
    },
    {
      title: m.app_tool_scribe_title(),
      description: m.app_tool_scribe_description(),
      icon: FileText,
      href: '/app/scribe',
    },
    {
      title: m.app_tool_research_title(),
      description: m.app_tool_research_description(),
      icon: BookOpen,
      href: '/app/research',
    },
  ]

  return (
    <div className="p-8 space-y-8 max-w-3xl">
      <div className="space-y-1">
        <h1 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
          {getGreeting(userName)}
        </h1>
        <p className="text-sm text-[var(--color-muted-foreground)]">
          {m.app_home_subtitle()}
        </p>
      </div>

      <div>
        <h2 className="mb-4 text-sm font-semibold text-[var(--color-foreground)]">{m.app_home_tools()}</h2>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          {tools.map((tool) => (
            <a
              key={tool.title}
              href={tool.href}
              className="group flex flex-col gap-3 rounded-xl border bg-[var(--color-card)] p-5 transition-shadow hover:shadow-md"
            >
              <tool.icon
                size={20}
                strokeWidth={1.5}
                className="text-[var(--color-purple-accent)]"
              />
              <div>
                <p className="text-sm font-medium text-[var(--color-purple-deep)] group-hover:text-[var(--color-purple-accent)] transition-colors">
                  {tool.title}
                </p>
                <p className="mt-0.5 text-xs text-[var(--color-muted-foreground)]">
                  {tool.description}
                </p>
              </div>
            </a>
          ))}
        </div>
      </div>
    </div>
  )
}
