import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { apiFetch } from '@/lib/apiFetch'
import { SCRIBE_BASE } from './-add-helpers'

export interface TranscriptionResponse {
  id: string
  name: string | null
  status: string
  text: string | null
  language: string | null
  duration_seconds: number | null
  inference_time_seconds: number | null
  summary_json: Record<string, unknown> | null
  created_at: string
}

interface MutationOptions {
  language: string
  onQueued: (data: TranscriptionResponse) => void
  onError: (message: string) => void
  onSuccess?: () => void
}

function useTranscriptionSuccessHandler(onQueued: (data: TranscriptionResponse) => void) {
  const queryClient = useQueryClient()
  const navigate = useNavigate({ from: '/app/transcribe/add' })

  return (data: TranscriptionResponse) => {
    void queryClient.invalidateQueries({ queryKey: ['transcriptions'] })
    if (data.status === 'transcribed') {
      void navigate({ to: '/app/transcribe/$transcriptionId', params: { transcriptionId: data.id } })
      return
    }
    onQueued(data)
  }
}

export function useTranscribeFileMutation({ language, onQueued, onError, onSuccess }: MutationOptions) {
  const handleSuccess = useTranscriptionSuccessHandler(onQueued)

  return useMutation({
    mutationFn: async (file: File) => {
      const form = new FormData()
      form.append('file', file)
      if (language) form.append('language', language)
      return apiFetch<TranscriptionResponse>(`${SCRIBE_BASE}/transcribe`, {
        method: 'POST',
        body: form,
      })
    },
    onSuccess: (data) => {
      handleSuccess(data)
      onSuccess?.()
    },
    onError: (err: Error) => {
      onError(err.message)
    },
  })
}

export function useRetryTranscriptionMutation({ language, onQueued, onError }: MutationOptions) {
  const handleSuccess = useTranscriptionSuccessHandler(onQueued)

  return useMutation({
    mutationFn: async (txnId: string) => {
      const params = language ? `?language=${encodeURIComponent(language)}` : ''
      return apiFetch<TranscriptionResponse>(`${SCRIBE_BASE}/transcriptions/${txnId}/retry${params}`, {
        method: 'POST',
      })
    },
    onSuccess: handleSuccess,
    onError: (err: Error) => {
      onError(err.message)
    },
  })
}
