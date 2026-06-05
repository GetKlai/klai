import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from '@/lib/auth'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Loader2, Mic, Video } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { ListFrame } from '@/components/ui/list'
import { ListEmptyState, ListLoadingState } from '@/components/ui/list-state'
import { PageHeader, PageIntro } from '@/components/ui/page-header'
import { Pagination } from '@/components/ui/pagination'
import { QueryErrorState } from '@/components/ui/query-error-state'
import { SearchInput } from '@/components/ui/search-input'
import { useListControls } from '@/components/ui/use-list-controls'
import * as m from '@/paraglide/messages'
import { apiFetch } from '@/lib/apiFetch'
import { ProductGuard } from '@/components/layout/ProductGuard'
import { TranscriptionTable } from './_components/TranscriptionTable'
import type {
  TranscriptionItem,
  TranscriptionListResponse,
  MeetingListItem,
  MeetingListResponse,
  UnifiedItem,
} from './_types'

type TranscribeSearch = { search?: string }

export const Route = createFileRoute('/app/transcribe/')({
  validateSearch: (search: Record<string, unknown>): TranscribeSearch => ({
    search: typeof search.search === 'string' && search.search ? search.search : undefined,
  }),
  component: () => (
    <ProductGuard product="scribe">
      <TranscribePage />
    </ProductGuard>
  ),
})

const SCRIBE_BASE = '/api/scribe/v1'
const BOTS_BASE = '/api/bots'
const ACTIVE_MEETING_STATUSES = ['pending', 'joining', 'recording', 'stopping', 'processing']

function toUnified(item: TranscriptionItem): UnifiedItem {
  const statusMap: Record<string, string> = { transcribed: 'done', processing: 'processing', failed: 'failed' }
  return {
    id: item.id,
    source: 'upload',
    title: item.name,
    text: item.text,
    language: item.language,
    duration_seconds: item.duration_seconds,
    created_at: item.created_at,
    status: statusMap[item.status] ?? 'done',
    uploadName: item.name,
    has_summary: item.has_summary,
  }
}

function meetingToUnified(item: MeetingListItem): UnifiedItem {
  const statusMap: Record<string, string> = { completed: 'done', stopping: 'processing' }
  return {
    id: item.id,
    source: 'meeting',
    title: item.meeting_title ?? item.meeting_url,
    text: item.transcript_text,
    language: item.language,
    duration_seconds: item.duration_seconds,
    created_at: item.created_at,
    status: statusMap[item.status] ?? item.status,
    meeting_url: item.meeting_url,
    platform: item.platform,
  }
}

function TranscribePage() {
  const auth = useAuth()
  const queryClient = useQueryClient()
  const navigate = useNavigate({ from: '/app/transcribe/' })

  function handleNavigateToDetail(item: UnifiedItem) {
    if (item.source === 'upload') {
      void navigate({ to: '/app/transcribe/$transcriptionId', params: { transcriptionId: item.id } })
    } else {
      void navigate({ to: '/app/meetings/$meetingId', params: { meetingId: String(item.id) } })
    }
  }

  const { search: searchParam } = Route.useSearch()
  const search = searchParam ?? ''

  const { data: transcriptionsData, isLoading: transcriptionsLoading, error: transcriptionsError, refetch: refetchTranscriptions } = useQuery<TranscriptionListResponse>({
    queryKey: ['transcriptions'],
    queryFn: async () => apiFetch<TranscriptionListResponse>(`${SCRIBE_BASE}/transcriptions?limit=50`),
    enabled: auth.isAuthenticated,
  })

  const { data: meetingsData, isLoading: meetingsLoading, error: meetingsError, refetch: refetchMeetings } = useQuery<MeetingListResponse>({
    queryKey: ['meetings'],
    queryFn: async () => apiFetch<MeetingListResponse>(`${BOTS_BASE}/meetings?limit=50`),
    enabled: auth.isAuthenticated,
    refetchInterval: (query) => {
      const data = query.state.data
      if (!data) return false
      return data.items.some((mtg) => ACTIVE_MEETING_STATUSES.includes(mtg.status)) ? 5000 : false
    },
  })

  const allItems: UnifiedItem[] = [
    ...(transcriptionsData?.items ?? []).map(toUnified),
    ...(meetingsData?.items ?? []).map(meetingToUnified),
  ].sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())

  const isLoading = transcriptionsLoading || meetingsLoading
  const queryError = transcriptionsError || meetingsError
  const hasActiveMeetings = (meetingsData?.items ?? []).some((mtg) =>
    ACTIVE_MEETING_STATUSES.includes(mtg.status),
  )

  const deleteUploadMutation = useMutation({
    mutationFn: async (id: string) => {
      await apiFetch(`${SCRIBE_BASE}/transcriptions/${id}`, { method: 'DELETE' })
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['transcriptions'] }),
  })

  const deleteMeetingMutation = useMutation({
    mutationFn: async (id: string) => {
      await apiFetch(`${BOTS_BASE}/meetings/${id}`, { method: 'DELETE' })
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['meetings'] }),
  })

  const renameMutation = useMutation({
    mutationFn: async ({ id, name }: { id: string; name: string | null }) => {
      await apiFetch(`${SCRIBE_BASE}/transcriptions/${id}`, {
        method: 'PATCH',
        body: JSON.stringify({ name }),
      })
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['transcriptions'] }),
  })

  const stopMutation = useMutation({
    mutationFn: async (id: string) => {
      await apiFetch(`${BOTS_BASE}/meetings/${id}/stop`, { method: 'POST' })
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['meetings'] }),
  })

  const retryMutation = useMutation({
    mutationFn: async (id: string) => {
      await apiFetch(`${SCRIBE_BASE}/transcriptions/${id}/retry`, { method: 'POST' })
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['transcriptions'] }),
  })

  const controls = useListControls(allItems, {
    pageSize: 10,
    query: search,
    onQueryChange: (value) => void navigate({ search: { search: value || undefined } }),
    filter: (item, q) => {
      const s = q.toLowerCase()
      return Boolean(
        item.text?.toLowerCase().includes(s) ||
          item.title?.toLowerCase().includes(s) ||
          item.meeting_url?.toLowerCase().includes(s),
      )
    },
  })

  const totalCount = (transcriptionsData?.total ?? 0) + (meetingsData?.total ?? 0)

  return (
    <div className="mx-auto max-w-4xl px-6 pt-4 pb-10 space-y-6">
      <PageHeader
        title={m.app_tool_transcribe_title()}
        count={!isLoading && !queryError ? totalCount : undefined}
        description={m.app_transcribe_subtitle()}
        actions={
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => void navigate({ to: '/app/meetings/start' })}
            >
              <Video className="mr-2 h-4 w-4" />
              {m.app_transcribe_new_meeting()}
            </Button>
            <Button
              size="sm"
              data-help-id="transcribe-add"
              onClick={() => void navigate({ to: '/app/transcribe/add' })}
            >
              <Mic className="mr-2 h-4 w-4" />
              {m.app_transcribe_new_audio()}
            </Button>
          </div>
        }
      />

      <PageIntro>
        <p>{m.app_transcribe_intro_body()}</p>
        <p>{m.app_transcribe_intro_meetings()}</p>
      </PageIntro>

      {hasActiveMeetings && (
        <div className="flex items-center gap-2 text-sm text-gray-400">
          <Loader2 className="h-4 w-4 animate-spin" />
          <span>{m.app_transcribe_auto_refresh()}</span>
        </div>
      )}

      {isLoading ? (
        <ListFrame>
          <ListLoadingState label={m.app_transcribe_processing()} />
        </ListFrame>
      ) : queryError ? (
        <QueryErrorState error={queryError instanceof Error ? queryError : new Error(String(queryError))} onRetry={() => { void refetchTranscriptions(); void refetchMeetings() }} />
      ) : allItems.length === 0 ? (
        <ListFrame data-help-id="transcribe-list">
          <ListEmptyState
            title={m.app_transcribe_empty_heading()}
            description={m.app_transcribe_empty_body()}
          />
        </ListFrame>
      ) : (
        <>
          {controls.showSearch && (
            <div className="max-w-sm">
              <SearchInput
                type="search"
                value={controls.query}
                onChange={(e) => controls.setQuery(e.target.value)}
                placeholder={m.app_transcribe_search_placeholder()}
                aria-label={m.app_transcribe_search_placeholder()}
              />
            </div>
          )}
          {controls.filteredCount === 0 ? (
            <ListFrame>
              <ListEmptyState title={m.app_transcribe_search_empty()} />
            </ListFrame>
          ) : (
            <TranscriptionTable
              items={controls.pageItems}
              onNavigateToDetail={handleNavigateToDetail}
              onRename={(id, name) => renameMutation.mutate({ id, name })}
              isRenaming={renameMutation.isPending}
              renamingId={renameMutation.variables?.id}
              onDeleteUpload={(id) => deleteUploadMutation.mutate(id)}
              isDeletingUpload={deleteUploadMutation.isPending}
              deletingUploadId={deleteUploadMutation.variables}
              onDeleteMeeting={(id) => deleteMeetingMutation.mutate(id)}
              isDeletingMeeting={deleteMeetingMutation.isPending}
              deletingMeetingId={deleteMeetingMutation.variables}
              onStop={(id) => stopMutation.mutate(id)}
              isStopping={stopMutation.isPending}
              stoppingId={stopMutation.variables}
              onRetry={(id) => retryMutation.mutate(id)}
              isRetrying={retryMutation.isPending}
              retryingId={retryMutation.variables}
            />
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
