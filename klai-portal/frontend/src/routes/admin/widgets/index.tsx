import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useState } from 'react'
import {
  Plus,
  Loader2,
  Eye,
  MessageSquare,
  Trash2,
} from 'lucide-react'
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

function formatRelativeTime(isoString: string | null): string {
  if (!isoString) return '—'
  return datetime(getLocale(), isoString, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

// Pure luminance check so the avatar foreground stays legible on any
// brand colour the admin sets. Matches the backend WCAG cutoff used
// in partner.py::_readable_text_color (0.179 threshold).
function readableForegroundOn(hex: string): string {
  const cleaned = hex.replace('#', '').padStart(6, '0')
  const r = parseInt(cleaned.slice(0, 2), 16) / 255
  const g = parseInt(cleaned.slice(2, 4), 16) / 255
  const b = parseInt(cleaned.slice(4, 6), 16) / 255
  const ch = (c: number) =>
    c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4)
  const lum = 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)
  return lum > 0.179 ? '#191918' : '#ffffff'
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
        <ul className="space-y-3">
          {widgets.map((w) => {
            const isConfirming = confirmDeleteId === String(w.id)
            const primary = w.widget_config?.primary_color || '#fcaa2d'
            const fg = readableForegroundOn(primary)
            return (
              <li key={w.id}>
                <InlineDeleteConfirm
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
                    className="group flex items-start gap-3 rounded-xl border border-gray-200 bg-white px-4 py-3.5 cursor-pointer klai-hover"
                  >
                    <span
                      aria-hidden
                      className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg"
                      style={{ backgroundColor: primary, color: fg }}
                    >
                      <MessageSquare className="h-5 w-5" strokeWidth={1.75} />
                    </span>
                    <div className="flex-1 min-w-0">
                      <p className="truncate text-[15px] font-display-medium text-gray-900">
                        {w.name}
                      </p>
                      {w.description && (
                        <p className="truncate text-[13px] text-gray-400">
                          {w.description}
                        </p>
                      )}
                      <p className="mt-1 flex items-center gap-2 text-xs text-gray-400">
                        <span>
                          {w.kb_access_count}{' '}
                          {w.kb_access_count === 1
                            ? 'kennisbank'
                            : 'kennisbanken'}
                        </span>
                        <span>·</span>
                        <span>
                          Laatst gebruikt {formatRelativeTime(w.last_used_at)}
                        </span>
                      </p>
                    </div>
                    <div
                      className="flex items-center gap-2 pt-1"
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
                          navigate({
                            to: '/admin/widgets/$id',
                            params: { id: String(w.id) },
                          })
                        }
                        aria-label={w.name}
                        className="inline-flex items-center justify-center text-[var(--color-accent)] transition-opacity hover:opacity-70"
                      >
                        <Eye className="h-4 w-4" />
                      </button>
                    </div>
                  </div>
                </InlineDeleteConfirm>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
