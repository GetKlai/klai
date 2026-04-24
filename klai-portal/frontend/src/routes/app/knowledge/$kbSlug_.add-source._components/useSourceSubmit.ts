/**
 * Shared mutation + error-mapping hook for URL / Text source forms.
 *
 * Keeps each individual form component under 100 lines (SPEC-KB-SOURCES-001
 * R6.3) and guarantees consistent error text across the two tiles. The
 * ``youtube`` kind used to be wired here too, but the tile was pulled from
 * the UI in 1.5.0 — the backend route remains live for when we re-enable.
 */
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { useState } from 'react'
import { ApiError, apiFetch } from '@/lib/apiFetch'
import * as m from '@/paraglide/messages'

export type SourceKind = 'url' | 'text'

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
 * Parse ApiError.detail as JSON and return ``detail.error_code`` when present.
 *
 * Portal-api's structured errors are emitted as
 * ``HTTPException(detail={"error_code": "...", ...})``, which apiFetch
 * stringifies into ``err.detail`` so the JSON roundtrip works here. Plain
 * string details (non-JSON) are ignored and return ``undefined``.
 *
 * Exported purely so the return value can be asserted against without
 * re-implementing the parse logic in tests.
 */
export function extractErrorCode(detail: string): string | undefined {
  if (!detail) return undefined
  try {
    const parsed = JSON.parse(detail) as { error_code?: unknown }
    return typeof parsed.error_code === 'string' ? parsed.error_code : undefined
  } catch {
    return undefined
  }
}

/**
 * Map an ApiError to one of the i18n error keys documented in SPEC D8.
 *
 * Never calls the generic key for errors we can recognise — the UI should
 * tell the user WHICH constraint tripped (invalid URL / blocked URL /
 * KB full) rather than a vague "try again". Exported for direct unit
 * testing.
 */
export function errorMessageFor(_kind: SourceKind, err: unknown): string {
  if (!(err instanceof ApiError)) {
    return m.knowledge_add_source_error_generic()
  }

  // Structured backend error: dict detail with error_code. Always wins over
  // status-based mapping because the code is more specific than the status.
  if (extractErrorCode(err.detail) === 'kb_quota_items_exceeded') {
    return m.knowledge_add_source_error_kb_full()
  }

  const lowerDetail = (err.detail || '').toLowerCase()

  switch (err.status) {
    case 400:
      return lowerDetail.includes('not allowed')
        ? m.knowledge_add_source_error_blocked_url()
        : m.knowledge_add_source_error_invalid_url()
    case 502:
      return m.knowledge_add_source_error_fetch_failed()
    default:
      return m.knowledge_add_source_error_generic()
  }
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
