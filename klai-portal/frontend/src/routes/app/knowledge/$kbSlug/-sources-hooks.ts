/**
 * SPEC-PORTAL-KENNIS-002 Track 2 mutations + reauth + polling, split out of
 * the Sources tab route file. Each hook here owns one user-visible action,
 * invalidates only the queries documented in `-kb-query-keys.ts`, and emits a
 * `queryLogger.error` with structured context on failure so the live VictoriaLogs
 * stream stays useful.
 */
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { apiFetch } from '@/lib/apiFetch'
import { queryLogger } from '@/lib/logger'
import { kbQueryKeys } from '@/lib/kb-query-keys'
import type { Source } from './-sources-types'

/**
 * Sync a single source.
 *
 * - Connector → POST `/connectors/{id}/sync` (full source-side resync).
 * - Upload    → POST `/uploads/{artifact_id}/reindex` (re-enqueue chunking).
 */
export function useSourceSync(kbSlug: string, source: Source) {
  const queryClient = useQueryClient()
  const endpoint =
    source.kind === 'upload'
      ? `/api/app/knowledge-bases/${kbSlug}/uploads/${source.id}/reindex`
      : `/api/app/knowledge-bases/${kbSlug}/connectors/${source.id}/sync`
  return useMutation({
    mutationFn: async () => apiFetch(endpoint, { method: 'POST' }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: kbQueryKeys.sources(kbSlug) })
      void queryClient.invalidateQueries({ queryKey: kbQueryKeys.statsSummary() })
    },
    onError: (err) =>
      queryLogger.error('Source sync failed', {
        kbSlug,
        sourceId: source.id,
        kind: source.kind,
        err,
      }),
  })
}

/**
 * Delete a single source.
 *
 * Branch by `source.kind` once at construction so callers don't have to
 * dispatch by hand. The mutation result shape is identical for both branches
 * (204 No Content), so the row UI does not care which path ran.
 */
export function useSourceDelete(kbSlug: string, source: Source) {
  const queryClient = useQueryClient()
  const endpoint =
    source.kind === 'upload'
      ? `/api/app/knowledge-bases/${kbSlug}/uploads/${source.id}`
      : `/api/app/knowledge-bases/${kbSlug}/connectors/${source.id}`
  return useMutation({
    mutationFn: async () => apiFetch(endpoint, { method: 'DELETE' }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: kbQueryKeys.sources(kbSlug) })
      void queryClient.invalidateQueries({ queryKey: kbQueryKeys.statsSummary() })
    },
    onError: (err) =>
      queryLogger.error('Source delete failed', {
        kbSlug,
        sourceId: source.id,
        kind: source.kind,
        err,
      }),
  })
}

/**
 * Rename an upload source's display label.
 *
 * Upload-only: connector display names live in `portal_connectors.name`,
 * edited via the dedicated `/edit-connector` route. The mutation does NOT
 * touch `artifacts.path` (Qdrant identity) - only `extra.display_name`.
 *
 * The `onSuccess` callback closes the inline edit overlay; failures keep the
 * overlay open so the user can retry without re-typing. This matches the
 * pre-rename behaviour from PR #574 - closing on error silently dropped
 * the user's typed name with no feedback.
 */
export function useSourceRename(kbSlug: string, source: Source, onDone: () => void) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (name: string) =>
      apiFetch(`/api/app/knowledge-bases/${kbSlug}/uploads/${source.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ name }),
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: kbQueryKeys.sources(kbSlug) })
      onDone()
    },
    onError: (err) =>
      queryLogger.error('Source rename failed', {
        kbSlug,
        sourceId: source.id,
        err,
      }),
  })
}

interface ReauthState {
  pending: boolean
  error: boolean
  /**
   * Trigger the OAuth authorize redirect for this connector.
   *
   * Goes via the existing `/api/oauth/{type}/authorize` endpoint with
   * `kb_slug` + `connector_id` query params, identical to the pattern used
   * by `connectors.tsx::handleReconnect`. No dedicated `/reauth` route.
   *
   * Sets `error=true` and `pending=false` if the connector has no
   * `connector_type` (defensive - should never happen on the auth_error
   * path) or if the authorize call itself fails before the redirect.
   */
  start: () => Promise<void>
}

export function useSourceReauth(kbSlug: string, source: Source): ReauthState {
  const queryClient = useQueryClient()
  const [pending, setPending] = useState(false)
  const [error, setError] = useState(false)

  async function start() {
    if (!source.connector_type) {
      setError(true)
      return
    }
    setError(false)
    setPending(true)
    try {
      const { authorize_url } = await apiFetch<{ authorize_url: string }>(
        `/api/oauth/${encodeURIComponent(source.connector_type)}/authorize` +
          `?kb_slug=${encodeURIComponent(kbSlug)}` +
          `&connector_id=${encodeURIComponent(source.id)}`,
      )
      window.location.assign(authorize_url)
      // Stay pending: page redirects away; spinner stays until navigation.
    } catch (err) {
      setPending(false)
      setError(true)
      queryLogger.error('Connector reauth failed', { kbSlug, sourceId: source.id, err })
      void queryClient.invalidateQueries({ queryKey: kbQueryKeys.sources(kbSlug) })
    }
  }

  return { pending, error, start }
}

/**
 * Fan-out helper: sync every connector source in this KB in parallel. We
 * don't await individual responses - each row will pick up its `pending`
 * status on the next poll of the bronnen list.
 */
export function useSyncAllConnectors(kbSlug: string, connectorSources: Source[]) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async () =>
      Promise.allSettled(
        connectorSources.map((s) =>
          apiFetch(`/api/app/knowledge-bases/${kbSlug}/connectors/${s.id}/sync`, {
            method: 'POST',
          }),
        ),
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: kbQueryKeys.sources(kbSlug) })
      void queryClient.invalidateQueries({ queryKey: kbQueryKeys.statsSummary() })
    },
    onError: (err) => queryLogger.error('Sync-all failed', { kbSlug, err }),
  })
}
