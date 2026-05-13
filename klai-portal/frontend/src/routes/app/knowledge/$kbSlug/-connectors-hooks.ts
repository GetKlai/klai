import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { apiFetch } from '@/lib/apiFetch'
import { kbQueryKeys } from '@/lib/kb-query-keys'
import { queryLogger } from '@/lib/logger'
import type { ConnectorSummary } from './-kb-types'

export function useConnectorDelete(kbSlug: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (id: string) => {
      await apiFetch(`/api/app/knowledge-bases/${kbSlug}/connectors/${id}`, { method: 'DELETE' })
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: kbQueryKeys.connectorsPortal(kbSlug) }),
    onError: (err, connectorId) => queryLogger.error('Connector delete failed', { kbSlug, connectorId, err }),
  })
}

export function useConnectorSync(kbSlug: string) {
  const queryClient = useQueryClient()
  const [syncingIds, setSyncingIds] = useState<Set<string>>(new Set())

  async function sync(connectorId: string) {
    setSyncingIds((prev) => new Set([...prev, connectorId]))
    try {
      await apiFetch(`/api/app/knowledge-bases/${kbSlug}/connectors/${connectorId}/sync`, { method: 'POST' })
      queryClient.setQueryData(kbQueryKeys.connectorsPortal(kbSlug), (old: ConnectorSummary[] | undefined) =>
        old?.map((connector) => connector.id === connectorId ? { ...connector, last_sync_status: 'running' } : connector)
      )
      void queryClient.invalidateQueries({ queryKey: kbQueryKeys.connectorsPortal(kbSlug) })
    } catch (err) {
      queryLogger.error('Connector sync failed', { kbSlug, connectorId, err })
      void queryClient.invalidateQueries({ queryKey: kbQueryKeys.connectorsPortal(kbSlug) })
    } finally {
      setSyncingIds((prev) => {
        const next = new Set(prev)
        next.delete(connectorId)
        return next
      })
    }
  }

  return { syncingIds, sync }
}

export function useConnectorReconnect(kbSlug: string) {
  const queryClient = useQueryClient()
  const [reconnectingId, setReconnectingId] = useState<string | null>(null)
  const [reconnectErrorId, setReconnectErrorId] = useState<string | null>(null)

  async function reconnect(connectorType: string, connectorId: string) {
    setReconnectErrorId(null)
    setReconnectingId(connectorId)
    try {
      const { authorize_url } = await apiFetch<{ authorize_url: string }>(
        `/api/oauth/${encodeURIComponent(connectorType)}/authorize?kb_slug=${encodeURIComponent(kbSlug)}&connector_id=${encodeURIComponent(connectorId)}`,
      )
      window.location.assign(authorize_url)
      // Stay pending: the redirect unmounts this tree, so the spinner remains visible until navigation.
    } catch (err) {
      setReconnectingId(null)
      setReconnectErrorId(connectorId)
      queryLogger.error('Connector reconnect failed', { kbSlug, connectorId, connectorType, err })
      void queryClient.invalidateQueries({ queryKey: kbQueryKeys.connectorsPortal(kbSlug) })
    }
  }

  return { reconnectingId, reconnectErrorId, reconnect }
}
