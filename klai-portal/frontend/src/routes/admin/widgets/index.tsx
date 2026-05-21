import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useState } from 'react'
import { Plus, Loader2, Pencil, MessageSquare, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { InlineDeleteConfirm } from '@/components/ui/inline-delete-confirm'
import { QueryErrorState } from '@/components/ui/query-error-state'
import * as m from '@/paraglide/messages'
import { getLocale } from '@/paraglide/runtime'
import { datetime } from '@/paraglide/registry'
import { useWidgets, useDeleteWidget } from './-hooks'
import type { WidgetResponse } from './-types'

export const Route = createFileRoute('/admin/widgets/')({
  component: WidgetsPage,
})

function formatRelativeTime(isoString: string | null): string | null {
  if (!isoString) return null
  return datetime(getLocale(), isoString, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function WidgetsPage() {
  const navigate = useNavigate()
  const { data, isLoading, error, refetch } = useWidgets()
  const deleteMutation = useDeleteWidget()
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null)

  const widgets: WidgetResponse[] = Array.isArray(data) ? data : []

  return (
    <div className="mx-auto max-w-3xl px-6 py-10 space-y-6">
      <div className="flex items-start justify-between">
        <h1 className="page-title text-[26px] font-display-bold text-gray-900">
          {m.admin_widgets_title()}
        </h1>
        <Button
          size="sm"
          onClick={() => void navigate({ to: '/admin/widgets/new' })}
        >
          <Plus className="h-4 w-4 mr-2" />
          {m.admin_widgets_create()}
        </Button>
      </div>

      {error ? (
        <QueryErrorState
          error={error instanceof Error ? error : new Error(String(error))}
          onRetry={() => void refetch()}
        />
      ) : isLoading ? (
        <p className="py-8 text-sm text-gray-400">
          <Loader2 className="inline h-4 w-4 animate-spin mr-2" />
          {m.admin_widgets_loading()}
        </p>
      ) : widgets.length === 0 ? (
        <div className="py-12 text-center space-y-3">
          <p className="text-sm font-medium text-gray-900">
            {m.admin_widgets_empty()}
          </p>
          <p className="text-sm text-gray-400">
            {m.admin_widgets_empty_description()}
          </p>
        </div>
      ) : (
        <div className="divide-y divide-gray-200 border-t border-b border-gray-200">
          {widgets.map((w) => {
            const isConfirming = confirmDeleteId === String(w.id)
            return (
              <InlineDeleteConfirm
                key={w.id}
                isConfirming={isConfirming}
                isPending={deleteMutation.isPending}
                label={m.admin_widgets_delete_confirm({ name: w.name })}
                cancelLabel={m.admin_users_cancel()}
                onConfirm={() => {
                  deleteMutation.mutate(String(w.id))
                  setConfirmDeleteId(null)
                }}
                onCancel={() => setConfirmDeleteId(null)}
              >
                <div
                  role="button"
                  tabIndex={0}
                  onClick={() =>
                    void navigate({
                      to: '/admin/widgets/$id',
                      params: { id: String(w.id) },
                    })
                  }
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault()
                      void navigate({
                        to: '/admin/widgets/$id',
                        params: { id: String(w.id) },
                      })
                    }
                  }}
                  className="group flex items-center gap-3 px-2 py-3.5 cursor-pointer klai-hover"
                >
                  {/* Leading icon — bare glyph, no background box */}
                  <div className="flex h-8 w-8 shrink-0 items-center justify-center text-gray-400">
                    <MessageSquare className="h-5 w-5" strokeWidth={1.75} />
                  </div>
                  <span className="flex-1 min-w-0 text-[15px] font-display text-gray-900 truncate">
                    {w.name}
                  </span>
                  <span className="hidden sm:inline text-gray-400 text-sm whitespace-nowrap">
                    {w.kb_access_count}{' '}
                    {w.kb_access_count === 1 ? 'kennisbank' : 'kennisbanken'}
                  </span>
                  {(() => {
                    const formatted = formatRelativeTime(w.last_used_at)
                    return formatted ? (
                      <span className="hidden md:inline text-gray-400 text-sm whitespace-nowrap tabular-nums">
                        {formatted}
                      </span>
                    ) : null
                  })()}
                  <div
                    className="flex items-center gap-2 shrink-0"
                    onClick={(e) => e.stopPropagation()}
                  >
                    <button
                      type="button"
                      onClick={() =>
                        window.open(
                          `/bot/${w.widget_id}`,
                          '_blank',
                          'noopener,noreferrer',
                        )
                      }
                      aria-label={`Test ${w.name}`}
                      title="Test bot"
                      className="inline-flex items-center justify-center text-gray-500 transition-opacity hover:opacity-70"
                    >
                      <MessageSquare className="h-4 w-4" />
                    </button>
                    <button
                      type="button"
                      onClick={() => setConfirmDeleteId(String(w.id))}
                      aria-label={`Delete ${w.name}`}
                      className="inline-flex items-center justify-center text-[var(--color-destructive)] transition-opacity hover:opacity-70"
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
                    <button
                      type="button"
                      onClick={() =>
                        void navigate({
                          to: '/admin/widgets/$id',
                          params: { id: String(w.id) },
                        })
                      }
                      aria-label={`Bewerk ${w.name}`}
                      title="Bewerk widget"
                      className="inline-flex items-center justify-center text-[var(--color-warning)] transition-opacity hover:opacity-70"
                    >
                      <Pencil className="h-4 w-4" />
                    </button>
                  </div>
                </div>
              </InlineDeleteConfirm>
            )
          })}
        </div>
      )}
    </div>
  )
}
