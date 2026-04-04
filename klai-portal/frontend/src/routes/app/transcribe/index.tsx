import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Loader2, Mic, Video } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { QueryErrorState } from '@/components/ui/query-error-state'
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

const SCRIBE_BASE = '/scribe/v1'
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
  const token = auth.user?.access_token
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
    queryFn: async () => apiFetch<TranscriptionListResponse>(`${SCRIBE_BASE}/transcriptions?limit=50`, token),
    enabled: !!token,
  })

  const { data: meetingsData, isLoading: meetingsLoading, error: meetingsError, refetch: refetchMeetings } = useQuery<MeetingListResponse>({
    queryKey: ['meetings'],
    queryFn: async () => apiFetch<MeetingListResponse>(`${BOTS_BASE}/meetings?limit=50`, token),
    enabled: !!token,
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
      await apiFetch(`${SCRIBE_BASE}/transcriptions/${id}`, token, { method: 'DELETE' })
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['transcriptions'] }),
  })

  const deleteMeetingMutation = useMutation({
    mutationFn: async (id: string) => {
      await apiFetch(`${BOTS_BASE}/meetings/${id}`, token, { method: 'DELETE' })
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['meetings'] }),
  })

  const renameMutation = useMutation({
    mutationFn: async ({ id, name }: { id: string; name: string | null }) => {
      await apiFetch(`${SCRIBE_BASE}/transcriptions/${id}`, token, {
        method: 'PATCH',
        body: JSON.stringify({ name }),
      })
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['transcriptions'] }),
  })

  const stopMutation = useMutation({
    mutationFn: async (id: string) => {
      await apiFetch(`${BOTS_BASE}/meetings/${id}/stop`, token, { method: 'POST' })
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['meetings'] }),
  })

  const retryMutation = useMutation({
    mutationFn: async (id: string) => {
      await apiFetch(`${SCRIBE_BASE}/transcriptions/${id}/retry`, token, { method: 'POST' })
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['transcriptions'] }),
  })

  const filteredItems = search.trim()
    ? allItems.filter((item) => {
        const q = search.toLowerCase()
        return (
          item.text?.toLowerCase().includes(q) ||
          item.title?.toLowerCase().includes(q) ||
          item.meeting_url?.toLowerCase().includes(q)
        )
      })
    : allItems

  const totalCount = (transcriptionsData?.total ?? 0) + (meetingsData?.total ?? 0)

  return (
    <div className="p-8 space-y-6">
      <div className="flex items-start justify-between">
        <div className="space-y-1">
          <h1 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
            {m.app_tool_transcribe_title()}
          </h1>
          <p className="text-sm text-[var(--color-muted-foreground)]">
            {!isLoading && m.app_transcribe_count_total({ count: String(totalCount) })}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            onClick={() => void navigate({ to: '/app/meetings/start' })}
          >
            <Video className="mr-2 h-4 w-4" />
            {m.app_transcribe_new_meeting()}
          </Button>
          <Button
            data-help-id="transcribe-add"
            onClick={() => void navigate({ to: '/app/transcribe/add' })}
          >
            <Mic className="mr-2 h-4 w-4" />
            {m.app_transcribe_new_audio()}
          </Button>
        </div>
      </div>

      {hasActiveMeetings && (
        <div className="flex items-center gap-2 text-sm text-[var(--color-muted-foreground)]">
          <Loader2 className="h-4 w-4 animate-spin" />
          <span>{m.app_transcribe_auto_refresh()}</span>
        </div>
      )}

      {isLoading ? (
        <div className="flex justify-center py-8">
          <Loader2 className="h-5 w-5 animate-spin text-[var(--color-muted-foreground)]" />
        </div>
      ) : queryError ? (
        <QueryErrorState error={queryError instanceof Error ? queryError : new Error(String(queryError))} onRetry={() => { void refetchTranscriptions(); void refetchMeetings() }} />
      ) : (
        <TranscriptionTable
          allItems={allItems}
          filteredItems={filteredItems}
          search={search}
          onSearchChange={(value) => void navigate({ search: { search: value || undefined } })}
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
    </div>
  )
}
