import { useEffect, useRef } from 'react'
import { createFileRoute } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { X } from 'lucide-react'
import { apiFetch } from '@/lib/apiFetch'
import * as m from '@/paraglide/messages'
import type { WidgetDetailResponse } from './-types'

// Fullscreen test environment for a widget. Loads klai-chat.js in
// inline mode with a viewport-filling container, so the admin sees the
// real widget UI (welcome, composer, send) exactly as a customer
// would — but on a clean my.getklai.com canvas without needing to
// embed the snippet on a real site. The widget's allowed_origins must
// include https://my.getklai.com — the Test button on EmbedTab adds
// it before opening this page.

export const Route = createFileRoute('/admin/widgets/$id_/test')({
  component: WidgetTestPage,
})

function WidgetTestPage() {
  const { id } = Route.useParams()
  const widgetQuery = useQuery<WidgetDetailResponse>({
    queryKey: ['admin-widget-detail', id],
    queryFn: () => apiFetch<WidgetDetailResponse>(`/api/admin/widgets/${id}`),
  })
  const scriptMounted = useRef(false)

  useEffect(() => {
    if (!widgetQuery.data || scriptMounted.current) return
    const w = widgetQuery.data
    const cfg = w.widget_config
    const s = document.createElement('script')
    s.src = 'https://my.getklai.com/widget/klai-chat.js'
    s.async = true
    s.setAttribute('data-widget-id', w.widget_id)
    s.setAttribute('data-mode', 'inline')
    s.setAttribute('data-container', '#klai-fullscreen-chat')
    if (cfg.title) s.setAttribute('data-title', cfg.title)
    if (cfg.welcome_message) s.setAttribute('data-welcome', cfg.welcome_message)
    document.body.appendChild(s)
    scriptMounted.current = true
    return () => {
      s.remove()
    }
  }, [widgetQuery.data])

  if (widgetQuery.isPending) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-[var(--color-rl-bg)]">
        <p className="text-sm text-gray-400">Laden…</p>
      </div>
    )
  }

  if (widgetQuery.error || !widgetQuery.data) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-[var(--color-rl-bg)] p-10">
        <p className="text-sm text-[var(--color-destructive)]">
          {widgetQuery.error instanceof Error ? widgetQuery.error.message : 'Widget kon niet geladen worden.'}
        </p>
      </div>
    )
  }

  const w = widgetQuery.data

  return (
    <div className="fixed inset-0 flex flex-col bg-[var(--color-rl-bg)]">
      {/* Top bar — widget name + close */}
      <div className="flex items-center justify-between border-b border-gray-200 px-6 py-3 bg-white">
        <div className="flex items-center gap-3 min-w-0">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-[var(--color-rl-cream)]">
            <span className="text-[16px]">💬</span>
          </div>
          <div className="min-w-0">
            <p className="text-[14px] font-display-medium text-gray-900 truncate">
              {w.widget_config.title || w.name}
            </p>
            <p className="text-[11px] text-gray-400">
              {m.admin_widgets_test_page_title()}
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={() => window.close()}
          aria-label="Sluiten"
          className="inline-flex h-8 w-8 items-center justify-center rounded-full text-gray-400 klai-hover"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {/* Notice strip */}
      <div className="border-b border-[var(--color-rl-border)] bg-[var(--color-rl-cream)] px-6 py-2">
        <p className="text-[11px] text-[var(--color-rl-accent-dark)] leading-snug">
          {m.admin_widgets_test_page_note_added()}
        </p>
      </div>

      {/* The widget renders into this container — fills the rest of the viewport */}
      <div id="klai-fullscreen-chat" className="flex-1 overflow-hidden" />
    </div>
  )
}
