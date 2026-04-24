/**
 * Shared mutation + error-mapping hook for URL / YouTube / Text source forms.
 *
 * Keeps each individual form component under 100 lines (SPEC-KB-SOURCES-001
 * R6.3) and guarantees consistent error text across the three tiles.
 */
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { useState } from 'react'
import { ApiError, apiFetch } from '@/lib/apiFetch'
import * as m from '@/paraglide/messages'

export type SourceKind = 'url' | 'youtube' | 'text'

export interface SourceIngestedResponse {
  artifact_id: string
  source_ref: string
  source_type: SourceKind
}

interface UseSourceSubmitOptions {
  kbSlug: string
  kind: SourceKind
}

/**
 * Map an ApiError to one of the i18n error keys documented in SPEC D8.
 *
 * Never calls the generic key for errors we can recognise — the UI should
 * tell the user WHICH constraint tripped (invalid URL / blocked URL / no
 * transcript / KB full) rather than a vague "try again".
 */
function errorMessageFor(kind: SourceKind, err: unknown): string {
  if (err instanceof ApiError) {
    const detail = (err.detail || '').toLowerCase()
    const errorCode = (() => {
      try {
        const parsed = JSON.parse(err.detail || '{}') as {
          error_code?: string
        }
        return parsed.error_code
      } catch {
        return undefined
      }
    })()

    if (errorCode === 'kb_quota_items_exceeded') {
      return m.knowledge_add_source_error_kb_full()
    }
    if (err.status === 400) {
      if (detail.includes('not allowed')) {
        return m.knowledge_add_source_error_blocked_url()
      }
      if (kind === 'youtube') {
        return m.knowledge_add_source_error_invalid_url()
      }
      return m.knowledge_add_source_error_invalid_url()
    }
    if (err.status === 422 && kind === 'youtube') {
      return m.knowledge_add_source_error_no_transcript()
    }
    if (err.status === 502) {
      return m.knowledge_add_source_error_fetch_failed()
    }
  }
  return m.knowledge_add_source_error_generic()
}

export function useSourceSubmit<TBody>({
  kbSlug,
  kind,
}: UseSourceSubmitOptions) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [successful, setSuccessful] = useState(false)

  const mutation = useMutation<SourceIngestedResponse, unknown, TBody>({
    mutationFn: async (body: TBody) =>
      apiFetch<SourceIngestedResponse>(
        `/api/app/knowledge-bases/${kbSlug}/sources/${kind}`,
        {
          method: 'POST',
          body: JSON.stringify(body),
        }
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['kb-items', kbSlug] })
      void queryClient.invalidateQueries({ queryKey: ['personal-knowledge', kbSlug] })
      void queryClient.invalidateQueries({
        queryKey: ['app-knowledge-bases-stats-summary'],
      })
      setSuccessful(true)
      setTimeout(() => {
        void navigate({
          to: '/app/knowledge/$kbSlug/overview',
          params: { kbSlug },
        })
      }, 1200)
    },
  })

  const errorMessage = mutation.error
    ? errorMessageFor(kind, mutation.error)
    : null

  return { mutation, errorMessage, successful }
}
