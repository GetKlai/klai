import { useState } from 'react'
import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { AlertTriangle, Info, KeyRound, Trash2 } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { ListEmptyState, ListLoadingState } from '@/components/ui/list-state'
import { PageContainer } from '@/components/ui/page-container'
import { PageHeader } from '@/components/ui/page-header'
import { QueryErrorState } from '@/components/ui/query-error-state'
import { Tabs, type TabItem } from '@/components/ui/tabs'
import * as m from '@/paraglide/messages'
import { CreatedKeyModal } from '../api-keys/_components/CreatedKeyModal'
import { isSlackLockedError, useDisconnectSlack, useSlackStatus } from './-hooks'
import { SlackConnectCard } from './_components/SlackConnectCard'
import { SlackDisconnectTab } from './_components/SlackDisconnectTab'
import { SlackKbTab } from './_components/SlackKbTab'
import { SlackLogo } from './_components/SlackLogo'
import { SlackOverviewTab } from './_components/SlackOverviewTab'
import { SlackStatusAlert } from './_components/SlackStatusAlert'

type TabId = 'overview' | 'kbs' | 'disconnect'

const VALID_TABS = new Set<TabId>(['overview', 'kbs', 'disconnect'])

type SlackSearch = {
  tab?: TabId
}

export const Route = createFileRoute('/admin/slack/')({
  validateSearch: (search: Record<string, unknown>): SlackSearch => ({
    tab: (VALID_TABS as Set<string>).has(search.tab as string)
      ? (search.tab as TabId)
      : undefined,
  }),
  component: SlackPage,
})

function SlackPage() {
  const search = Route.useSearch()
  const navigate = useNavigate()
  const { data, isLoading, error, refetch } = useSlackStatus()
  const disconnectMutation = useDisconnectSlack()
  // The key lives here, not in the connect card: creating it flips the status
  // to awaiting_link, which unmounts the card while the modal is still open.
  const [createdKey, setCreatedKey] = useState<string | null>(null)

  const title = (name: string) => (
    <span className="inline-flex items-center gap-2">
      <SlackLogo className="h-6 w-6" />
      {name}
    </span>
  )

  if (isLoading) {
    return (
      <PageContainer width="3xl" gap="6">
        <PageHeader title={title(m.admin_section_slack_title())} />
        <ListLoadingState label={m.admin_slack_loading()} />
      </PageContainer>
    )
  }

  if (error) {
    return (
      <PageContainer width="3xl" gap="6">
        <PageHeader title={title(m.admin_section_slack_title())} />
        {isSlackLockedError(error) ? (
          <ListEmptyState icon={AlertTriangle} title={m.admin_slack_unlock_required()} />
        ) : (
          <QueryErrorState
            error={error instanceof Error ? error : new Error(String(error))}
            onRetry={() => void refetch()}
          />
        )}
      </PageContainer>
    )
  }

  if (!data) return null

  const awaiting = data.status === 'awaiting_link'
  const connected = data.status === 'connected'

  const tabs: TabItem<TabId>[] = [
    ...(connected
      ? [{ id: 'overview' as const, label: m.admin_slack_tab_overview(), icon: Info }]
      : []),
    { id: 'kbs', label: m.admin_slack_tab_kbs(), icon: KeyRound },
    {
      id: 'disconnect',
      label: awaiting ? m.admin_slack_tab_remove_key() : m.admin_slack_tab_disconnect(),
      icon: Trash2,
    },
  ]
  const activeTab: TabId =
    search.tab && tabs.some((t) => t.id === search.tab) ? search.tab : tabs[0].id

  function setTab(tab: TabId) {
    void navigate({ to: '/admin/slack', search: { tab } })
  }

  const badge = connected ? (
    <Badge variant="success">{m.admin_slack_status_connected()}</Badge>
  ) : awaiting ? (
    <Badge variant="info">{m.admin_slack_status_awaiting()}</Badge>
  ) : data.status === 'revoked' ? (
    <Badge variant="warning">{m.admin_slack_status_revoked()}</Badge>
  ) : null

  const workspaceName = data.installation?.team_name
  const pageTitle = title(workspaceName ?? m.admin_section_slack_title())

  return (
    <PageContainer width="3xl" gap="6">
      <PageHeader
        title={pageTitle}
        description={m.admin_section_slack_description()}
        actions={badge}
      />

      {data.status === 'not_connected' && (
        <SlackConnectCard onCreated={setCreatedKey} />
      )}

      {data.status === 'revoked' && (
        <>
          <SlackStatusAlert
            status="revoked"
            isReconnecting={disconnectMutation.isPending}
            onReconnect={() => disconnectMutation.mutate()}
          />
          {disconnectMutation.error && (
            <p className="text-sm text-[var(--color-destructive)]">
              {disconnectMutation.error.message}
            </p>
          )}
        </>
      )}

      {(awaiting || connected) && (
        <>
          {awaiting && data.key && (
            <SlackStatusAlert status="awaiting_link" keyPrefix={data.key.key_prefix} />
          )}
          <Tabs tabs={tabs} value={activeTab} onValueChange={setTab} />
          {activeTab === 'overview' && data.installation && data.key && (
            <SlackOverviewTab installation={data.installation} slackKey={data.key} />
          )}
          {activeTab === 'kbs' && (
            <SlackKbTab key={[...data.kb_ids].sort().join(',')} kbIds={data.kb_ids} />
          )}
          {activeTab === 'disconnect' && <SlackDisconnectTab awaitingLink={awaiting} />}
        </>
      )}

      {createdKey && (
        <CreatedKeyModal
          apiKey={createdKey}
          open
          onConfirm={() => setCreatedKey(null)}
          title={m.admin_slack_key_modal_title()}
          warning={m.admin_slack_key_modal_warning()}
          description={m.admin_slack_key_modal_description()}
          confirmLabel={m.admin_slack_key_modal_confirm()}
        />
      )}
    </PageContainer>
  )
}
