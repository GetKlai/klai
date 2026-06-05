import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useState } from 'react'
import { Plus } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { InlineDeleteConfirm } from '@/components/ui/inline-delete-confirm'
import {
  ListFrame,
  ListHeader,
  ListRow,
  ListRowActions,
  ListRowContent,
  ListRowDescription,
  ListRowTitle,
} from '@/components/ui/list'
import { ListEmptyState, ListLoadingState } from '@/components/ui/list-state'
import { PageHeader, PageIntro } from '@/components/ui/page-header'
import { QueryErrorState } from '@/components/ui/query-error-state'
import { BorderedRowActionIconButton, RowActionGroup } from '@/components/ui/row-action'
import * as m from '@/paraglide/messages'
import { useWidgets, useDeleteWidget } from './-hooks'
import type { WidgetResponse } from './-types'

export const Route = createFileRoute('/admin/widgets/')({
  component: WidgetsPage,
})

const widgetsListGrid =
  'md:grid-cols-[minmax(240px,1fr)_minmax(120px,0.4fr)_112px]'

function WidgetsPage() {
  const navigate = useNavigate()
  const { data, isLoading, error, refetch } = useWidgets()
  const deleteMutation = useDeleteWidget()
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null)

  const widgets: WidgetResponse[] = Array.isArray(data) ? data : []

  return (
    <div className="mx-auto max-w-4xl px-6 pt-4 pb-10 space-y-6">
      <PageHeader
        title={m.admin_widgets_title()}
        count={!isLoading && !error ? widgets.length : undefined}
        description={m.admin_section_widgets_description()}
        actions={
          <Button
            type="button"
            onClick={() => void navigate({ to: '/admin/widgets/new' })}
            size="sm"
          >
            <Plus className="h-4 w-4" />
            {m.admin_widgets_create()}
          </Button>
        }
      />

      <PageIntro>
        <p>{m.admin_widgets_subtitle()}</p>
      </PageIntro>

      {error && (
        <QueryErrorState
          error={error instanceof Error ? error : new Error(String(error))}
          onRetry={() => void refetch()}
        />
      )}

      {isLoading && !error && (
        <ListLoadingState label={m.admin_widgets_loading()} />
      )}

      {!isLoading && !error && widgets.length === 0 && (
        <ListEmptyState
          title={m.admin_widgets_empty()}
          description={m.admin_widgets_empty_description()}
        />
      )}

      {!isLoading && !error && widgets.length > 0 && (
        <ListFrame data-help-id="admin-widgets-table">
          <ListHeader className={`hidden gap-x-3 ${widgetsListGrid} md:grid`}>
            <span>{m.admin_widgets_col_name()}</span>
            <span>{m.admin_widgets_col_kb_access()}</span>
            <span className="text-right">{m.admin_widgets_col_actions()}</span>
          </ListHeader>

          {widgets.map((w) => {
            const isConfirming = confirmDeleteId === String(w.id)
            const goToDetail = () =>
              void navigate({
                to: '/admin/widgets/$id',
                params: { id: String(w.id) },
              })

            return (
              <ListRow
                key={w.id}
                role="button"
                tabIndex={0}
                interactive
                confirming={isConfirming}
                onClick={goToDetail}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault()
                    goToDetail()
                  }
                }}
                className={`grid items-center gap-x-3 gap-y-3 px-4 py-4 ${widgetsListGrid}`}
              >
                <ListRowContent>
                  <ListRowTitle>{w.name}</ListRowTitle>
                  {w.description && (
                    <ListRowDescription>{w.description}</ListRowDescription>
                  )}
                </ListRowContent>

                <div>
                  <Badge variant="secondary">
                    {w.kb_access_count === 1
                      ? m.admin_widgets_kb_access_one()
                      : m.admin_widgets_kb_access_many({ count: w.kb_access_count })}
                  </Badge>
                </div>

                <ListRowActions
                  className="self-center justify-self-end"
                  onClick={(e) => e.stopPropagation()}
                >
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
                    <RowActionGroup>
                      <BorderedRowActionIconButton
                        type="button"
                        onClick={() =>
                          window.open(
                            `/bot/${w.widget_id}`,
                            '_blank',
                            'noopener,noreferrer',
                          )
                        }
                        label={m.admin_widgets_action_open()}
                        action="open"
                      />
                      <BorderedRowActionIconButton
                        type="button"
                        onClick={goToDetail}
                        label={m.admin_widgets_action_edit()}
                        action="edit"
                      />
                      <BorderedRowActionIconButton
                        type="button"
                        onClick={() => setConfirmDeleteId(String(w.id))}
                        label={m.admin_widgets_delete_button()}
                        action="delete"
                      />
                    </RowActionGroup>
                  </InlineDeleteConfirm>
                </ListRowActions>
              </ListRow>
            )
          })}
        </ListFrame>
      )}
    </div>
  )
}
