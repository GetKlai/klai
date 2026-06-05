import type { ComponentType } from 'react'
import { AlertTriangle, FileText, Globe, Loader2 } from 'lucide-react'
import { SiGithub, SiGoogledrive, SiNotion } from '@icons-pack/react-simple-icons'
import { Button } from '@/components/ui/button'
import { Tooltip } from '@/components/ui/tooltip'
import { DataTableRow, DataTableCell } from '@/components/ui/data-table'
import { RowActionGroup, BorderedRowActionIconButton } from '@/components/ui/row-action'
import * as m from '@/paraglide/messages'
import { SyncStatusBadge } from './-kb-helpers'
import type { ConnectorSummary } from './-kb-types'

type ConnectorTypeInfo = { label: () => string; IconComponent: ComponentType<{ className?: string }> }

// Paraglide message functions keep labels i18n-driven instead of hard-coded strings.
const CONNECTOR_TYPE_MAP: Record<string, ConnectorTypeInfo> = {
  github:       { label: m.admin_connectors_type_github,       IconComponent: SiGithub },
  web_crawler:  { label: m.admin_connectors_type_website,      IconComponent: Globe },
  notion:       { label: m.admin_connectors_type_notion,       IconComponent: SiNotion },
  google_drive: { label: m.admin_connectors_type_google_drive, IconComponent: SiGoogledrive },
  ms_docs:      { label: m.admin_connectors_type_ms_docs,      IconComponent: FileText },
}

/** OAuth-backed connector types that support the /api/oauth/{provider}/authorize reconnect flow.
 *  google_docs/sheets/slides are Google Drive aliases - the backend normalises
 *  them to the google_drive provider, so reconnect works for them too. */
const OAUTH_RECONNECTABLE = new Set<string>([
  'google_drive', 'google_docs', 'google_sheets', 'google_slides', 'ms_docs',
])

export interface ConnectorLiveProgress {
  pagesDone: number | null
  pagesTotal: number | null
  liveResolutionFailed: boolean
}

interface ConnectorRowProps {
  connector: ConnectorSummary
  isOwner: boolean
  isSyncing: boolean
  liveProgress?: ConnectorLiveProgress
  syncError?: string
  reconnecting: boolean
  reconnectFailed: boolean
  onSync: (connectorId: string) => void
  onReconnect: (connectorType: string, connectorId: string) => void
  onEdit: (connectorId: string) => void
  onDelete: (connectorId: string) => void
  onInvestigate: (connector: ConnectorSummary) => void
}

export function ConnectorRow({
  connector,
  isOwner,
  isSyncing,
  liveProgress,
  syncError,
  reconnecting,
  reconnectFailed,
  onSync,
  onReconnect,
  onEdit,
  onDelete,
  onInvestigate,
}: ConnectorRowProps) {
  const info = CONNECTOR_TYPE_MAP[connector.connector_type]
  const Icon = info?.IconComponent ?? FileText
  const typeLabel = info?.label() ?? connector.connector_type
  const isRunning = connector.last_sync_status?.toUpperCase() === 'RUNNING'

  return (
    <DataTableRow className="group klai-hover">
      <DataTableCell className="w-6 px-0 pr-2 align-top">
        <Tooltip className="leading-none mt-px" label={typeLabel}>
          <Icon className="h-4 w-4 text-gray-400" />
        </Tooltip>
      </DataTableCell>
      <DataTableCell className="align-top">
        <span className="font-medium text-gray-900">{connector.name}</span>
      </DataTableCell>
      <DataTableCell className="w-28 align-top">
        <span className="text-xs text-gray-400">{typeLabel}</span>
      </DataTableCell>
      <DataTableCell className="w-32 align-top">
        <SyncStatusBadge
          status={connector.last_sync_status}
          lastSyncAt={connector.last_sync_at}
          pagesDone={liveProgress?.pagesDone}
          pagesTotal={liveProgress?.pagesTotal}
          liveResolutionFailed={liveProgress?.liveResolutionFailed ?? false}
        />
        {isOwner
          && connector.last_sync_status?.toUpperCase() === 'AUTH_ERROR'
          && OAUTH_RECONNECTABLE.has(connector.connector_type) && (
          <div className="mt-1.5 space-y-1">
            <Button
              size="sm"
              variant="outline"
              disabled={reconnecting}
              onClick={() => onReconnect(connector.connector_type, connector.id)}
              className="h-7 text-xs"
            >
              {reconnecting ? <Loader2 className="h-3 w-3 animate-spin" /> : null}
              {m.admin_connectors_reconnect_action()}
            </Button>
            {reconnectFailed && (
              <p className="text-xs text-[var(--color-destructive)]">
                {m.admin_connectors_reconnect_error()}
              </p>
            )}
          </div>
        )}
        {connector.last_sync_documents_ok != null && connector.last_sync_documents_ok > 0 && (
          <p className="mt-0.5 text-xs text-gray-400 tabular-nums">
            {connector.last_sync_documents_ok.toLocaleString()} {m.connectors_documents_indexed()}
          </p>
        )}
        {syncError && (
          <p className="mt-1 max-w-44 truncate text-xs text-[var(--color-destructive)]" title={syncError}>
            {syncError}
          </p>
        )}
        {/* SPEC-CONNECTOR-INPUT-VALIDATION-001 REQ-5/REQ-7 - actionable error badge. */}
        {connector.needs_reconfiguration && (
          <button
            type="button"
            onClick={() => onInvestigate(connector)}
            className="mt-1.5 inline-flex items-center gap-1 rounded-md border border-[var(--color-destructive)]/30 bg-[var(--color-destructive)]/5 px-2 py-1 text-xs font-medium text-[var(--color-destructive)] hover:bg-[var(--color-destructive)]/10 transition-colors"
          >
            <AlertTriangle className="h-3 w-3" />
            Needs reconfiguration
          </button>
        )}
      </DataTableCell>
      {isOwner && (
        <DataTableCell align="right" className="w-28 align-top">
          <RowActionGroup>
            <BorderedRowActionIconButton
              action="sync"
              label={isSyncing || isRunning ? m.admin_connectors_syncing() : m.admin_connectors_action_sync()}
              disabled={isSyncing || isRunning}
              spinner={isSyncing || isRunning ? <Loader2 className="animate-spin" /> : undefined}
              onClick={() => onSync(connector.id)}
            />
            <BorderedRowActionIconButton
              action="edit"
              label={m.admin_connectors_action_edit()}
              onClick={() => onEdit(connector.id)}
            />
            <BorderedRowActionIconButton
              action="delete"
              label={m.admin_connectors_action_delete()}
              onClick={() => onDelete(connector.id)}
            />
          </RowActionGroup>
        </DataTableCell>
      )}
    </DataTableRow>
  )
}
