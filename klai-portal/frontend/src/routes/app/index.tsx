import { createFileRoute } from '@tanstack/react-router'
import { useAuth } from '@/lib/auth'
import { ChevronRight } from 'lucide-react'
import * as m from '@/paraglide/messages'
import { useCurrentUser } from '@/hooks/useCurrentUser'
import { getAccessibleAppTools } from './-app-tools'

export const Route = createFileRoute('/app/')({
  component: AppHome,
})

function getGreeting(name: string | null): string {
  const hour = new Date().getHours()
  if (name) {
    if (hour >= 6 && hour < 12) return m.app_home_greeting_morning({ name })
    if (hour >= 12 && hour < 18) return m.app_home_greeting_afternoon({ name })
    return m.app_home_greeting_evening({ name })
  }
  if (hour >= 6 && hour < 12) return m.app_home_greeting_morning_anon()
  if (hour >= 12 && hour < 18) return m.app_home_greeting_afternoon_anon()
  return m.app_home_greeting_evening_anon()
}

function AppHome() {
  const auth = useAuth()
  const { user } = useCurrentUser()
  const userName = auth.user?.profile.given_name ?? auth.user?.profile.name ?? null

  const products = user?.products ?? []
  const accessibleTools = getAccessibleAppTools(products)

  return (
    <div className="mx-auto max-w-3xl px-6 pt-4 pb-10 space-y-8">
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
          <div className="divide-y divide-gray-200 border-t border-b border-gray-200">
            {accessibleTools.map((tool) => (
              <a
                key={tool.href}
                href={tool.href}
                data-help-id={tool.helpId}
                className="group flex items-center gap-3 px-2 py-3.5 klai-hover"
              >
                <div className="flex h-8 w-8 shrink-0 items-center justify-center text-gray-400">
                  <tool.icon className="h-4 w-4" />
                </div>
                <div className="min-w-0 flex-1">
                  <span className="text-[15px] font-display text-gray-900">
                    {tool.title()}
                  </span>
                  <p className="text-xs text-gray-400 mt-0.5">
                    {tool.description()}
                  </p>
                </div>
                <ChevronRight className="h-4 w-4 text-gray-300 shrink-0" />
              </a>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
