import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, CheckCheck, Loader2, Megaphone } from 'lucide-react'
import { ApiError, apiFetch } from '@/lib/apiFetch'
import { useAuth } from '@/lib/auth'
import { useLocale } from '@/lib/locale'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  ListFrame,
  ListRow,
  ListRowContent,
  ListRowDescription,
  ListRowIcon,
  ListRowTitle,
  ListRowChevron,
} from '@/components/ui/list'
import { ListEmptyState, ListLoadingState } from '@/components/ui/list-state'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { QueryErrorState } from '@/components/ui/query-error-state'
import * as m from '@/paraglide/messages'

interface ProductUpdate {
  id: number
  title: string
  body: string
  commit_shas: string[]
  created_at: string
  read_at: string | null
  unread: boolean
}

interface ProductUpdatesResponse {
  items: ProductUpdate[]
  unread_count: number
}

const PRODUCT_UPDATES_QUERY_KEY = ['product-updates'] as const

export function ProductUpdatesPopover() {
  const auth = useAuth()
  const { locale } = useLocale()
  const queryClient = useQueryClient()
  const [selectedId, setSelectedId] = useState<number | null>(null)

  const updatesQuery = useQuery({
    queryKey: PRODUCT_UPDATES_QUERY_KEY,
    queryFn: () => apiFetch<ProductUpdatesResponse>('/api/app/product-updates'),
    enabled: auth.isAuthenticated,
  })

  const markReadMutation = useMutation({
    mutationFn: (id: number) => apiFetch(`/api/app/product-updates/${id}/read`, { method: 'POST' }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: PRODUCT_UPDATES_QUERY_KEY }),
  })

  const markAllReadMutation = useMutation({
    mutationFn: () => apiFetch('/api/app/product-updates/read-all', { method: 'POST' }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: PRODUCT_UPDATES_QUERY_KEY }),
  })

  const items = updatesQuery.data?.items ?? []
  const unreadCount = updatesQuery.data?.unread_count ?? 0
  const selected = items.find((item) => item.id === selectedId) ?? null

  function openUpdate(update: ProductUpdate) {
    setSelectedId(update.id)
    if (update.unread) markReadMutation.mutate(update.id)
  }

  function backToList() {
    setSelectedId(null)
  }

  return (
    <Popover onOpenChange={(open) => {
      if (!open) setSelectedId(null)
    }}>
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-label={
            unreadCount > 0
              ? m.product_updates_label_unread({ count: String(unreadCount) })
              : m.product_updates_label()
          }
          className="relative inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-gray-300 bg-white text-gray-600 outline-none transition-colors hover:bg-[var(--color-secondary)] hover:text-gray-900 focus-visible:ring-2 focus-visible:ring-[var(--color-rl-accent)]"
        >
          <Megaphone className="h-[18px] w-[18px]" strokeWidth={1.75} />
          {unreadCount > 0 && (
            <span
              aria-hidden="true"
              className="absolute -right-0.5 -top-0.5 h-2.5 w-2.5 rounded-full bg-[var(--color-success)] ring-2 ring-[var(--color-sidebar)]"
            />
          )}
        </button>
      </PopoverTrigger>

      <PopoverContent align="end" sideOffset={8} className="w-[min(24rem,calc(100vw-2rem))] p-0">
        {selected ? (
          <ProductUpdateDetail update={selected} locale={locale} onBack={backToList} />
        ) : (
          <div>
            <div className="flex items-start justify-between gap-3 border-b border-gray-200 px-4 py-3">
              <div className="min-w-0">
                <h2 className="text-sm font-medium text-gray-900">{m.product_updates_title()}</h2>
                <p className="mt-1 text-xs text-gray-400">
                  {unreadCount > 0 ? m.product_updates_unread_count({ count: String(unreadCount) }) : m.product_updates_all_read()}
                </p>
              </div>
              {unreadCount > 0 && (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="shrink-0"
                  onClick={() => markAllReadMutation.mutate()}
                  disabled={markAllReadMutation.isPending}
                >
                  {markAllReadMutation.isPending ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <CheckCheck className="h-4 w-4" />
                  )}
                  {m.product_updates_mark_all_read()}
                </Button>
              )}
            </div>

            {updatesQuery.isLoading ? (
              <ListLoadingState label={m.admin_shared_loading()} className="py-8" />
            ) : updatesQuery.error ? (
              updatesQuery.error instanceof ApiError && updatesQuery.error.status === 404 ? (
                <div className="px-4 py-8 text-center text-sm text-gray-400">
                  {m.product_updates_not_available()}
                </div>
              ) : (
                <QueryErrorState
                  error={new Error(m.product_updates_error())}
                  onRetry={() => void updatesQuery.refetch()}
                />
              )
            ) : items.length === 0 ? (
              <ListEmptyState
                title={m.product_updates_empty_title()}
                description={m.product_updates_empty_description()}
                className="py-8"
              />
            ) : (
              <div className="max-h-[26rem] overflow-y-auto">
                <ListFrame className="border-0">
                  {items.map((update) => (
                    <ProductUpdateRow
                      key={update.id}
                      update={update}
                      locale={locale}
                      onOpen={() => openUpdate(update)}
                    />
                  ))}
                </ListFrame>
              </div>
            )}
          </div>
        )}
      </PopoverContent>
    </Popover>
  )
}

function ProductUpdateRow({
  update,
  locale,
  onOpen,
}: {
  update: ProductUpdate
  locale: 'nl' | 'en'
  onOpen: () => void
}) {
  return (
    <ListRow asChild interactive className="px-4">
      <button type="button" onClick={onOpen} className="w-full text-left">
        <ListRowIcon>
          <Megaphone className="h-4 w-4" />
        </ListRowIcon>
        <ListRowContent>
          <div className="flex min-w-0 items-center gap-2">
            {update.unread && (
              <span className="h-2 w-2 shrink-0 rounded-full bg-[var(--color-success)]" />
            )}
            <ListRowTitle className="text-sm">{update.title}</ListRowTitle>
          </div>
          <ListRowDescription>{update.body}</ListRowDescription>
          <p className="mt-1 text-xs text-gray-400">
            {formatProductUpdateDate(update.created_at, locale)}
          </p>
        </ListRowContent>
        <ListRowChevron />
      </button>
    </ListRow>
  )
}

function ProductUpdateDetail({
  update,
  locale,
  onBack,
}: {
  update: ProductUpdate
  locale: 'nl' | 'en'
  onBack: () => void
}) {
  return (
    <div className="max-h-[32rem] overflow-y-auto px-4 py-4">
      <Button type="button" variant="ghost" size="sm" className="-ml-2 h-8 px-3" onClick={onBack}>
        <ArrowLeft className="h-4 w-4" />
        {m.product_updates_back()}
      </Button>

      <div className="mt-4 border-b border-gray-200 pb-4">
        <h2 className="text-base font-display-bold text-gray-900">{update.title}</h2>
        <p className="mt-1 text-xs text-gray-400">{formatProductUpdateDate(update.created_at, locale)}</p>
      </div>

      <p className="mt-4 whitespace-pre-wrap text-sm leading-relaxed text-gray-700">{update.body}</p>

      {update.commit_shas.length > 0 && (
        <div className="mt-5">
          <h3 className="text-xs font-medium text-gray-400">{m.product_updates_commits()}</h3>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {update.commit_shas.map((sha) => (
              <Badge key={sha} variant="outline" className="font-mono text-[11px] font-normal text-gray-500">
                {sha}
              </Badge>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function formatProductUpdateDate(value: string, locale: 'nl' | 'en') {
  return new Intl.DateTimeFormat(locale === 'nl' ? 'nl-NL' : 'en-US', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  }).format(new Date(value))
}
