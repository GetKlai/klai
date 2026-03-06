import { createFileRoute } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { MessageSquare, Mic, FileText } from 'lucide-react'

export const Route = createFileRoute('/app/')({
  component: AppHome,
})

const tools = [
  {
    title: 'Chat',
    description: 'Privé AI-gesprekken op Europese servers',
    icon: MessageSquare,
    href: '/app/chat',
  },
  {
    title: 'Transcriberen',
    description: 'Audio en video omzetten naar tekst',
    icon: Mic,
    href: '/app/transcribe',
  },
  {
    title: 'Scribe',
    description: 'Documenten en notities genereren',
    icon: FileText,
    href: '/app/scribe',
  },
]

function AppHome() {
  const auth = useAuth()
  const userName = auth.user?.profile.given_name ?? auth.user?.profile.name ?? 'daar'

  return (
    <div className="p-8 space-y-8 max-w-3xl">
      <div className="space-y-1">
        <h1 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
          Goedemorgen, {userName}
        </h1>
        <p className="text-sm text-[var(--color-muted-foreground)]">
          Jouw werkruimte draait op Europese servers. Alles blijft van jou.
        </p>
      </div>

      <div>
        <h2 className="mb-4 text-sm font-semibold text-[var(--color-foreground)]">Tools</h2>
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
