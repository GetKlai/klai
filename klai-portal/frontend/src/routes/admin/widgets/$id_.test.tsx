import { createFileRoute } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { MessageSquare, Plus, Share2, X } from 'lucide-react'
import { apiFetch } from '@/lib/apiFetch'
import * as m from '@/paraglide/messages'
import type { WidgetDetailResponse } from './-types'

// Fullscreen test environment for a widget — matches the TalkWithData /
// LibreChat layout (top bar, hero, suggestion chips, composer). Uses an
// iframe pointing at /widget-preview.html (static asset) so klai-chat.js
// loads from a real <script> tag where document.currentScript is the
// snippet — the only reliable path for inline mode rendering.

export const Route = createFileRoute('/admin/widgets/$id_/test')({
  component: WidgetTestPage,
})

function WidgetTestPage() {
  const { id } = Route.useParams()
  const widgetQuery = useQuery<WidgetDetailResponse>({
    queryKey: ['admin-widget-detail', id],
    queryFn: () => apiFetch<WidgetDetailResponse>(`/api/admin/widgets/${id}`),
  })

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
  const cfg = w.widget_config
  const displayTitle = cfg.title || w.name

  const previewUrl = `/widget-preview.html?${new URLSearchParams({
    widget_id: w.widget_id,
    ...(cfg.title ? { title: cfg.title } : {}),
    ...(cfg.welcome_message ? { welcome: cfg.welcome_message } : {}),
  }).toString()}`

  return (
    <div className="fixed inset-0 flex flex-col bg-[var(--color-rl-bg)]">
      {/* Top bar: branded title + actions (mirrors TalkWithData / LibreChat layout) */}
      <div className="flex shrink-0 items-center justify-between border-b border-gray-200 bg-white px-6 py-3">
        <div className="flex min-w-0 items-center gap-3">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-[var(--color-rl-accent)]/15">
            <MessageSquare className="h-4 w-4 text-[var(--color-rl-accent-dark)]" />
          </div>
          <div className="min-w-0">
            <p className="truncate text-[14px] font-display-medium text-gray-900">{displayTitle}</p>
            <p className="flex items-center gap-1.5 text-[11px] text-gray-400">
              <span className="inline-block h-1.5 w-1.5 rounded-full bg-[var(--color-success)]" />
              {m.admin_widgets_test_page_title()}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="inline-flex items-center gap-1.5 rounded-full border border-gray-200 bg-white px-3 py-1.5 text-[12px] text-gray-700 klai-hover"
          >
            <Plus className="h-3.5 w-3.5" />
            Nieuw gesprek
          </button>
          <button
            type="button"
            onClick={() => {
              void navigator.clipboard.writeText(window.location.href)
            }}
            className="inline-flex items-center gap-1.5 rounded-full border border-gray-200 bg-white px-3 py-1.5 text-[12px] text-gray-700 klai-hover"
          >
            <Share2 className="h-3.5 w-3.5" />
            Deel link
          </button>
          <button
            type="button"
            onClick={() => window.close()}
            aria-label="Sluiten"
            className="ml-1 inline-flex h-8 w-8 items-center justify-center rounded-full text-gray-400 klai-hover"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      </div>

      {/* Notice strip */}
      <div className="shrink-0 border-b border-[var(--color-rl-border)] bg-[var(--color-rl-cream)] px-6 py-2">
        <p className="text-[11px] leading-snug text-[var(--color-rl-accent-dark)]">
          {m.admin_widgets_test_page_note_added()}
        </p>
      </div>

      {/* Live widget — real klai-chat.js running inline inside a static
          HTML iframe so document.currentScript resolves correctly */}
      <iframe
        src={previewUrl}
        title="Widget preview"
        className="flex-1 w-full border-0"
        sandbox="allow-scripts allow-same-origin allow-popups allow-forms"
      />
    </div>
  )
}
