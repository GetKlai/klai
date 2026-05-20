import { useEffect, useState } from 'react'
import { createFileRoute } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { ArrowLeft, CheckCircle, AlertTriangle, Info, Loader2 } from 'lucide-react'

// Public widget-test page — TWD's WidgetTest.vue equivalent. Loads
// klai-chat.js with the widget's data-widget-id so the floating chat
// bubble appears bottom-right exactly as it will on a customer site.
// URL: /widget-test?id=<widget_id>

export const Route = createFileRoute('/widget-test')({
  component: WidgetTestEmbedPage,
  validateSearch: (s: Record<string, unknown>) => ({
    id: typeof s.id === 'string' ? s.id : '',
  }),
})

interface PublicConfig {
  name?: string
  title: string
}

function WidgetTestEmbedPage() {
  const { id } = Route.useSearch()
  const [scriptStatus, setScriptStatus] = useState<'idle' | 'loading' | 'loaded' | 'error'>('idle')

  const configQuery = useQuery<PublicConfig>({
    queryKey: ['widget-test-public-config', id],
    enabled: !!id,
    queryFn: async () => {
      const res = await fetch(`/partner/v1/public-bot-config?id=${encodeURIComponent(id)}`)
      if (!res.ok) throw new Error(`config ${res.status}`)
      return (await res.json()) as PublicConfig
    },
    retry: false,
  })

  useEffect(() => {
    if (!id) return
    setScriptStatus('loading')
    const s = document.createElement('script')
    s.src = '/widget/klai-chat.js'
    s.setAttribute('data-widget-id', id)
    s.onload = () => setScriptStatus('loaded')
    s.onerror = () => setScriptStatus('error')
    document.body.appendChild(s)
    // Safety fallback: mark loaded after 2s in case onload doesn't fire.
    const t = setTimeout(() => {
      setScriptStatus((prev) => (prev === 'loading' ? 'loaded' : prev))
    }, 2000)
    return () => {
      clearTimeout(t)
      s.remove()
      // Best-effort cleanup of any klai-widget DOM the script attached.
      document.querySelectorAll('#klai-widget-root, .klai-bubble').forEach((n) => n.remove())
    }
  }, [id])

  if (!id) {
    return (
      <div className="mx-auto max-w-2xl px-6 py-16">
        <div className="rounded-xl border border-gray-200 bg-white p-6">
          <p className="text-sm text-[var(--color-destructive)]">
            Geen widget_id in URL. Gebruik <code>?id=&lt;widget_id&gt;</code>.
          </p>
        </div>
      </div>
    )
  }

  const widgetName = configQuery.data?.title || configQuery.data?.name || 'jouw widget'
  const origin = window.location.origin
  const snippet = `<script src="${origin}/widget/klai-chat.js" data-widget-id="${id}"></script>`

  return (
    <div className="min-h-screen bg-[var(--color-rl-bg)]">
      {/* Top nav */}
      <nav className="sticky top-0 z-50 border-b border-gray-200 bg-white/95 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-3xl items-center justify-between px-6">
          <div className="flex items-center gap-3">
            <img src="/klai-logo.svg" alt="Klai" className="h-5" />
            <span className="rounded bg-[var(--color-rl-cream)] px-2 py-1 text-xs font-medium text-gray-500">Widget test</span>
          </div>
          <button
            type="button"
            onClick={() => {
              if (window.history.length > 1) window.history.back()
              else window.close()
            }}
            className="inline-flex items-center gap-1.5 text-sm font-medium text-gray-600 hover:text-gray-900"
          >
            <ArrowLeft className="h-3.5 w-3.5" />
            Terug
          </button>
        </div>
      </nav>

      {/* Main content */}
      <main className="mx-auto max-w-2xl px-6 pb-16 pt-10">
        <h1 className="mb-2 text-2xl font-semibold text-gray-900">Test je widget</h1>
        <p className="mb-8 text-sm text-gray-500">
          Bekijk hoe de chat-bubble eruitziet op een externe website. De widget verschijnt rechtsonder.
        </p>

        <div className="rounded-xl border border-gray-200 bg-white p-6">
          {/* Status */}
          {scriptStatus === 'loading' && (
            <div className="mb-5 flex items-center gap-3 rounded-lg bg-[var(--color-rl-cream)] p-4">
              <Loader2 className="h-4 w-4 animate-spin text-gray-500" />
              <span className="text-sm text-gray-600">Widget laden…</span>
            </div>
          )}
          {scriptStatus === 'loaded' && (
            <div className="mb-5 flex items-center gap-3 rounded-lg border border-green-200 bg-green-50 p-4">
              <CheckCircle className="h-5 w-5 text-green-600" />
              <span className="text-sm text-green-700">Widget actief — klik op de chat-bubble rechtsonder.</span>
            </div>
          )}
          {scriptStatus === 'error' && (
            <div className="mb-5 flex items-center gap-3 rounded-lg border border-red-200 bg-red-50 p-4">
              <AlertTriangle className="h-5 w-5 text-[var(--color-destructive)]" />
              <span className="text-sm text-red-700">Kon widget-script niet laden.</span>
            </div>
          )}

          <p className="mb-5 text-sm leading-relaxed text-gray-600">
            Dit simuleert hoe <strong className="text-gray-900">{widgetName}</strong> eruitziet wanneer je hem embed op je site.
            De widget wordt direct in de pagina geïnjecteerd, zonder iframe.
          </p>

          {/* Embed code */}
          <div className="rounded-lg border border-gray-200 bg-[var(--color-rl-cream)] p-4">
            <div className="mb-2 text-[10px] font-semibold uppercase tracking-[0.08em] text-gray-400">Embed code</div>
            <code className="block break-all font-mono text-xs leading-relaxed text-gray-700">
              {snippet}
            </code>
          </div>

          {/* Hint */}
          <div className="mt-5 flex items-start gap-3 rounded-lg border border-[var(--color-rl-border)] bg-[var(--color-rl-accent)]/10 p-4">
            <Info className="mt-0.5 h-5 w-5 shrink-0 text-[var(--color-rl-accent-dark)]" />
            <p className="text-sm text-gray-700">
              Plak deze snippet vóór de <code className="rounded bg-white px-1 py-0.5 font-mono text-xs">&lt;/body&gt;</code>{' '}
              tag op je site. De widget verschijnt automatisch rechtsonder.
            </p>
          </div>
        </div>
      </main>
    </div>
  )
}
