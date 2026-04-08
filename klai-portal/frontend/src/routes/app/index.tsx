import { createFileRoute } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { MessageSquare, Mic, BookOpen, BookMarked, Brain } from 'lucide-react'
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
      helpId: 'home-tool-chat',
    },
    {
      title: m.app_tool_transcribe_title(),
      description: m.app_tool_transcribe_description(),
      icon: Mic,
      href: '/app/transcribe',
      helpId: 'home-tool-transcribe',
    },
    {
      title: m.app_tool_focus_title(),
      description: m.app_tool_focus_description(),
      icon: BookOpen,
      href: '/app/focus',
      helpId: 'home-tool-focus',
    },
    {
      title: m.app_tool_knowledge_title(),
      description: m.app_tool_knowledge_description(),
      icon: Brain,
      href: '/app/knowledge',
      helpId: 'home-tool-knowledge',
    },
    {
      title: m.app_tool_docs_title(),
      description: m.app_tool_docs_description(),
      icon: BookMarked,
      href: '/app/docs',
      helpId: 'home-tool-docs',
    },
  ]

  return (
    <div className="p-8 space-y-8 max-w-3xl">
      <div className="space-y-1" data-help-id="home-greeting">
        <h1 className="text-xl font-semibold text-[var(--color-foreground)]">
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
              data-help-id={tool.helpId}
              className="group flex flex-col gap-3 rounded-xl border bg-[var(--color-card)] p-5 transition-shadow hover:shadow-md"
            >
              <tool.icon
                size={20}
                strokeWidth={1.5}
                className="text-[var(--color-rl-accent)]"
              />
              <div>
                <p className="text-sm font-medium text-[var(--color-foreground)] group-hover:text-[var(--color-rl-accent)] transition-colors">
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
