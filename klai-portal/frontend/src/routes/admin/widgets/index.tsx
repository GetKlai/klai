import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useState } from 'react'
import {
  Plus,
  Pencil,
  ExternalLink,
  Trash2,
  MessageSquare,
} from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { InlineDeleteConfirm } from '@/components/ui/inline-delete-confirm'
import { QueryErrorState } from '@/components/ui/query-error-state'
import * as m from '@/paraglide/messages'
import { useWidgets, useDeleteWidget } from './-hooks'
import type { WidgetResponse } from './-types'

// Matches the canonical admin-list pattern used by /admin/templates:
// divider-rows, no leading icon, name + optional badge inline, optional
// description below, action icons right-aligned with gray-400 idle and
// semantic-colour hover. Same paddings, same dividers, same hover.

export const Route = createFileRoute('/admin/widgets/')({
  component: WidgetsPage,
})

function WidgetsPage() {
  const navigate = useNavigate()
  const { data, isLoading, error, refetch } = useWidgets()
  const deleteMutation = useDeleteWidget()
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null)

  const widgets: WidgetResponse[] = Array.isArray(data) ? data : []

  return (
    <div className="mx-auto max-w-3xl px-6 py-10">
      <div className="flex items-center justify-between mb-2">
        <h1 className="page-title text-[26px] font-display-bold text-gray-900">
          {m.admin_widgets_title()}
        </h1>
        <Button
          type="button"
          onClick={() => void navigate({ to: '/admin/widgets/new' })}
          size="sm"
        >
          <Plus className="h-4 w-4" />
          {m.admin_widgets_create()}
        </Button>
      </div>
      <p className="text-sm text-gray-400 mb-6 max-w-2xl">
        {m.admin_widgets_subtitle()}
      </p>

      {error && (
        <QueryErrorState
          error={error instanceof Error ? error : new Error(String(error))}
          onRetry={() => void refetch()}
        />
      )}

      {isLoading && !error && (
        <div className="space-y-3" aria-busy="true">
          {[0, 1, 2].map((i) => (
            <div key={i} className="h-14 rounded-lg bg-gray-50 animate-pulse" />
          ))}
        </div>
      )}

      {!isLoading && !error && widgets.length === 0 && (
        <div className="rounded-lg border border-dashed border-gray-200 py-16 text-center">
          <MessageSquare className="h-10 w-10 text-gray-300 mx-auto mb-3" />
          <p className="text-base font-medium text-gray-900">
            {m.admin_widgets_empty()}
          </p>
          <p className="text-sm text-gray-400 mt-1 max-w-md mx-auto">
            {m.admin_widgets_empty_description()}
          </p>
          <Button
            type="button"
            onClick={() => void navigate({ to: '/admin/widgets/new' })}
            size="sm"
            className="mt-4"
          >
            <Plus className="h-4 w-4" />
            {m.admin_widgets_create()}
          </Button>
        </div>
      )}

      {!isLoading && !error && widgets.length > 0 && (
        <div className="divide-y divide-gray-200 border-t border-b border-gray-200">
          {widgets.map((w) => {
            const isConfirming = confirmDeleteId === String(w.id)
            const goToDetail = () =>
              void navigate({
                to: '/admin/widgets/$id',
                params: { id: String(w.id) },
              })

            return (
              <div
                key={w.id}
                role="button"
                tabIndex={0}
                onClick={goToDetail}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault()
                    goToDetail()
                  }
                }}
                className="flex items-start gap-4 py-3.5 px-2 cursor-pointer klai-hover"
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-sm font-medium text-gray-900 truncate">
                      {w.name}
                    </span>
                    <Badge variant="secondary">
                      {w.kb_access_count === 1
                        ? '1 kennisbank'
                        : `${w.kb_access_count} kennisbanken`}
                    </Badge>
                  </div>
                  {w.description && (
                    <p className="mt-1 text-sm text-gray-400 truncate">
                      {w.description}
                    </p>
                  )}
                </div>

                <div onClick={(e) => e.stopPropagation()}>
                <InlineDeleteConfirm
                  isConfirming={isConfirming}
                  isPending={deleteMutation.isPending && isConfirming}
                  label={m.admin_widgets_delete_confirm({ name: w.name })}
                  cancelLabel={m.admin_users_cancel()}
                  onConfirm={() => {
                    deleteMutation.mutate(String(w.id))
                    setConfirmDeleteId(null)
                  }}
                  onCancel={() => setConfirmDeleteId(null)}
                >
                  <div className="flex items-center justify-end gap-1">
                    <Button
                      type="button"
                      onClick={() =>
                        window.open(
                          `/bot/${w.widget_id}`,
                          '_blank',
                          'noopener,noreferrer',
                        )
                      }
                      aria-label={`Open ${w.name}`}
                      title="Open bot in nieuw tabblad"
                      variant="ghost"
                      size="icon"
                      className="h-8 w-8 rounded-md text-gray-400 hover:text-gray-900"
                    >
                      <ExternalLink className="h-4 w-4" />
                    </Button>
                    <Button
                      type="button"
                      onClick={goToDetail}
                      aria-label={`Bewerk ${w.name}`}
                      title="Bewerken"
                      variant="ghost"
                      size="icon"
                      className="h-8 w-8 rounded-md text-gray-400 hover:text-gray-900"
                    >
                      <Pencil className="h-4 w-4" />
                    </Button>
                    <Button
                      type="button"
                      onClick={() => setConfirmDeleteId(String(w.id))}
                      aria-label={`Verwijder ${w.name}`}
                      title="Verwijderen"
                      variant="ghost"
                      size="icon"
                      className="h-8 w-8 rounded-md text-gray-400 hover:text-[var(--color-destructive)]"
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </div>
                </InlineDeleteConfirm>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
