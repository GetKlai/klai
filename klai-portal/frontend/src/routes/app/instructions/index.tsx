import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, Sliders } from 'lucide-react'

import { apiFetch } from '@/lib/apiFetch'
import { ProductGuard } from '@/components/layout/ProductGuard'
import { ActionTag } from '@/components/ui/action-tag'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { InlineDeleteConfirm } from '@/components/ui/inline-delete-confirm'
import {
  ListFrame,
  ListRow,
  ListRowActions,
  ListRowContent,
  ListRowDescription,
  ListRowTitle,
} from '@/components/ui/list'
import { ListEmptyState, ListLoadingState } from '@/components/ui/list-state'
import { PageHeader, PageIntro } from '@/components/ui/page-header'
import { Pagination } from '@/components/ui/pagination'
import { QueryErrorState } from '@/components/ui/query-error-state'
import { BorderedRowActionIconButton, RowActionGroup } from '@/components/ui/row-action'
import { SearchInput } from '@/components/ui/search-input'
import { useListControls } from '@/components/ui/use-list-controls'
import { useCurrentUser } from '@/hooks/useCurrentUser'
import * as m from '@/paraglide/messages'

export const Route = createFileRoute('/app/instructions/')({
  component: () => (
    <ProductGuard product="chat">
      <InstructionsPage />
    </ProductGuard>
  ),
})

interface Instruction {
  id: number
  name: string
  slug: string
  description: string | null
  prompt_text: string
  scope: 'org' | 'personal'
  created_by: string
  is_active: boolean
  created_at: string
  updated_at: string
}

interface KBPref {
  active_template_ids: number[] | null
}

export function InstructionsPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { data: currentUser } = useCurrentUser()
  const isAdmin = currentUser?.isAdmin ?? false
  const canManageOrgTemplates = currentUser?.hasCapability('templates.manage_org') ?? false
  const callerZitadelId = currentUser?.user_id

  const [confirmingDeleteId, setConfirmingDeleteId] = useState<number | null>(null)

  const instructionsQuery = useQuery<Instruction[]>({
    queryKey: ['app-instructions'],
    queryFn: async () => apiFetch<Instruction[]>('/api/app/templates'),
  })

  const prefQuery = useQuery<KBPref>({
    queryKey: ['kb-preference'],
    queryFn: async () => apiFetch<KBPref>('/api/app/account/kb-preference'),
  })

  const deleteMutation = useMutation({
    mutationFn: async (slug: string) =>
      apiFetch(`/api/app/templates/${slug}`, { method: 'DELETE' }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['app-instructions'] })
      void queryClient.invalidateQueries({ queryKey: ['app-instructions-for-bar'] })
      void queryClient.invalidateQueries({ queryKey: ['kb-preference'] })
      setConfirmingDeleteId(null)
    },
    onError: () => setConfirmingDeleteId(null),
  })

  const instructions = instructionsQuery.data ?? []
  const controls = useListControls(instructions, {
    pageSize: 10,
    filter: (t, q) => {
      const s = q.trim().toLowerCase()
      return t.name.toLowerCase().includes(s) || (t.description ?? '').toLowerCase().includes(s)
    },
  })

  const activeIds = new Set(prefQuery.data?.active_template_ids ?? [])

  function canMutate(t: Instruction): boolean {
    if (t.scope === 'org') return canManageOrgTemplates
    if (isAdmin) return true
    if (!callerZitadelId) return false
    return t.created_by === callerZitadelId
  }

  const createLabel = canManageOrgTemplates
    ? m.instructions_list_create_button()
    : m.instructions_list_create_personal_button()

  return (
    <div className="mx-auto max-w-3xl px-6 pt-4 pb-10 space-y-6">
      <PageHeader
        title={m.instructions_page_title()}
        count={instructionsQuery.data ? instructions.length : undefined}
        description={m.instructions_page_subtitle()}
        actions={
          <Button
            type="button"
            size="sm"
            onClick={() => void navigate({ to: '/app/instructions/new' })}
          >
            <Plus className="h-4 w-4" />
            {createLabel}
          </Button>
        }
      />

      {/* Korte uitleg boven de lijst — geen kader, gewoon tekst. */}
      <PageIntro>
        <p>{m.instructions_intro_body()}</p>
        <p>
          <span className="text-gray-500">{m.instructions_intro_examples_heading()}</span>{' '}
          {m.instructions_intro_example_1()} {m.instructions_intro_example_2()}
        </p>
        <p>{m.instructions_intro_invoke()}</p>
      </PageIntro>

      {instructionsQuery.isLoading && (
        <ListFrame aria-busy="true">
          <ListLoadingState label={m.instructions_list_loading()} />
        </ListFrame>
      )}

      {instructionsQuery.isError && (
        <QueryErrorState error={instructionsQuery.error ?? new Error('Unknown error')} onRetry={() => void instructionsQuery.refetch()} />
      )}

      {instructionsQuery.data && instructionsQuery.data.length === 0 && (
        <div className="rounded-lg border border-dashed border-gray-200 py-16 text-center">
          <Sliders className="h-10 w-10 text-gray-300 mx-auto mb-3" />
          <p className="text-base font-medium text-gray-900">{m.instructions_empty_title()}</p>
          <p className="text-sm text-gray-400 mt-1 max-w-md mx-auto">
            {m.instructions_empty_description()}
          </p>
          <Button
            type="button"
            onClick={() => void navigate({ to: '/app/instructions/new' })}
            size="sm"
            className="mt-4"
          >
            <Plus className="h-4 w-4" />
            {m.instructions_empty_cta()}
          </Button>
        </div>
      )}

      {instructionsQuery.data && instructions.length > 0 && (
        <>
          {controls.showSearch && (
            <div className="max-w-sm">
              <SearchInput
                type="search"
                value={controls.query}
                onChange={(e) => controls.setQuery(e.target.value)}
                placeholder={m.instructions_search_placeholder()}
                aria-label={m.instructions_search_placeholder()}
              />
            </div>
          )}
          {controls.filteredCount === 0 ? (
            <ListFrame>
              <ListEmptyState title={m.list_no_results()} />
            </ListFrame>
          ) : (
          <ListFrame>
          {controls.pageItems.map((t) => {
            const mutateAllowed = canMutate(t)
            const isConfirming = confirmingDeleteId === t.id
            const isPending = deleteMutation.isPending && confirmingDeleteId === t.id
            const active = activeIds.has(t.id)

            const openDetail = () =>
              void navigate({
                to: '/app/instructions/$slug/edit',
                params: { slug: t.slug },
              })
            return (
              <ListRow
                key={t.id}
                role="button"
                tabIndex={0}
                interactive
                onClick={openDetail}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault()
                    openDetail()
                  }
                }}
                className="grid items-center gap-4 px-4 py-4 sm:grid-cols-[minmax(0,1fr)_auto]"
              >
                <ListRowContent>
                  <div className="flex items-center gap-2 flex-wrap">
                    <ListRowTitle>{t.name}</ListRowTitle>
                    <Badge variant="secondary">
                      {t.scope === 'org'
                        ? m.instructions_list_scope_org()
                        : m.instructions_list_scope_personal()}
                    </Badge>
                    {active && (
                      <ActionTag state="open">
                        {m.instructions_list_active_label()}
                      </ActionTag>
                    )}
                  </div>
                  {t.description && (
                    <ListRowDescription>{t.description}</ListRowDescription>
                  )}
                </ListRowContent>

                <ListRowActions
                  className="self-center justify-self-end"
                  onClick={(e) => e.stopPropagation()}
                >
                <InlineDeleteConfirm
                  isConfirming={isConfirming}
                  isPending={isPending}
                  label={m.instructions_list_delete_confirm()}
                  cancelLabel={m.instructions_form_cancel()}
                  onConfirm={() => deleteMutation.mutate(t.slug)}
                  onCancel={() => setConfirmingDeleteId(null)}
                >
                  <RowActionGroup>
                    <BorderedRowActionIconButton
                      onClick={() => void navigate({ to: '/app/instructions/$slug/edit', params: { slug: t.slug } })}
                      label={m.instructions_list_edit_label()}
                      action="edit"
                    />
                    <BorderedRowActionIconButton
                      disabled={!mutateAllowed}
                      onClick={() => setConfirmingDeleteId(t.id)}
                      title={
                        mutateAllowed
                          ? m.instructions_list_delete_label()
                          : m.instructions_form_scope_org_disabled_tooltip()
                      }
                      label={m.instructions_list_delete_label()}
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
          {controls.showPagination && (
            <Pagination
              page={controls.page}
              pageCount={controls.pageCount}
              onPageChange={controls.setPage}
            />
          )}
        </>
      )}
    </div>
  )
}
